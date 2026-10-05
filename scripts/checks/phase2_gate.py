"""Isolated, operator-run Phase 2 checkpoint."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from uuid import uuid4

from fleet.container import configured_container
from fleet import transport
from fleet.errors import FleetError


def agent_pid(home: Path, job: str, run: str) -> int | None:
    if Path(job).name != job or job in (".", ".."):
        raise ValueError("invalid job identity")
    path = home / "jobs" / job / "job.json"
    if not path.resolve().is_relative_to(home.resolve()):
        raise ValueError("job is outside isolated FLEET_HOME")
    record = json.loads(path.read_text())
    if record["id"] != job or record["run_id"] != run:
        raise ValueError("job does not belong to this run")
    pid = record["agent_pid"]
    if pid is not None and (type(pid) is not int or pid <= 1):
        raise ValueError("invalid agent pid")
    return pid


class Environment:
    def __init__(self, names: list[str], root: Path):
        self.control = str(root / "ssh-%C")
        self.hosts = sorted([replace(transport.host_by_name(name), control_path=self.control)
                             for name in names], key=lambda host: not host.is_local)
        self.root = root
        self.suffix = "m5-" + uuid4().hex[:12]
        self.homes = {}
        self.copies = {}
        self.masters = {}
        self.offline = False
        self.original_env = dict(os.environ)
        os.environ.update(FLEET_FLEETD_PATH=f"~/.local/share/fleet-{self.suffix}/fleetd.py",
                          FLEET_REMOTE_HOME=f"~/.fleet-{self.suffix}",
                          FLEET_STORE=str(root / "store.db"), FLEET_CONFIG=str(root / "config.json"),
                          FLEET_HOME=str(root / "controller"))

    def ensure_master(self, host):
        if host.is_local:
            return
        if self.offline:
            raise FleetError("dedicated SSH connection is disconnected")
        if host.name in self.masters and self.masters[host.name].poll() is None:
            return
        process = subprocess.Popen(["ssh", "-o", "ControlPersist=no", *host.ssh_options, "-M", "-N", host.ssh_target],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.masters[host.name] = process
        for _ in range(100):
            result = subprocess.run(["ssh", "-S", self.control, "-O", "check", host.ssh_target],
                                    capture_output=True, timeout=10)
            if result.returncode == 0:
                return
            if process.poll() is not None:
                raise FleetError(f"cannot open dedicated SSH connection to {host.name}")
            time.sleep(0.05)
        raise FleetError("dedicated SSH connection did not become ready")

    def call(self, host, arguments, *, stdin_text=None):
        self.ensure_master(host)
        return transport.call(host, arguments, stdin_text=stdin_text)

    def python(self, host, source, *args, stdin=None):
        self.ensure_master(host)
        command = shlex.join([host.python, "-c", source, *args])
        result = subprocess.run(host.shell_command(command), input=stdin, text=True,
                                capture_output=True, timeout=20)
        if result.returncode:
            raise FleetError(result.stderr)
        return result.stdout.strip()

    def setup(self):
        for host in self.hosts:
            home = self.python(host, "from pathlib import Path; print(Path.home())")
            self.homes[host.name] = str(Path(home) / f".fleet-{self.suffix}")
            self.copies[host.name] = str(Path(home) / f".local/share/fleet-{self.suffix}")
            print(f"{host.name}: FLEET_HOME={self.homes[host.name]} fleetd={self.copies[host.name]}/fleetd.py", flush=True)
            self.python(host, """
