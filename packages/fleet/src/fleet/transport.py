"""Talking to fleetd on each host: over ssh, or directly for the local machine."""
from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path
from typing import Any

from fleet_worker import WIRE_PROTOCOL_VERSION

from fleet.errors import FleetError
from fleet.host_values import REMOTE_FLEETD_PATH, SSH_OPTIONS, Host, HostReport

LOCAL_FLEETD_SOURCE = files("fleet_worker").joinpath("fleetd.py")


def config_path() -> Path:
    if "FLEET_CONFIG" in os.environ:
        return Path(os.environ["FLEET_CONFIG"])
    return Path.home() / ".config" / "fleet" / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if path.exists():
        return json.loads(path.read_text())
    return {"hosts": {}}


def save_config(config: dict[str, Any]) -> None:
    import tempfile
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {key: value for key, value in config.items() if key not in ("projects", "capacity")}
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary.write(json.dumps(settings, indent=2) + "\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)


def configured_hosts() -> list[Host]:
    return [Host(name, entry.get("ssh"), entry.get("python", "python3"))
            for name, entry in load_config().get("hosts", {}).items()]


def host_by_name(name: str) -> Host:
    for host in configured_hosts():
        if host.name == name:
            return host
    raise FleetError(f"unknown host '{name}' — add it with: fleet host add {name} --ssh <target>")


def ensure_master(host: Host) -> None:
    """Start the shared ssh connection detached from our pipes.

    A master spawned implicitly by ControlMaster=auto inherits the caller's
    stdout/stderr and keeps them open, so capture_output would block until
    ControlPersist expires.
    """
    if host.is_local:
        return
    control = ["-o", next(option for option in host.ssh_options if option.startswith("ControlPath="))]
    try:
        check = subprocess.run(["ssh", *control, "-O", "check", host.ssh_target], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired as error:
        raise FleetError(f"{host.name}: SSH control connection check timed out after 10s") from error
    if check.returncode != 0:
        try:
            subprocess.run(["ssh", *host.ssh_options, "-o", "ControlMaster=yes", "-M", "-N", "-f", host.ssh_target],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except subprocess.TimeoutExpired as error:
            raise FleetError(f"{host.name}: SSH connection setup timed out after 15s") from error


def _call(host: Host, arguments: list[str], *, stdin_text: str | None = None, timeout: float | None = 30) -> Any:
    """Run a fleetd command and return its parsed JSON output."""
    ensure_master(host)
    try:
        completed = subprocess.run(host.fleetd_command(arguments), input=stdin_text, capture_output=True,
                                   text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise FleetError(f"{host.name}: timed out after {timeout}s") from error
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        detail = completed.stderr.strip().splitlines()[-1:] or [f"exit {completed.returncode}"]
        raise FleetError(f"{host.name}: {detail[0]}")
    document = json.loads(lines[-1])
    if isinstance(document, dict) and "error" in document:
        raise FleetError(f"{host.name}: {document['error']}")
    return document


class ProtocolMismatch(FleetError):
    """A worker requires an explicit installation before it can be used."""


def check_protocol(host: Host, document: Any) -> None:
    version = document.get("wire_protocol_version") if isinstance(document, dict) else None
    if type(version) is not int or version != WIRE_PROTOCOL_VERSION:
        raise ProtocolMismatch(f"{host.name}: wire protocol mismatch: controller {WIRE_PROTOCOL_VERSION}, "
                               f"worker {version if version is not None else 'unreported'}; "
                               f"run fleet install {host.name}")


def worker_version(host: Host) -> dict[str, Any]:
    """Checked worker metadata for installation/deployment version reporting."""
    try:
        document = _call(host, ["version"])
    except (FleetError, ValueError) as error:
        raise ProtocolMismatch(f"{host.name}: wire protocol mismatch: controller {WIRE_PROTOCOL_VERSION}, "
                               f"worker unreported ({error}); run fleet install {host.name}") from error
    check_protocol(host, document)
    return document


def call(host: Host, arguments: list[str], *, stdin_text: str | None = None,
         timeout: float | None = 30) -> Any:
    version = worker_version(host)
    if arguments == ["version"]:
        return version
    return _call(host, arguments, stdin_text=stdin_text, timeout=timeout)


def gather(hosts: list[Host], arguments: list[str]) -> list[HostReport]:
    """Run `fleetd ls …` on every host in parallel; unreachable hosts become error reports."""
    def one(host: Host) -> HostReport:
        try:
            return HostReport(host, call(host, arguments, timeout=20)["jobs"])
        except (FleetError, ValueError) as error:
            return HostReport(host, [], str(error))

    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(one, hosts))


def catch_up_jobs(host: Host) -> list[dict]:
    """All jobs still held by a worker, including finishes missed by the stream."""
    return call(host, ["ls", "--all", "--events", "0"], timeout=30)["jobs"]


def job_steps(host: Host, job_id: str) -> list[dict]:
    """A job's steps as the worker holds them, prompts included."""
    return call(host, ["show", job_id, "--events", "0"], timeout=30)["steps"]


def recent_events(host: Host, job_id: str, count: int) -> list[dict]:
    """A job's latest events; the catch-up listing carries none, to stay small across every retained job."""
    return call(host, ["show", job_id, "--events", str(count)], timeout=30)["events"]


def catch_up_sessions(host: Host, since: str) -> list[dict]:
    return call(host, ["sessions", "--since", since], timeout=30)["sessions"]


def keep_run_trace(execution, host: Host, job: dict) -> None:
    """Copy terminal normalized events once; retain an explicit error and retry on the next report."""
    run = execution.record_observed(host.name, job)
    if job.get("trace") is None:
        return
    source = {**job["trace"], "observed_at": job.get("updated_at")}
    previous = run.trace or {}
    if previous.get("events", {}).get("availability") == "kept" and previous.get("source") == source:
        return
    if job["status"] not in ("done", "failed", "cancelled", "lost", "blocked"):
        execution.record_trace(run.id, source)
        return
    try:
        result = call(host, ["read-trace", job["id"]], timeout=30)
        execution.record_trace(run.id, source, content=result["content"], error=result.get("reason"))
    except (FleetError, OSError, ValueError, KeyError) as error:
        execution.record_trace(run.id, source, error=f"trace copy failed: {error}")


def gather_sessions(hosts: list[Host]) -> dict[str, list[dict[str, Any]]]:
    """Live interactive CLI sessions per host name, from `fleetd sessions`.

    Unreachable hosts contribute no sessions; incompatible workers fail explicitly.
    """
    def one(host: Host) -> list[dict[str, Any]]:
        try:
            return call(host, ["sessions"], timeout=20).get("sessions", [])
        except ProtocolMismatch:
            raise
        except (FleetError, ValueError, AttributeError):
            return []

    if not hosts:
        return {}
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return dict(zip((host.name for host in hosts), pool.map(one, hosts)))


def repository_remotes(host: Host, directories: list[str]) -> dict[str, list[str]]:
    """Git remote URLs of each directory on the host; a directory outside git has none."""
    script = "".join(f"git -C {shlex.quote(directory)} config --get-regexp '^remote\\..*\\.url$' 2>/dev/null"
                     f" | while read -r _ url; do printf '%s\\t%s\\n' {shlex.quote(directory)} \"$url\"; done\n"
                     for directory in directories)
    ensure_master(host)
    try:
        completed = subprocess.run(host.shell_command(script), capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as error:
        raise FleetError(f"{host.name}: timed out reading repository remotes") from error
    if completed.returncode != 0:
        raise FleetError(f"{host.name}: could not read repository remotes: {completed.stderr.strip()}")
    remotes: dict[str, list[str]] = {directory: [] for directory in directories}
    for line in completed.stdout.splitlines():
        directory, _, url = line.partition("\t")
        if directory in remotes and url:
            remotes[directory].append(url)
    return remotes


def rsync(sources: list[str], destination: str, host: Host) -> None:
    ensure_master(host)
    command = ["rsync", "-a", *sources, destination]
    if not host.is_local:
        command[1:1] = ["-e", " ".join(["ssh", *host.ssh_options])]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise FleetError(f"rsync failed: {completed.stderr.strip()}")


def follow_stream(host: Host, receive: Callable[[dict[str, Any]], None], *,
                  events: str, silence_limit: int, stop: threading.Event | None = None) -> str:
    """Drain a worker stream while its messages are handled; return why it ended."""
    if stop is not None and stop.is_set():
        return "stream cancelled"
    try:
        worker_version(host)
    except FleetError as error:
        return str(error)
    except subprocess.TimeoutExpired:
        return "ssh connect timed out"
    if stop is not None and stop.is_set():
        return "stream cancelled"
    process = subprocess.Popen(host.fleetd_command(["stream", "--events", events]),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None and process.stderr is not None
    # Both pipes are drained on their own threads. Applying a message may call the host again over the same ssh
    # master (the catch-up after hello); the master writes the stream into these pipes and, once one is full,
    # blocks every channel to the host, the catch-up's included, so reading here alone deadlocked.
    lines: queue.Queue[bytes | None] = queue.Queue()
    stderr_lines: list[str] = []

    def pump_stdout() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    def pump_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line.decode(errors="replace").rstrip())
            del stderr_lines[:-20]

    pumps = [threading.Thread(target=pump, daemon=True) for pump in (pump_stdout, pump_stderr)]
    for pump in pumps:
        pump.start()
    try:
        deadline = time.monotonic() + silence_limit
        while stop is None or not stop.is_set():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = lines.get(timeout=min(remaining, 0.1) if stop is not None else silence_limit)
            except queue.Empty:
                if stop is None or time.monotonic() >= deadline:
                    return f"no heartbeat for {silence_limit}s"
                continue
            deadline = time.monotonic() + silence_limit
            if line is None:
                process.wait(timeout=5)
                pumps[1].join(timeout=5)
                return stderr_lines[-1] if stderr_lines else f"stream ended (exit {process.returncode})"
            if line.strip():
                document = json.loads(line)
                if document.get("type") == "hello":
                    try:
                        check_protocol(host, document)
                    except FleetError as error:
                        return str(error)
                receive(document)
        return "stream cancelled"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for pump in pumps:
            pump.join()
        process.stdout.close()
        process.stderr.close()


TimeoutExpired = subprocess.TimeoutExpired


def run_shell(host: Host, command: str, *, interactive: bool = False, **options):
    return subprocess.run(host.shell_command(command, interactive=interactive), **options)


def exec_shell(host: Host, command: str) -> None:
    arguments = host.shell_command(command, interactive=True)
    os.execvp(arguments[0], arguments)


def events(host: Host, job_id: str, *, lines: int, follow: bool):
    worker_version(host)
    command = host.fleetd_command(["events", job_id, "--lines", str(lines)] + (["-f"] if follow else []))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    try:
        for line in process.stdout:
            yield json.loads(line)
    except KeyboardInterrupt:
        process.terminate()


def wait_jobs(pending: dict[str, tuple[Host, str]], *, step: int | None,
              timeout: float | None, any_job: bool):
    # Prepare every connection before opening a pipe, so setup failure cannot orphan another waiter.
    for host, _ in pending.values():
        worker_version(host)
    processes = {}
    for reference, (host, job_id) in pending.items():
        arguments = ["wait", job_id] + (["--step", str(step)] if step is not None else [])
        arguments += ["--timeout", str(timeout)] if timeout else []
        processes[reference] = subprocess.Popen(host.fleetd_command(arguments), stdout=subprocess.PIPE, text=True)
    finished = False
    while processes:
        for reference, process in list(processes.items()):
            if process.poll() is None:
                continue
            output = (process.stdout.read() if process.stdout else "").strip().splitlines()
            job = json.loads(output[-1]) if output else {"error": "no output"}
            del processes[reference]
            finished = True
            yield reference, job
        if any_job and finished:
            for process in processes.values():
                process.terminate()
            break
        time.sleep(0.5)

__all__ = ["Host", "HostReport", "SSH_OPTIONS", "REMOTE_FLEETD_PATH"]
