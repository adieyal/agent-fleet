"""A mismatched host must be rejected before any operation is started."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from fleet import transport
from fleet.errors import FleetError
from fleet_worker import WIRE_PROTOCOL_VERSION
from fleet_worker import fleetd


def test_worker_contract_and_distribution_versions_agree():
    from importlib.metadata import version
    assert WIRE_PROTOCOL_VERSION == fleetd.WIRE_PROTOCOL_VERSION
    assert version("fleet-worker") == fleetd.WORKER_VERSION


@pytest.mark.parametrize("version", [None, 0, 1, 3, "2", True])
@pytest.mark.parametrize("path", ["call", "events", "wait", "stream"])
def test_every_host_path_rejects_mismatch_before_opening_operation(monkeypatch, version, path):
    host = transport.Host("test-worker", None)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps({"wire_protocol_version": version}), stderr="", returncode=0)

    monkeypatch.setattr(transport.subprocess, "run", run)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: pytest.fail("opened incompatible worker"))
    if path == "stream":
        message = transport.follow_stream(host, lambda _: pytest.fail("received incompatible worker"),
                                          events="0", silence_limit=1)
    else:
        with pytest.raises(FleetError) as failure:
            if path == "call":
                transport.call(host, ["cancel", "job"])
            elif path == "events":
                list(transport.events(host, "job", lines=1, follow=False))
            else:
                list(transport.wait_jobs({"job": (host, "job")}, step=None, timeout=1, any_job=False))
        message = str(failure.value)
    assert "controller 2" in message
    assert "worker " in message
    assert "fleet install test-worker" in message
    assert len(commands) == 1 and commands[0][-1] == "version"


def test_old_worker_without_version_has_explicit_upgrade_message(monkeypatch):
    monkeypatch.setattr(transport.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="", stderr="fleetd: invalid choice: version", returncode=2))
    with pytest.raises(FleetError, match="controller 2, worker unreported.*fleet install old"):
        transport.call(transport.Host("old", None), ["ls"])


def test_compatible_call_preflights_each_operation(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command[-1])
        data = {"wire_protocol_version": 2, "worker_version": "0.1.1"} if command[-1] == "version" else {"jobs": []}
        return SimpleNamespace(stdout=json.dumps(data), stderr="", returncode=0)

    monkeypatch.setattr(transport.subprocess, "run", run)
    host = transport.Host("test", None)
    assert transport.call(host, ["ls"]) == {"jobs": []}
    assert transport.call(host, ["ls"]) == {"jobs": []}
    assert commands == ["version", "ls", "version", "ls"]


def test_stream_hello_is_checked_after_preflight(monkeypatch):
    monkeypatch.setattr(transport, "worker_version", lambda host: {"wire_protocol_version": 2})
    killed = []
    process = SimpleNamespace(stdout=io.BytesIO(b'{"type":"hello","wire_protocol_version":1}\n'),
                              stderr=io.BytesIO(), wait=lambda **k: None, poll=lambda: None,
                              kill=lambda: killed.append(True))
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: process)
    message = transport.follow_stream(transport.Host("test", None), lambda _: pytest.fail("received mismatch"),
                                      events="0", silence_limit=1)
    assert "controller 2, worker 1" in message and "fleet install test" in message
    assert killed == [True]


def test_python38_check_is_a_required_ci_step():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "uv run --no-project --python 3.8 python scripts/checks/worker_python38.py" in (
        root / ".github/workflows/checks.yml").read_text()


def test_standalone_version_command(tmp_path):
    import os
    import sys
    result = subprocess.check_output([sys.executable, "-I", fleetd.__file__, "version"],
                                     env={**os.environ, "FLEET_HOME": str(tmp_path)})
    assert json.loads(result) == {"worker_version": "0.1.1", "wire_protocol_version": 2,
                                  "stream_protocol_version": 3, "dispatch_schema_version": 4}


def test_session_gather_does_not_hide_protocol_mismatch(monkeypatch):
    monkeypatch.setattr(transport, "call", lambda *a, **k: (_ for _ in ()).throw(
        transport.ProtocolMismatch("controller 2, worker 1; fleet install test")))
    with pytest.raises(transport.ProtocolMismatch, match="fleet install test"):
        transport.gather_sessions([transport.Host("test", None)])