from pathlib import Path
import sys
home, copy = map(Path, sys.argv[1:])
home.mkdir(mode=0o700)
copy.mkdir(parents=True)
(copy / 'fleetd.py').write_text(sys.stdin.read())
""", self.homes[host.name], self.copies[host.name], stdin=transport.LOCAL_FLEETD_SOURCE.read_text())
            assert self.call(host, ["ls", "--all"])["jobs"] == []
        (self.root / "config.json").write_text(json.dumps({"hosts": {
            h.name: {"ssh": h.ssh_target, "python": h.python} for h in self.hosts}}))

    def controller(self):
        store = configured_container().store()
        project = configured_container(store).initialized_workspace().move_in([h.name for h in self.hosts], 'Phase 2 gate').project_id
        work = configured_container(store).work()
        item = work.add(project=project, title="Isolated Phase 2", goal="Verify dispatch", kind="epic", actor="user")
        return store, work, item

    def disconnect(self, host):
        subprocess.run(["ssh", "-S", self.control, "-O", "exit", host.ssh_target],
                       capture_output=True, check=True, timeout=10)
        self.masters.pop(host.name).wait(timeout=10)
        self.offline = True

    def cleanup(self):
        self.offline = False
        errors = []
        for host in self.hosts:
            if host.name not in self.copies:
                continue
            try:
                jobs = self.call(host, ["ls", "--all"])["jobs"]
                for job in jobs:
                    self.call(host, ["cancel", job["id"], "--all-steps"])
                self.python(host, """
from pathlib import Path
import shutil, sys
for value in sys.argv[1:]:
    path = Path(value)
    if path.exists():
        shutil.rmtree(path)
