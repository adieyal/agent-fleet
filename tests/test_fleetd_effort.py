"""Per-job reasoning effort and codex bare mode reach the agent command line."""
from fleet_worker import fleetd


def job(agent, **extra):
    return {"id": "job1", "agent": agent, "project": "example", "description": "probe",
            "permission": "danger-full-access" if agent == "codex" else "default", "cwd": "/tmp", **extra}


def test_codex_effort_and_bare(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    command = fleetd.agent_command(job("codex", effort="low", bare=True), {"index": 0, "prompt": "probe"}, None)
    assert command[command.index("-c") + 1] == 'model_reasoning_effort="low"'
    assert "--ignore-user-config" in command
    resumed = fleetd.agent_command(job("codex", effort="low", bare=True), {"index": 1, "prompt": "more"}, "s1")
    assert 'model_reasoning_effort="low"' in resumed and "--ignore-user-config" in resumed
    plain = fleetd.agent_command(job("codex"), {"index": 0, "prompt": "probe"}, None)
    assert "--ignore-user-config" not in plain and not any("reasoning_effort" in part for part in plain)


def test_claude_effort_keeps_input_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    command = fleetd.agent_command(job("claude", effort="low", bare=True), {"index": 0, "prompt": "probe"}, None)
    assert command[command.index("--effort") + 1] == "low"
    assert "--bare" not in command and "--settings" in command
