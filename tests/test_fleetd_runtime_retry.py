"""Provider failures resume the same job; tool errors never replay work."""
import json
from io import StringIO
from types import SimpleNamespace

import pytest

from fleet_worker import fleetd


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(fleetd.signal, "signal", lambda *args: None)
    monkeypatch.setattr(fleetd, "collect_workspace", lambda cwd: (None, "test workspace"))
    monkeypatch.setattr(fleetd, "begin_step_git", lambda cwd: {"reason": "test workspace"})
    monkeypatch.setattr(fleetd, "end_step_git", lambda cwd, record: record)
    fleetd.CONFIG_PATH.write_text(json.dumps({"runtime_retry_delays": [0, 0, 0]}))
    job = {"id": "job", "agent": "codex", "project": "p", "description": "Retry",
           "cwd": str(tmp_path), "permission": "read-only", "created_at": 1,
           "runner_pid": None, "steps": [fleetd.make_step(0, "Do work", None)]}
    directory = fleetd.JOBS_DIRECTORY / "job"
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps(job))
    return job


def stream(*records):
    return "".join(json.dumps(record) + "\n" for record in records)


def failure(agent):
    if agent == "claude":
        return stream({"type": "result", "subtype": "error_during_execution", "is_error": True,
                       "errors": ["API Error: 529 overloaded_error"], "session_id": "session-1"})
    return stream({"type": "thread.started", "thread_id": "session-1"},
                  {"type": "turn.failed", "error": {"message": "Selected model is at capacity. Please try a different model."}})


def success(agent):
    if agent == "claude":
        return stream({"type": "result", "subtype": "success", "is_error": False,
                       "result": "FLEET_STATUS: done", "session_id": "session-1"})
    return stream({"type": "item.completed", "item": {"type": "agent_message", "text": "FLEET_STATUS: done"}},
                  {"type": "turn.completed"})


def fake_agent(monkeypatch, replies):
    commands = []
    replies = iter(replies)

    def spawn(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(pid=123, stdout=StringIO(next(replies)), wait=lambda: 0)
    monkeypatch.setattr(fleetd.subprocess, "Popen", spawn)
    return commands


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_transient_failure_resumes_same_session_then_succeeds(job, monkeypatch, agent):
    with fleetd.locked_job("job") as live:
        live["agent"] = agent
    commands = fake_agent(monkeypatch, [failure(agent), success(agent)])
    fleetd.run_job("job")
    live = fleetd.read_job("job")
    assert live["steps"][0]["status"] == "done"
    assert len(commands) == 2
    assert "session-1" in commands[1]
    assert "resume" in commands[1] if agent == "codex" else "--resume" in commands[1]
    events = fleetd.read_events("job", 100)
    waits = [event for event in events if event["kind"] == "retry" and "retry_at" in event]
    assert len(waits) == 1 and waits[0]["retry"] == 1
    assert "runtime_wait" not in live


def test_never_recovers_fails_only_after_three_retries(job, monkeypatch):
    commands = fake_agent(monkeypatch, [failure("codex")] * 4)
    fleetd.run_job("job")
    step = fleetd.read_job("job")["steps"][0]
    assert len(commands) == 4
    assert step["status"] == "failed"
    assert step["reason"] == "model at capacity; exhausted 3 runtime retries"
    assert "Selected model is at capacity" in step["result"]


def test_cancel_during_wait_is_visible_and_launches_no_retry(job, monkeypatch):
    fleetd.CONFIG_PATH.write_text(json.dumps({"runtime_retry_delays": [60, 180, 600]}))
    commands = fake_agent(monkeypatch, [failure("codex")])
    waits = []

    def cancel(seconds):
        live = fleetd.read_job("job")
        summary = fleetd.job_summary(live, 0)
        waits.append(summary["activity"])
        assert summary["status"] == "running"
        assert summary["activity"]["summary"].startswith("waiting: model at capacity, retry 1/3 at ")
        assert live["agent_pid"] is None
        fleetd.command_cancel(SimpleNamespace(job="job", all_steps=True))
    monkeypatch.setattr(fleetd.time, "sleep", cancel)
    fleetd.run_job("job")
    assert len(commands) == 1 and len(waits) == 1
    assert fleetd.read_job("job")["steps"][0]["status"] == "cancelled"


@pytest.mark.parametrize("message,reason", [
    ("HTTP 429 Too Many Requests", "provider rate limited"),
    ("unexpected status 503", "provider HTTP error"),
    ("HTTP status: 500", "provider HTTP error"),
    ("API Error: 500", "provider HTTP error"),
    ("rate_limit_error", "provider rate limited"),
    ("overloaded_error", "provider overloaded"),
    ("api_error: Internal server error", "provider unavailable"),
    ("invalid_api_key", None),
    ("HTTP 401 Unauthorized", None),
    ("context window exceeded", None),
])
def test_provider_error_classification(message, reason):
    assert fleetd.transient_runtime_reason(message) == reason


def test_tool_failure_is_not_retried(job, monkeypatch):
    commands = fake_agent(monkeypatch, [stream(
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1,
                                               "command": "curl provider # HTTP 503"}},
        {"type": "turn.completed"})])
    fleetd.run_job("job")
    assert len(commands) == 1


