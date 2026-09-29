"""A step whose agent ends with `FLEET_STATUS: blocked` is blocked, not failed, from the runner to the CLI."""
import argparse
import json
from io import StringIO
from types import SimpleNamespace

import pytest

from fleet import cli
from fleet.modules.execution.domain import JobObservation
from fleet.remote import fleetd


def claude_result(text, *, error=False):
    return json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success",
                       "is_error": error, "result": text, "session_id": "s"}) + "\n"


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(fleetd.signal, "signal", lambda *args: None)

    def write(steps, **fields):
        job = {"id": "job", "agent": "claude", "project": "p", "description": "Blocked", "cwd": str(tmp_path),
               "permission": "read-only", "created_at": 1, "runner_pid": None,
               "steps": [fleetd.make_step(index, prompt, None) for index, prompt in enumerate(steps)], **fields}
        (tmp_path / "jobs" / "job").mkdir(parents=True, exist_ok=True)
        (tmp_path / "jobs" / "job" / "job.json").write_text(json.dumps(job))
        return job
    return write


def run_with_replies(monkeypatch, replies):
    """Run the job with a fake agent that answers each step with the next reply."""
    replies = list(replies)
    monkeypatch.setattr(fleetd.subprocess, "Popen",
                        lambda command, **kwargs: SimpleNamespace(pid=123, stdout=StringIO(replies.pop(0)),
                                                                  wait=lambda: 0))
    fleetd.run_job("job")
    return fleetd.read_job("job")


def test_a_blocked_step_records_blocked_and_its_reason_and_stops_the_job(jobs, monkeypatch):
    jobs(["first", "second"])
    job = run_with_replies(monkeypatch, [claude_result("No token.\n\nFLEET_STATUS: blocked — no access to the inbox")])
    first, second = job["steps"]
    assert (first["status"], first["reason"]) == ("blocked", "no access to the inbox")
    assert second["status"] == "pending"
    assert fleetd.derive_status(job) == "blocked"
    events = [json.loads(line) for line in (fleetd.JOBS_DIRECTORY / "job" / "events.jsonl").read_text().splitlines()]
    assert {"kind": "job", "status": "blocked"}.items() <= events[-1].items()


def test_without_stop_on_failure_a_blocked_step_lets_the_rest_run(jobs, monkeypatch):
    jobs(["first", "second"], stop_on_failure=False)
    job = run_with_replies(monkeypatch, [claude_result("FLEET_STATUS: blocked"), claude_result("FLEET_STATUS: done")])
    assert [step["status"] for step in job["steps"]] == ["blocked", "done"]
    assert "reason" not in job["steps"][0]
    assert fleetd.derive_status(job) == "blocked"


@pytest.mark.parametrize("reply, status", [
    (claude_result("FLEET_STATUS: failed — tests still fail"), "failed"),
    (claude_result("FLEET_STATUS: blocked — never finished", error=True), "failed"),   # the turn itself broke
    (claude_result("FLEET_STATUS: done"), "done"),
])
def test_only_a_finished_turn_that_says_blocked_is_blocked(jobs, monkeypatch, reply, status):
    jobs(["only"])
    assert run_with_replies(monkeypatch, [reply])["steps"][0]["status"] == status


@pytest.mark.parametrize("statuses, derived", [
    (["done", "blocked"], "blocked"),
    (["blocked", "pending"], "blocked"),
    (["blocked", "failed"], "failed"),
    (["failed", "blocked"], "failed"),
    (["done", "done"], "done"),
])
def test_derive_status_is_blocked_only_when_no_step_failed(statuses, derived):
    job = {"steps": [{"index": index, "status": status} for index, status in enumerate(statuses)]}
    assert fleetd.derive_status(job) == derived
    assert derived in fleetd.TERMINAL_STATUSES or derived == "done"


def test_retry_requeues_a_blocked_step_and_drops_its_reason(jobs, tmp_path):
    job = jobs(["first"])
    job["steps"][0].update(status="blocked", reason="no access")
    (fleetd.JOBS_DIRECTORY / "job" / "job.json").write_text(json.dumps(job))
    steps_file = tmp_path / "steps.json"
    steps_file.write_text("[]")
    fleetd.command_add(argparse.Namespace(job="job", steps_file=str(steps_file), retry=True, hold=True))
    step = fleetd.read_job("job")["steps"][0]
    assert step["status"] == "pending" and "reason" not in step


def test_fleetd_wait_returns_for_a_blocked_job(jobs, capsys):
    job = jobs(["first"])
    job["steps"][0]["status"] = "blocked"
    (fleetd.JOBS_DIRECTORY / "job" / "job.json").write_text(json.dumps(job))
    fleetd.command_wait(argparse.Namespace(job="job", step=None, timeout=1))
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "blocked"


def test_fleet_wait_exits_1_and_says_blocked(monkeypatch, capsys):
    finished = {"status": "blocked", "description": "Gather notes",
                "results": [{"index": 0, "title": "Gather", "status": "blocked", "result": "no access"}]}
    host = SimpleNamespace(name="h", fleetd_command=lambda arguments: arguments)
    monkeypatch.setattr(cli, "resolve", lambda reference: (host, "job"))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command, **kwargs: SimpleNamespace(
        poll=lambda: 0, stdout=StringIO(json.dumps(finished) + "\n"), terminate=lambda: None))
    with pytest.raises(SystemExit) as exit_info:
        cli.wait_for(["h:job"], step=None, timeout=None, as_json=False)
    assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert "h:job blocked" in output and "⚑ 1. Gather" in output


def test_fleet_notify_says_blocked(monkeypatch, capsys):
    running = {"id": "job", "project": "p", "description": "Gather notes", "status": "running",
               "steps": [{"index": 0, "title": "Gather", "status": "running"}]}
    blocked = {**running, "status": "blocked",
               "steps": [{"index": 0, "title": "Gather", "status": "blocked", "result": "no access"}]}
    reports = [[SimpleNamespace(host=SimpleNamespace(name="h"), jobs=[job])] for job in (running, blocked)]
    monkeypatch.setattr(cli, "selected_hosts", lambda arguments: [])
    monkeypatch.setattr(cli.transport, "gather", lambda hosts, arguments: reports.pop(0))

    def sleep(seconds):
        if not reports:
            raise KeyboardInterrupt
    monkeypatch.setattr(cli.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        cli.command_notify(argparse.Namespace(interval=0))
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["STEP BLOCKED h:job step 1/1: Gather — no access", "JOB BLOCKED h:job (p): Gather notes"]


def test_a_blocked_job_is_a_failed_run_with_reason_blocked():
    assert JobObservation("job", "blocked", "claude", None, None, None).run_status() == "failed"