""", self.homes[host.name], self.copies[host.name])
            except Exception as error:
                errors.append(f"{host.name}: {error}")
        for process in self.masters.values():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        os.environ.clear()
        os.environ.update(self.original_env)
        if errors:
            raise RuntimeError("Cleanup incomplete; use --help: " + "; ".join(errors))


def existing_checks(environment):
    hosts = environment.hosts
    store, work, item = environment.controller()
    item_id = item.id
    before = (work.get(item_id), work.criteria(item_id), work.progress(item_id))
    execution = configured_container(store).execution()
    key = "phase2-" + str(uuid4())

    def payload(cwd):
        return {"cwd": cwd, "arguments": ["create", "--project", item.project,
            "--description", key, "--agent", "codex", "--cwd", cwd,
            "--permission", "read-only", "--steps-file", "/dev/stdin", "--hold"],
            "steps": ["Reply with FLEET_STATUS: done. Do not use tools."], "context": [], "hold": True}

    print("1. Two controller processes contend for one action; expect identical run IDs and four history rows.", flush=True)
    program = '''
    import json, sys
    from fleet.container import configured_container
    execution = configured_container().execution()
    print("ready", flush=True)
    sys.stdin.readline()
    for _ in range(24):
        result = execution.dispatch(sys.argv[1], host=sys.argv[4], runtime="codex",
            payload=json.loads(sys.argv[2]), actor="user", reason="phase2 check", idempotency_key=sys.argv[3])
    print(result.run.id, flush=True)
    '''
    sequence = store.latest_sequence()
    processes = [subprocess.Popen([sys.executable, "-c", textwrap.dedent(program), item_id, json.dumps(payload(environment.homes[hosts[0].name])), key, hosts[0].name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for process in processes:
            assert select.select([process.stdout], [], [], 10)[0], "claimant did not become ready"
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        ids = []
        for process in processes:
            output, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
            ids.append(output.strip())
        assert ids[0] == ids[1]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    run = execution.get_run(ids[0])
    subjects = {f"execution:{kind}:{identity}" for kind, identity in
                (("action", run.action), ("run", run.id), ("claim", run.id), ("request", key))}
    assert len([row for row in store.history_after(sequence) if row["subject"] in subjects]) == 4

    print("2. Dispatch another action to the second host; expect distinct actions and two held jobs.", flush=True)
    other = execution.dispatch(item_id, host=hosts[1].name, runtime="codex", payload=payload(environment.homes[hosts[1].name]),
        actor="user", reason="phase2 check", idempotency_key=key + "-home").run
    assert other.action != run.action
    execution.deliver(run, lambda args, stdin: environment.call(hosts[0], args, stdin_text=stdin), lambda *args: None)

    print("3. Discard the second host's create reply; expect the same run without another create.", flush=True)
    calls = []
    def dropped(args, stdin):
        calls.append(args[0])
        reply = environment.call(hosts[1], args, stdin_text=stdin)
        if args[0] == "create":
            raise FleetError("injected dropped SSH reply after remote create")
        return reply
    job = execution.deliver(other, dropped, lambda *args: None)
    assert calls == ["create", "reconcile"] and job["run_id"] == other.id
    for host, intended in zip(hosts, (run, other)):
        jobs = environment.call(host, ["ls", "--all"])["jobs"]
        matches = [job for job in jobs if job.get("run_id") == intended.id]
        assert len(matches) == 1 and matches[0]["id"] == intended.remote_job_id
        print(f"  {host.name}:{intended.remote_job_id} run={intended.id}")

    print("4. Inject a disconnect during reconciliation; expect unknown outcome, active claims, unchanged work.", flush=True)
    def disconnected(args, stdin):
        raise FleetError("injected SSH disconnect")
    try:
        execution.deliver(other, disconnected, lambda *args: None, reconcile=True)
    except FleetError:
        execution.unavailable(hosts[1].name)
    else:
        raise AssertionError("disconnect was not surfaced")
    sequence = store.latest_sequence()
    execution.unavailable(hosts[1].name)
    assert store.latest_sequence() == sequence
    for intended in (run, other):
        assert execution.get_run(intended.id).status == "unknown outcome"
        claim, = [claim for claim in execution.claims() if claim.run == intended.id]
        assert claim.active
    assert (work.get(item_id), work.criteria(item_id), work.progress(item_id)) == before
    return store, work, item, execution, payload


def real_disconnect(environment, store, work, item, execution, payload):
    host = environment.hosts[1]
    before = (work.get(item.id), work.criteria(item.id), work.progress(item.id))
    run = execution.dispatch(item.id, host=host.name, runtime="codex",
        payload=payload(environment.homes[host.name]), actor="user", reason="real SSH disconnect",
        idempotency_key="real-disconnect").run

    def dropped(args, stdin):
        environment.ensure_master(host)
        if args[0] != "create":
            return environment.call(host, args, stdin_text=stdin)
        command = host.fleetd_command(args)
        reply = str(Path(environment.homes[host.name]) / "withheld-reply.json")
        command[-1] += f" > {shlex.quote(reply)} && echo created && exec sleep 30"
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write(stdin)
            process.stdin.close()
            process.stdin = None
            assert select.select([process.stdout], [], [], 20)[0], "remote create did not finish"
            assert process.stdout.readline().strip() == "created", "remote create failed"
            environment.disconnect(host)
            process.communicate(timeout=10)
            assert process.returncode != 0, "SSH dispatch did not lose its connection"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        raise FleetError("real SSH disconnect during dispatch reply")

    try:
        execution.deliver(run, dropped, lambda *args: None)
    except FleetError as error:
        assert "real SSH disconnect" in str(error)
        execution.unavailable(host.name)
    else:
        raise AssertionError("real disconnect was not surfaced")
    assert execution.get_run(run.id).status == "unknown outcome"
    assert next(c for c in execution.claims() if c.run == run.id).active
    sequence = store.latest_sequence()
    execution.unavailable(host.name)
    assert store.latest_sequence() == sequence
    assert (work.get(item.id), work.criteria(item.id), work.progress(item.id)) == before
    environment.offline = False
    job = execution.deliver(run, lambda args, stdin: environment.call(host, args, stdin_text=stdin),
                            lambda *args: None, reconcile=True)
    jobs = environment.call(host, ["ls", "--all"])["jobs"]
    assert job["id"] == run.remote_job_id
    assert len([j for j in jobs if j.get("run_id") == run.id]) == 1
    print("5. Real SSH disconnect: unknown outcome, claim retained, unchanged work, one job after reconnect.")


def killed_agent(environment, store, work, item, execution, payload):
    import shutil

    host = environment.hosts[0]
    home = Path(environment.homes[host.name])
    codex_home = home / "codex"
    codex_home.mkdir(mode=0o700)
    auth = Path(environment.original_env.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    if auth.exists():
        shutil.copyfile(auth, codex_home / "auth.json")
    request = payload(str(home))
    request["hold"] = False
    request["arguments"].remove("--hold")
    request["arguments"] += ["--env", f"CODEX_HOME={codex_home}", "--env", f"HOME={home}"]
    request["steps"] = ["Think carefully about a plan for a large database migration. Do not use tools or write files."]
    run = execution.dispatch(item.id, host=host.name, runtime="codex", payload=request,
        actor="user", reason="real killed agent", idempotency_key="killed-agent").run
    execution.deliver(run, lambda args, stdin: environment.call(host, args, stdin_text=stdin), lambda *args: None)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            pid = agent_pid(home, run.remote_job_id, run.id)
            if pid is not None:
                os.kill(pid, signal.SIGKILL)
                break
            time.sleep(0.01)
        else:
            raise AssertionError("agent pid was not recorded")
        deadline = time.monotonic() + 20
        while json.loads((home / "jobs" / run.remote_job_id / "job.json").read_text())["runner_pid"] is not None:
            assert time.monotonic() < deadline, "runner did not finish after agent was killed"
            time.sleep(0.01)
        execution.deliver(run, lambda args, stdin: environment.call(host, args, stdin_text=stdin),
                          lambda *args: None, reconcile=True)
        ended = execution.get_run(run.id)
        assert (ended.status, ended.reason) == ("failed", "lost")
        assert not next(c for c in execution.claims() if c.run == run.id).active
        assert work.get(item.id).condition != "complete"
        sequence = store.latest_sequence()
        execution.deliver(run, lambda args, stdin: environment.call(host, args, stdin_text=stdin),
                          lambda *args: None, reconcile=True)
        assert store.latest_sequence() == sequence
    finally:
        environment.call(host, ["cancel", run.remote_job_id, "--all-steps"])
    print("6. Started job on isolated tmux: recorded PID killed, failed/lost after reconciliation, claim released.")


def main():
    parser = argparse.ArgumentParser(description="""Run Phase 2 on side-by-side fleetd copies on carbon and home.
