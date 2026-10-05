import argparse
import ast
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


from fleet.container import configured_container
from fleet.modules.execution import JobObservation
from fleet_worker import fleetd


@pytest.fixture
def worker(tmp_path, monkeypatch):
    home = tmp_path / "worker"
    monkeypatch.setenv("FLEET_HOME", str(home))
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", home / "jobs")
    steps = tmp_path / "steps.json"
    steps.write_text('["Ship"]')
    return argparse.Namespace(id="job", run_id="run", fingerprint="digest", schema_version=4,
        cwd=str(tmp_path), agent="codex", permission="workspace-write", steps_file=str(steps),
        project="p", description="Task", model=None, keep_going=False, allowed_tools=None,
        add_dir=[], env=[], hold=True)


def start_args(worker):
    return argparse.Namespace(job=worker.id, run_id=worker.run_id,
                              fingerprint=worker.fingerprint, schema_version=4)


def test_run_create_start_are_idempotent(worker, monkeypatch, capsys):
    starts = []
    def launch(job_id):
        # Launch must happen outside the reservation lock.
        import fcntl
        with open(fleetd.JOBS_DIRECTORY / job_id / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        starts.append(job_id)
    monkeypatch.setattr(fleetd, "launch_runner", launch)
    for _ in range(2):
        fleetd.command_create(worker)
        fleetd.command_start(start_args(worker))
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(reply["run_id"] == "run" and reply["schema_version"] == 4 for reply in replies)
    assert starts == ["job"]
    before = (fleetd.JOBS_DIRECTORY / "job" / "job.json").read_bytes()
    fleetd.command_start(start_args(worker))
    assert (fleetd.JOBS_DIRECTORY / "job" / "job.json").read_bytes() == before


@pytest.mark.parametrize("change", ["fingerprint", "steps", "job_id", "start"])
def test_run_refuses_changed_payload(worker, capsys, change):
    fleetd.command_create(worker)
    if change == "steps":
        Path(worker.steps_file).write_text('["Different"]')
    elif change == "job_id":
        worker.id = "different"
    else:
        worker.fingerprint = "different"
    with pytest.raises(SystemExit):
        if change == "start":
            fleetd.command_start(start_args(worker))
        else:
            fleetd.command_create(worker)
    assert "fingerprint" in capsys.readouterr().out


def test_killed_local_agent_is_lost_and_releases_claim(worker, capsys):
    execution = configured_container().execution()
    run = execution.dispatch(None, project="p", host="local", runtime="codex",
        payload={"cwd": worker.cwd}, actor="user", reason="test", idempotency_key="key").run
    worker.id, worker.run_id = run.remote_job_id, run.id
    fleetd.command_create(worker)
    agent = Path(worker.cwd) / "agent"
    agent.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    agent.chmod(0o755)
    home = fleetd.JOBS_DIRECTORY.parent
    (home / "config.json").write_text(json.dumps({"codex": str(agent)}))
    process = subprocess.Popen([sys.executable, fleetd.__file__, "_run", worker.id],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    agent_pid = None
    try:
        deadline = time.monotonic() + 10
        while agent_pid is None:
            assert time.monotonic() < deadline, "agent did not start"
            agent_pid = fleetd.read_job(worker.id)["agent_pid"]
            time.sleep(.01)
        os.kill(agent_pid, signal.SIGKILL)
        _, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
    finally:
        if process.poll() is None:
            if agent_pid is not None:
                os.kill(agent_pid, signal.SIGKILL)
            process.kill()
        process.wait(timeout=10)
    capsys.readouterr()
    fleetd.command_reconcile(start_args(worker))
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "lost"
    observation = JobObservation(worker.id, summary["status"], "codex", None, None, None)
    execution.observe("local", observation)
    assert execution.runs()[0].reason == "lost"
    assert execution.runs()[0].status == "failed"
    assert not execution.claims()[0].active
    store = configured_container().store()
    sequence = store.latest_sequence()
    execution.observe("local", observation)
    assert store.latest_sequence() == sequence


def test_fleetd_standalone_stdlib_only(tmp_path):
    source = Path(fleetd.__file__).read_text()
    imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
    for node in imports:
        names = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
        assert all(name.split('.')[0] in sys.stdlib_module_names for name in names)
    copy = tmp_path / "fleetd.py"
    copy.write_text(source)
    result = subprocess.run([sys.executable, "-I", str(copy), "--help"], capture_output=True, timeout=10)
    assert result.returncode == 0


def test_reconcile_by_run_id_and_wrong_version(worker, capsys):
    fleetd.command_create(worker)
    capsys.readouterr()
    fleetd.command_reconcile(start_args(worker))
    reply = json.loads(capsys.readouterr().out)
    assert reply["run_id"] == worker.run_id and reply["id"] == worker.id
    worker.schema_version = 99
    with pytest.raises(SystemExit):
        fleetd.command_create(worker)
    assert "schema version" in capsys.readouterr().out


def test_one_dispatch_version_and_runtime_permission_defaults(worker, capsys):
    worker.schema_version = 3
    with pytest.raises(SystemExit):
        fleetd.command_create(worker)
    with pytest.raises(SystemExit):
        fleetd.command_reconcile(worker)
    worker.schema_version = 4
    worker.permission = None
    fleetd.command_create(worker)
    assert fleetd.read_job(worker.id)['permission'] == 'workspace-write'
    worker.run_id = 'absent'
    capsys.readouterr()
    fleetd.command_reconcile(worker)
    assert json.loads(capsys.readouterr().out)['status'] == 'absent'


def test_worker_refuses_claude_only_flags_for_codex(worker, capsys):
    worker.allowed_tools = '["Bash"]'
    with pytest.raises(SystemExit):
        fleetd.command_create(worker)
    assert 'claude' in capsys.readouterr().out


def test_worker_accepts_extra_writable_directories_for_codex(worker):
    worker.add_dir = ['/tmp']
    fleetd.command_create(worker)
    assert fleetd.read_job(worker.id)["add_dirs"] == ['/tmp']


def test_dead_runner_requires_confirmed_agent_death(worker, monkeypatch):
    fleetd.command_create(worker)
    job = fleetd.read_job(worker.id)
    job["steps"][0]["status"] = "running"
    job["runner_pid"] = 123
    job["agent_pid"] = 456
    monkeypatch.setattr(fleetd, "process_alive", lambda pid: pid == 456)
    assert fleetd.derive_status(job) == "stalled"
    monkeypatch.setattr(fleetd, "process_alive", lambda pid: False)
    assert fleetd.derive_status(job) == "lost"
    job["agent_pid"] = None
    assert fleetd.derive_status(job) == "stalled"
