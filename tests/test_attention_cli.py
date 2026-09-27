import json
import os
import subprocess
import sys


def test_attention_commands_end_to_end(tmp_path):
    env = {**os.environ, "FLEET_CONFIG": str(tmp_path / "config.json"), "FLEET_STORE": str(tmp_path / "store.db")}

    def run(*args):
        result = subprocess.run([sys.executable, "-m", "fleet.cli", "attention", *args],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    item = run("add", "Choose a direction", "--project", "p1", "--kind", "decision", "--owner", "user",
               "--source", "manual", "--source-reference", "q1", "--context-reference", "doc:1", "--actor", "user")
    assert run("list")[0]["id"] == item["id"]
    assert run("ack", item["id"], "--actor", "user")["state"] == "acknowledged"
    assert run("snooze", item["id"], "--until", "2099-01-01T00:00:00+00:00", "--actor", "user")["state"] == "snoozed"
    assert run("resolve", item["id"], "--details", "Handled", "--actor", "user")["state"] == "resolved"
    assert run("list", "--state", "open") == []