Each host gets unique ~/.local/share/fleet-m5-ID/fleetd.py and ~/.fleet-m5-ID
(FLEET_HOME). The controller uses a temporary store/config and creates its own
project and epic. Four held-job checks plus a real dedicated SSH ControlMaster
disconnect and a started job with a real killed Codex agent verify dispatch recovery.
The side-by-side worker starts its runner on its own tmux socket. Codex runs
locally in the copy's directory with isolated HOME/CODEX_HOME; existing auth is
copied if present. Requires python3, tmux, SSH access and local codex on PATH.
No live Fleet installation, store, config, tmux server or SSH socket is changed.
Jobs are cancelled and both host directories removed on exit, including SIGTERM.
If interrupted by SIGKILL or a host outage, use the printed paths on EACH host:
  FLEET_HOME=<printed-home> python3 <printed-fleetd> ls --all
  FLEET_HOME=<printed-home> python3 <printed-fleetd> cancel <job-id> --all-steps
Wait for recorded runner/agent PIDs to exit, then remove only those two printed
directories. Close only the printed socket with ssh -S <socket> -O exit <host>,
and remove the printed controller temporary directory. Never use a PID pattern.
""", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hosts", nargs="+", default=["carbon", "home"],
                        help="host names from Fleet config; configured SSH targets determine locality")
    parser.add_argument("--setup-only", action="store_true", help="verify setup and teardown without dispatch or agents")
    args = parser.parse_args()
    if len(set(args.hosts)) != len(args.hosts):
        parser.error("host names must be unique")
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryDirectory(prefix="fleet-m5-") as directory:
        environment = Environment(args.hosts, Path(directory))
        print(f"Controller: {directory}; dedicated SSH ControlPath: {environment.control}", flush=True)
        try:
            if not args.setup_only and (len(environment.hosts) != 2 or not environment.hosts[0].is_local
                                       or environment.hosts[1].is_local):
                parser.error("full check requires one local and one SSH host; run on carbon or home")
            environment.setup()
            if args.setup_only:
                environment.controller()
            else:
                state = existing_checks(environment)
                real_disconnect(environment, *state)
                killed_agent(environment, *state)
        except BaseException:
            try:
                environment.cleanup()
            except Exception as error:
                print(str(error), file=sys.stderr)
            raise
        else:
            environment.cleanup()
    print("PASS: setup and teardown verified." if args.setup_only else
          "PASS: all six Phase 2 checks verified; isolated jobs cancelled and copies removed.")


if __name__ == "__main__":
    main()
