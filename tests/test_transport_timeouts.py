"""Connection preparation is bounded just like the remote command."""
import io
import subprocess
from types import SimpleNamespace

import pytest

from fleet import transport
from fleet.errors import FleetError


@pytest.mark.parametrize("stage, seconds, message", [
    ("check", 10, "SSH control connection check"),
    ("setup", 15, "SSH connection setup"),
])
def test_ssh_master_timeouts_are_bounded_and_translated(monkeypatch, stage, seconds, message):
    host = transport.Host("home", "home")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if stage == "check" or len(calls) == 2:
            assert kwargs["timeout"] == seconds
            raise subprocess.TimeoutExpired(command, seconds)
        assert kwargs["timeout"] == 10
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(transport.subprocess, "run", run)
    with pytest.raises(FleetError, match=f"home: {message} timed out after {seconds}s"):
        transport.call(host, ["ls", "--all"])
    assert len(calls) == (1 if stage == "check" else 2)


def test_remote_command_timeout_is_translated(monkeypatch):
    host = transport.Host("home", "home")
    monkeypatch.setattr(transport, "ensure_master", lambda host: None)

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(transport.subprocess, "run", timeout)
    with pytest.raises(FleetError, match="home: timed out after 30s"):
        transport.call(host, ["add", "job"])


def test_stream_process_is_killed_when_message_handling_fails(monkeypatch):
    host = transport.Host("worker", None)
    monkeypatch.setattr(transport, "worker_version", lambda host: {"wire_protocol_version": 2})
    killed = []
    process = SimpleNamespace(stdout=io.BytesIO(b'{"type": "hello", "wire_protocol_version": 2}\n'), stderr=io.BytesIO(),
                              wait=lambda **kwargs: None, poll=lambda: None, kill=lambda: killed.append(True))
    monkeypatch.setattr(transport, "ensure_master", lambda host: None)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: process)

    def receive(message):
        assert message == {"type": "hello", "wire_protocol_version": 2}
        raise RuntimeError("message rejected")

    with pytest.raises(RuntimeError, match="message rejected"):
        transport.follow_stream(host, receive, events="15", silence_limit=20)
    assert killed == [True]
