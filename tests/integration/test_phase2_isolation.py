import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from fleet import transport


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/checks/phase2-gate.sh"


@pytest.mark.parametrize("local", [True, False])
def test_worker_command_defaults_unchanged(monkeypatch, local):
    monkeypatch.delenv("FLEET_FLEETD_PATH", raising=False)
    monkeypatch.delenv("FLEET_REMOTE_HOME", raising=False)
    host = transport.Host("test", None if local else "remote")
    path = "~/.local/share/fleet/fleetd.py"
    expected = (["python3", os.path.expanduser(path), "show", "two words"] if local else
                ["ssh", *transport.SSH_OPTIONS, "remote", f"python3 {path} show 'two words'"])
    assert host.fleetd_command(["show", "two words"]) == expected


@pytest.mark.parametrize("local", [True, False])
def test_worker_command_overrides_are_quoted_and_expand_on_host(monkeypatch, local):
    monkeypatch.setenv("FLEET_FLEETD_PATH", "~/side by side/fleetd.py")
    monkeypatch.setenv("FLEET_REMOTE_HOME", "~/worker space")
    host = transport.Host("test", None if local else "remote")
    command = host.fleetd_command(["show", "two words"])
    if local:
        assert command == ["env", f"FLEET_HOME={Path.home()}/worker space", "python3",
                           f"{Path.home()}/side by side/fleetd.py", "show", "two words"]
    else:
        assert command[-1] == 'env FLEET_HOME="$HOME"/\'worker space\' python3 "$HOME"/\'side by side/fleetd.py\' show \'two words\''


def test_gate_setup_teardown_does_not_touch_live_paths(tmp_path):
    home = tmp_path / "home"
    protected = [home / name for name in (".fleet", ".config/fleet", ".local/share/fleet")]
    for directory in protected:
        directory.mkdir(parents=True)
        (directory / "sentinel").write_text("unchanged")
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for d in protected for p in d.rglob("*")}
    result = subprocess.run([str(SCRIPT), "--hosts", "local", "--setup-only"],
                            env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "setup and teardown verified" in result.stdout
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for d in protected for p in d.rglob("*")} == before
    assert not list(home.glob(".fleet-m5-*"))
    assert not list((home / ".local/share").glob("fleet-m5-*"))


def load_gate():
    spec = importlib.util.spec_from_file_location("phase2_gate", SCRIPT.with_name("phase2_gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kill_pid_is_selected_only_from_owned_job(tmp_path):
    gate = load_gate()
    path = tmp_path / "jobs" / "owned" / "job.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "owned", "run_id": "run", "agent_pid": 12345}))
    assert gate.agent_pid(tmp_path, "owned", "run") == 12345
    with pytest.raises(ValueError):
        gate.agent_pid(tmp_path, "../owned", "run")
    with pytest.raises(ValueError):
        gate.agent_pid(tmp_path, "owned", "other-run")
    path.write_text(json.dumps({"id": "owned", "run_id": "run", "agent_pid": None}))
    assert gate.agent_pid(tmp_path, "owned", "run") is None
    path.write_text(json.dumps({"id": "owned", "run_id": "run", "agent_pid": 0}))
    with pytest.raises(ValueError):
        gate.agent_pid(tmp_path, "owned", "run")
    outside = tmp_path.parent / "outside.json"
    outside.write_text(json.dumps({"id": "owned", "run_id": "run", "agent_pid": 12345}))
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        gate.agent_pid(tmp_path, "owned", "run")


def test_help_describes_isolation_and_interrupted_cleanup():
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    for term in ("ControlMaster", "FLEET_HOME", "interrupted", "cancel", "carbon", "home"):
        assert term in result.stdout


def test_gate_checks_and_kill_reconcile_with_local_workers(tmp_path, monkeypatch):
    gate = load_gate()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / "unused-auth"))
    root = tmp_path / "controller"
    root.mkdir()
    environment = gate.Environment(["local"], root)
    try:
        environment.setup()
        environment.hosts.append(transport.Host("second", None))
        environment.homes["second"] = environment.homes["local"]
        state = gate.existing_checks(environment)
        worker_home = Path(environment.homes["local"])
        (worker_home / "exec").write_text("import time\ntime.sleep(60)\n")
        (worker_home / "config.json").write_text(json.dumps({"codex": sys.executable}))
        gate.killed_agent(environment, *state)
    finally:
        environment.cleanup()
    assert not worker_home.exists()


def test_gate_preserves_check_error_when_cleanup_also_fails(monkeypatch, capsys):
    gate = load_gate()

    class BrokenEnvironment:
        def __init__(self, names, root):
            self.control = str(root / "ssh-%C")

        def setup(self):
            raise RuntimeError("original check error")

        def cleanup(self):
            raise RuntimeError("cleanup error")

    monkeypatch.setattr(gate, "Environment", BrokenEnvironment)
    monkeypatch.setattr(sys, "argv", ["phase2_gate", "--setup-only"])
    previous = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(RuntimeError, match="original check error"):
            gate.main()
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert "cleanup error" in capsys.readouterr().err
