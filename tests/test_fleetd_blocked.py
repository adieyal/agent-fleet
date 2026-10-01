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
    monkeypatch.setattr(fleetd, "collect_workspace", lambda cwd: (None, "not a git repository"))  # git would use the fake

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


def test_without_stop_on_failure_a_blocked_step_still_holds_the_job(jobs, monkeypatch):
    jobs(["first", "second"], stop_on_failure=False)
    job = run_with_replies(monkeypatch, [claude_result("FLEET_STATUS: blocked"), claude_result("FLEET_STATUS: done")])
    assert [step["status"] for step in job["steps"]] == ["blocked", "pending"]
    assert "reason" not in job["steps"][0]
    assert fleetd.derive_status(job) == "blocked"


def run_recording_order(monkeypatch):
    """Run the job with a fake agent that finishes every step; returns the step indices in the order they ran."""
    order = []

    def agent(command, **kwargs):
        order.append(next(step["index"] for step in fleetd.read_job("job")["steps"] if step["status"] == "running"))
        return SimpleNamespace(pid=123, stdout=StringIO(claude_result("FLEET_STATUS: done")), wait=lambda: 0)
    monkeypatch.setattr(fleetd.subprocess, "Popen", agent)
    fleetd.run_job("job")
    return order


def test_a_new_runner_starts_nothing_while_a_step_waits_for_its_answer(jobs, monkeypatch, tmp_path):
    job = jobs(["first", "second"])
    job["steps"][0]["status"] = "blocked"
    (fleetd.JOBS_DIRECTORY / "job" / "job.json").write_text(json.dumps(job))
    steps_file = tmp_path / "steps.json"
    steps_file.write_text(json.dumps([{"prompt": "third"}]))
    fleetd.command_add(argparse.Namespace(job="job", steps_file=str(steps_file), retry=False, hold=True,
                                          key=None, answers=None, schema_version=None))   # an unkeyed add appends
    assert run_recording_order(monkeypatch) == []
    job = fleetd.read_job("job")
    assert [step["status"] for step in job["steps"]] == ["blocked", "pending", "pending"]
    assert fleetd.derive_status(job) == "blocked"


def test_the_answer_runs_before_the_steps_queued_behind_the_blocked_one(jobs, monkeypatch, tmp_path):
    jobs(["first", "second", "third"])
    run_with_replies(monkeypatch, [claude_result("Which one?\n\nFLEET_STATUS: blocked — needs a decision")])
    answer(tmp_path)
    assert run_recording_order(monkeypatch) == [3, 1, 2]
    job = fleetd.read_job("job")
    assert [step["index"] for step in job["steps"]] == [0, 1, 2, 3]   # nothing renumbered
    assert [step["status"] for step in job["steps"]] == ["blocked", "done", "done", "done"]
    assert fleetd.derive_status(job) == "done"


def test_a_blocked_job_can_be_cancelled(jobs, monkeypatch, tmp_path, capsys):
    jobs(["first", "second"])
    run_with_replies(monkeypatch, [claude_result("FLEET_STATUS: blocked — needs a decision")])
    fleetd.command_cancel(argparse.Namespace(job="job", all_steps=True))
    job = fleetd.read_job("job")
    assert [step["status"] for step in job["steps"]] == ["blocked", "cancelled"]
    assert fleetd.derive_status(job) == "cancelled"
    answer(tmp_path)   # an answer reopens the job and runs only itself
    assert run_recording_order(monkeypatch) == [2]
    assert fleetd.derive_status(fleetd.read_job("job")) == "done"


