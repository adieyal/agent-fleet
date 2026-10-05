import json
import os
import subprocess
import sys
from fleet.container import configured_container


def test_attention_commands_end_to_end(tmp_path):
    env = {**os.environ, "FLEET_CONFIG": str(tmp_path / "config.json"), "FLEET_STORE": str(tmp_path / "store.db")}
    (tmp_path / 'config.json').write_text(json.dumps({'projects': {'p-00000001': {'name': 'p1'}}}))
    configured_container(path=tmp_path / 'store.db').store()

    def run(*args):
        result = subprocess.run([sys.executable, "-m", "fleet_cli.cli", "attention", *args],
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


def test_attention_owner_commands_end_to_end(tmp_path):
    env = {**os.environ, "FLEET_CONFIG": str(tmp_path / "config.json"), "FLEET_STORE": str(tmp_path / "store.db")}
    (tmp_path / 'config.json').write_text(json.dumps({'projects': {'p-00000001': {'name': 'p1'}}}))
    configured_container(path=tmp_path / 'store.db').store()

    def run(*args, code=0):
        if args[0] in ('delegate', 'take'):
            args = (*args, '--json')
        result = subprocess.run([sys.executable, "-m", "fleet_cli.cli", "attention", *args],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == code, result.stderr
        return json.loads(result.stdout) if code == 0 else result.stderr

    item = run("add", "Push the release branch?", "--project", "p1", "--kind", "decision", "--owner", "user",
               "--reason", "pushing needs the user's approval", "--source", "manual", "--source-reference", "q1",
               "--context-reference", "doc:1", "--actor", "claude")
    assert (item["owner"], item["owner_reason"], item["subject"]) == ("user", "pushing needs the user's approval", None)
    assert "invalid choice: 'job:carbon:ab12'" in run(
        "add", "Step failed", "--project", "p1", "--kind", "blocker", "--owner", "job:carbon:ab12", "--source",
        "manual", "--source-reference", "q2", "--context-reference", "doc:2", "--actor", "user", code=2)

    assert "no confirmed triage mandate" in run("delegate", item["id"], "--actor", "user", code=2)

    from fleet.modules.records import TRIAGE_PATH
    policy = dict(goal='Inspect attention', constraints=['Do not complete work or judge criteria'],
                  decision_authority=['record_decision', 'escalate'], escalation_conditions=['Outside policy'],
                  criteria_it_may_judge=[], host='home', runtime='codex', cwd=str(tmp_path),
                  permission='workspace-write', routing={}, permissions={'allow': [], 'escalate': []},
                  limits={'retries_per_step': 1, 'runs_per_day': 3, 'unclaimed_minutes': 30})
    configured_container(configured_container(path=tmp_path / 'store.db').store()).records().write_mandate('p-00000001', TRIAGE_PATH, json.dumps(policy), key='owner-test', actor='user')

    delegated = run("delegate", item["id"], "--actor", "user", "--note", "decide under the charter")
    assert (delegated["owner"], delegated["owner_reason"], delegated["state"]) == (
        "agent", "decide under the charter", "open")
    assert [entry["id"] for entry in run("list", "--owner", "agent")] == [item["id"]]
    assert run("list", "--owner", "user") == []
    assert "escalation needs a reason" in run("escalate", item["id"], "--actor", "triage", "--reason", " ", code=2)
    escalated = run("escalate", item["id"], "--actor", "triage", "--reason", "outside the charter")
    assert (escalated["owner"], escalated["owner_reason"], escalated["owner_actor"]) == (
        "user", "outside the charter", "triage")
    assert "is yours, not the agent" in run("take", item["id"], "--actor", "user", code=2)
    run("delegate", item["id"], "--actor", "user")
    assert run("take", item["id"], "--actor", "user", "--reason", "I'll do it")["owner"] == "user"
