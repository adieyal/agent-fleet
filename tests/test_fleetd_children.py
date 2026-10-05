"""Real runtime exit must not finish a step before detached descendants exit."""
import json
import os
import subprocess
import sys

import pytest

@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_step_waits_for_detached_grandchild(tmp_path, agent, cancel):
    marker = tmp_path / "finished"
    runtime = tmp_path / "runtime.py"
    records = ([{"type": "result", "subtype": "success", "is_error": False,
                 "result": "FLEET_STATUS: done", "session_id": "s"}] if agent == "claude" else
               [{"type": "item.completed", "item": {"type": "agent_message", "text": "FLEET_STATUS: done"}},
                {"type": "turn.completed"}])
    runtime.write_text(f'''
import os, time
from pathlib import Path
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        os.close(1)
        os.close(2)
        time.sleep(0.6)
        Path({str(marker)!r}).write_text("finished")
    os._exit(0)
print({''.join(json.dumps(r) + chr(10) for r in records)!r}, end="", flush=True)
''')
    script = f'''
import json, sys, threading, time
from pathlib import Path
from fleet_worker import fleetd
job = {{"id": "test", "agent": {agent!r}, "cwd": {str(tmp_path)!r}, "steps": [fleetd.make_step(0, "work", None)]}}
directory = fleetd.JOBS_DIRECTORY / "test"
directory.mkdir(parents=True)
(directory / "job.json").write_text(json.dumps(job))
fleetd.agent_command = lambda *args: [sys.executable, {str(runtime)!r}]
def cancel_children():
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if fleetd.read_job("test").get("runtime_wait", {{}}).get("kind") == "children_wait":
            with fleetd.locked_job("test") as live:
                live["cancelled"] = True
            return
        time.sleep(0.01)
if {cancel!r}:
    threading.Thread(target=cancel_children, daemon=True).start()
fleetd.run_job("test")
assert not fleetd.runner_children(), "descendants must have been reaped"
assert "runtime_wait" not in fleetd.read_job("test")
assert any(event["kind"] == "children_wait" for event in fleetd.read_events("test", 100))
assert Path({str(marker)!r}).exists() == {not cancel!r}, "step completed before descendant finished"
assert fleetd.read_job("test")["steps"][0]["status"] == {("cancelled" if cancel else "done")!r}
'''
    env = {**os.environ, **{key: str(tmp_path / key) for key in
                           ("FLEET_HOME", "FLEET_CONFIG", "FLEET_STORE", "FLEET_MANAGEMENT")}}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