def test_nontransient_runtime_failure_is_not_retried(job, monkeypatch):
    commands = fake_agent(monkeypatch, [stream({"type": "turn.failed", "error": {"message": "HTTP 401 Unauthorized"}})])
    fleetd.run_job("job")
    assert len(commands) == 1
    assert fleetd.read_job("job")["steps"][0]["status"] == "failed"


def test_recovered_error_in_same_turn_does_not_retry(job, monkeypatch):
    commands = fake_agent(monkeypatch, [stream({"type": "error", "message": "HTTP 503"}) + success("codex")])
    fleetd.run_job("job")
    assert len(commands) == 1
    assert fleetd.read_job("job")["steps"][0]["status"] == "done"


def test_retry_configuration_can_disable_retries(job, monkeypatch):
    fleetd.CONFIG_PATH.write_text(json.dumps({"runtime_retry_delays": []}))
    commands = fake_agent(monkeypatch, [failure("codex")])
    fleetd.run_job("job")
    assert len(commands) == 1
    assert "exhausted 0 runtime retries" in fleetd.read_job("job")["steps"][0]["reason"]


def test_default_backoff_schedule(job, monkeypatch):
    fleetd.CONFIG_PATH.write_text("{}")
    commands = fake_agent(monkeypatch, [failure("codex")] * 4)
    clock = [1000.0]
    monkeypatch.setattr(fleetd, "now", lambda: clock[0])
    monkeypatch.setattr(fleetd.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    fleetd.run_job("job")
    waits = [event for event in fleetd.read_events("job", 100) if "retry_at" in event]
    assert [event["retry_at"] for event in waits] == [1060, 1240, 1840]
    assert [event["retry"] for event in waits] == [1, 2, 3]
    assert len(commands) == 4


def test_wait_message_is_visible_in_cli(job, monkeypatch):
    from fleet_cli.cli import job_label

    with fleetd.locked_job("job") as live:
        live["steps"][0]["status"] = "running"
        live["runtime_wait"] = {"kind": "retry", "summary": "waiting: model at capacity, retry 2/3 at 14:05"}
    monkeypatch.setattr(fleetd, "runner_alive", lambda live: True)
    summary = fleetd.job_summary(fleetd.read_job("job"), 0)
    assert "waiting: model at capacity, retry 2/3 at 14:05" in job_label("test-host", summary).plain


@pytest.mark.parametrize("delays", [[1, 2, 3, 4], [-1], [float("inf")], [float("nan")], [True], "60"])
def test_invalid_retry_configuration_fails_before_launch(job, monkeypatch, delays):
    fleetd.CONFIG_PATH.write_text(json.dumps({"runtime_retry_delays": delays}))
    commands = fake_agent(monkeypatch, [])
    fleetd.run_job("job")
    step = fleetd.read_job("job")["steps"][0]
    assert step["status"] == "failed"
    assert "runtime_retry_delays" in step["result"]
    assert step["reason"] == "invalid runtime retry configuration"
    assert not commands