def test_a_delivered_reply_answers_the_waiting_step(jobs, monkeypatch, capsys):
    job = jobs(["first", "second"], session_id="s", cancelled=False)
    job["steps"][0]["status"] = "blocked"
    (fleetd.JOBS_DIRECTORY / "job" / "job.json").write_text(json.dumps(job))
    monkeypatch.setattr(fleetd.sys, "stdin", StringIO("Use the second."))
    monkeypatch.setattr(fleetd, "launch_runner", lambda job_id: None)
    fleetd.command_deliver(argparse.Namespace(job="job", key="decision", schema_version=1))
    job = fleetd.read_job("job")
    assert job["steps"][0]["answered_by"] == 2
    assert run_recording_order(monkeypatch) == [2, 1]


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
    fleetd.command_add(argparse.Namespace(job="job", steps_file=str(steps_file), retry=True, hold=True,
                                          key=None, answers=None, schema_version=None))
    step = fleetd.read_job("job")["steps"][0]
    assert step["status"] == "pending" and "reason" not in step


def answer(tmp_path, key="item:answer", answers=0, reply="Yes, exempt it and continue."):
    steps_file = tmp_path / "answer.json"
    steps_file.write_text(json.dumps([{"prompt": reply, "title": "Answer to step 1"}]))
    fleetd.command_add(argparse.Namespace(job="job", steps_file=str(steps_file), retry=False, hold=True,
                                          key=key, answers=answers, schema_version=1))


def test_a_blocked_step_reports_its_final_message_in_full(jobs, monkeypatch):
    jobs(["first"])
    question = "May I exempt the existing import and continue? " + "The rule says stop. " * 40
    job = run_with_replies(monkeypatch, [claude_result(question + "\n\nFLEET_STATUS: blocked — needs your decision")])
    [step] = fleetd.job_summary(job, 0)["steps"]
    assert step["message"] == question + "\n\nFLEET_STATUS: blocked — needs your decision"
    assert step["result"] != step["message"]   # the result is shortened for lists
    assert step["answered_by"] is None


def test_an_answer_continues_the_job_once_per_key(jobs, monkeypatch, tmp_path, capsys):
    jobs(["first"])
    run_with_replies(monkeypatch, [claude_result("Exempt it?\n\nFLEET_STATUS: blocked — needs a decision")])
    for _ in range(2):   # the second is a retry after a lost reply
        answer(tmp_path)
        assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {
            "schema_version": 1, "key": "item:answer", "status": "applied", "answers": 0, "steps": [1]}
    job = fleetd.read_job("job")
    assert [(step["title"], step["prompt"], step["status"]) for step in job["steps"][1:]] == [
        ("Answer to step 1", "Yes, exempt it and continue.", "pending")]
    assert job["steps"][0]["answered_by"] == 1
    assert fleetd.derive_status(job) == "queued"
    assert fleetd.job_summary(job, 0)["steps"][0]["message"] is None   # answered: no longer asking
    job = run_with_replies(monkeypatch, [claude_result("Done.\n\nFLEET_STATUS: done")])
    assert fleetd.derive_status(job) == "done"


@pytest.mark.parametrize("status, answers, error", [
    ("done", 0, "not waiting for an answer"),
    ("blocked", 3, "job has no step 3"),
])
def test_an_answer_to_a_step_that_is_not_asking_changes_nothing(jobs, tmp_path, capsys, status, answers, error):
    job = jobs(["first"])
    job["steps"][0]["status"] = status
    (fleetd.JOBS_DIRECTORY / "job" / "job.json").write_text(json.dumps(job))
    with pytest.raises(SystemExit):
        answer(tmp_path, answers=answers)
    assert error in capsys.readouterr().out
    assert fleetd.read_job("job") == job


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


def test_batch12_notify_reports_host_transitions_once(monkeypatch, capsys):
    reports = [[SimpleNamespace(host=SimpleNamespace(name='h'), jobs=[], error=error)]
               for error in ('connection refused', 'connection refused', None, None, 'timed out')]
    monkeypatch.setattr(cli, 'selected_hosts', lambda arguments: [])
    monkeypatch.setattr(cli.transport, 'gather', lambda *_args: reports.pop(0))

    def sleep(_seconds):
        if not reports:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        cli.command_notify(argparse.Namespace(interval=0))
    assert capsys.readouterr().out.splitlines() == [
        'HOST DOWN h: connection refused', 'HOST UP h', 'HOST DOWN h: timed out']
