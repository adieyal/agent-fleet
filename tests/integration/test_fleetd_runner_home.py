import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

from fleet_worker import fleetd


@pytest.fixture
def worker(tmp_path, monkeypatch):
    home = tmp_path / "worker home"
    monkeypatch.setenv("FLEET_HOME", str(home))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("isolated_fleetd", fleetd.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    steps = tmp_path / "steps.json"
    steps.write_text('["Reply done"]')
    module.command_create(argparse.Namespace(id="job", run_id=None, fingerprint=None,
        schema_version=None, cwd=str(tmp_path), agent="codex", permission="read-only",
        steps_file=str(steps), project="p", description="test", model=None,
        keep_going=False, allowed_tools=None, add_dir=[], env=[], hold=True))
    (tmp_path / "exec").write_text('print(\'{"type":"turn.completed","usage":{}}\')\n')
    (home / "config.json").write_text(json.dumps({"codex": sys.executable}))
    return module


def tmux(command, *args, **kwargs):
    return subprocess.run([*command, *args], capture_output=True, text=True,
                          timeout=10, **kwargs)


def test_side_by_side_socket_and_runner_environment(worker, tmp_path):
    assert worker.TMUX_COMMAND[2] != "fleet"
    # Even this worker's server can predate its current environment.
    command = worker.TMUX_COMMAND
    wrong = {**os.environ, "FLEET_HOME": str(tmp_path / "wrong")}
    tmux(command, "new-session", "-d", "-s", "keeper", "sleep 60", env=wrong, check=True)
    try:
        worker.launch_runner("job")
        deadline = time.monotonic() + 10
        while worker.derive_status(worker.read_job("job")) not in worker.TERMINAL_STATUSES:
            assert time.monotonic() < deadline, "runner did not finish"
            time.sleep(.01)
        assert worker.derive_status(worker.read_job("job")) == "done"
        assert command[2] in worker.job_summary(worker.read_job("job"), 0)["tmux"]
        assert tmux(["tmux", "-L", "fleet"], "list-sessions").returncode != 0
        assert not (tmp_path / "wrong" / "jobs").exists()
    finally:
        tmux(command, "kill-session", "-t", "fleet-job")
        tmux(command, "kill-session", "-t", "keeper")


def test_missing_runner_job_records_failure(worker, tmp_path, monkeypatch):
    log_path = worker.JOBS_DIRECTORY / "job" / "runner.log"
    log_path.write_text("previous launch output\n")
    monkeypatch.setattr(worker, "TMUX_COMMAND", ["tmux", "-L", "missing-job", "-f", "/dev/null"])
    wrapper = tmp_path / "wrong_home.py"
    wrapper.write_text("import os, runpy\n"
        f"os.environ['FLEET_HOME'] = {str(tmp_path / 'absent')!r}\n"
        f"runpy.run_path({fleetd.__file__!r}, run_name='__main__')\n")
    monkeypatch.setattr(worker, "__file__", str(wrapper))
    try:
        worker.launch_runner("job")
        job = worker.read_job("job")
        assert worker.derive_status(job) == "failed"
        assert job["steps"][0]["result"] == "no such job: job"
        assert worker.read_events("job", 1)[0]["summary"] == "no such job: job"
        assert log_path.read_text().startswith("previous launch output\n")
    finally:
        tmux(worker.TMUX_COMMAND, "kill-session", "-t", "fleet-job")


def test_runner_bypasses_tmux_default_shell(worker, tmp_path):
    """A user's shell startup must not run before the fleet runner."""
    marker = tmp_path / "shell-started"
    shell = tmp_path / "user-shell"
    shell.write_text(f'#!/bin/sh\n: > "{marker}"\nexec /bin/sh "$@"\n')
    shell.chmod(0o755)
    command = worker.TMUX_COMMAND
    tmux(command, "new-session", "-d", "-s", "keeper", "/bin/sleep", "60", check=True)
    tmux(command, "set-option", "-g", "default-shell", str(shell), check=True)
    try:
        worker.launch_runner("job")
        deadline = time.monotonic() + 10
        while worker.derive_status(worker.read_job("job")) not in worker.TERMINAL_STATUSES:
            assert time.monotonic() < deadline, "runner did not finish"
            time.sleep(.01)
        assert worker.derive_status(worker.read_job("job")) == "done"
        assert not marker.exists(), "tmux ran the user's shell startup"
    finally:
        tmux(command, "kill-server")
