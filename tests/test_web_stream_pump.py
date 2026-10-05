"""The deck keeps reading a host's stream while it handles a message.

Handling hello calls the host again over the same ssh master; the master writes the stream into the deck's pipe and,
once that pipe is full, blocks every channel to the host. So the stream must be drained while a message is handled.
Here the fake stream writes 1 MB after hello and then a marker file; handling hello waits for the marker.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import fleet.web.server as server


class FakeHost:
    name = "worker"

    def __init__(self, script: str) -> None:
        self.script = script

    def fleetd_command(self, arguments: list[str]) -> list[str]:
        return [sys.executable, "-c", self.script]


def test_the_stream_is_drained_while_a_message_is_handled(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "written"
    filler = json.dumps({"type": "heartbeat", "pad": "x" * 1000})
    script = (f"import sys, pathlib\n"
              f"print({json.dumps(json.dumps({'type': 'hello'}))}, flush=True)\n"
              f"for _ in range(1000): print({filler!r})\n"
              f"sys.stdout.flush()\n"
              f"pathlib.Path({str(marker)!r}).write_text('done')\n")
    handled: list[str] = []
    waited: list[float] = []

    def apply(state, host, message) -> None:
        if message["type"] == "hello":
            start = time.monotonic()
            while not marker.exists() and time.monotonic() - start < 10:
                time.sleep(0.05)
            waited.append(time.monotonic() - start)
        handled.append(message["type"])

    monkeypatch.setattr(server.transport, "ensure_master", lambda host: None)
    monkeypatch.setattr(server, "apply_message", apply)
    reason = server.run_stream(None, FakeHost(script))

    assert marker.exists() and waited[0] < 5, "the stream's writer was blocked while hello was handled"
    assert handled == ["hello"] + ["heartbeat"] * 1000
    assert reason == "stream ended (exit 0)"
