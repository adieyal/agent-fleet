import datetime
import json
import os
import time

from fleet_worker import fleetd


def test_session_base_survives_restart_and_working_workspace_refreshes(tmp_path, monkeypatch):
    clock = [100]
    head = ["a" * 40]
    calls = []
    monkeypatch.setattr(fleetd, "now", lambda: clock[0])
    monkeypatch.setattr(fleetd, "checked_git", lambda *args: head[0])
    def collect(cwd):
        calls.append(cwd)
        return {"head": head[0]}, None
    monkeypatch.setattr(fleetd, "collect_workspace", collect)
    transcript = fleetd.Transcript(tmp_path / "session.jsonl", "claude")
    transcript.cwd = str(tmp_path)
    first, reason = fleetd.session_workspace(transcript, "working")
    assert reason is None and first["base"] == "a" * 40
    head[0] = "b" * 40
    clock[0] = 129
    assert fleetd.session_workspace(transcript, "working")[0] == first
    clock[0] = 130
    refreshed, _ = fleetd.session_workspace(transcript, "working")
    assert refreshed["head"] == "b" * 40 and refreshed["base"] == first["base"]
    restarted = fleetd.Transcript(transcript.path, "claude")
    restarted.cwd = transcript.cwd
    assert fleetd.session_workspace(restarted, "idle")[0]["base"] == first["base"]
    assert len(calls) == 3


def test_session_catchup_is_bounded_to_thirty_days(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "CLAUDE_PROJECTS_DIRECTORY", tmp_path / "projects")
    monkeypatch.setattr(fleetd, "CODEX_SESSIONS_DIRECTORY", tmp_path / "codex")
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    directory = tmp_path / "projects" / "-work"
    directory.mkdir(parents=True)
    for identity, days in (("recent", 29), ("old", 31)):
        transcript = directory / f"{identity}.jsonl"
        transcript.write_text(json.dumps({"type": "user", "cwd": "/work", "timestamp": stamp(days * 86400),
                                         "message": {"role": "user", "content": "Plan"}}) + "\n")
        at = time.time() - days * 86400
        os.utime(transcript, (at, at))
    sessions = fleetd.SessionTracker(since=time.time() - 60 * 86400).scan()
    assert set(sessions) == {"recent"} and sessions["recent"]["status"] == "stopped"


def test_session_without_commits_records_why_base_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "collect_workspace", lambda cwd: ({"head": None}, None))
    transcript = fleetd.Transcript(tmp_path / "unborn.jsonl", "claude")
    transcript.cwd = str(tmp_path)
    workspace, reason = fleetd.session_workspace(transcript, "working")
    assert reason is None and workspace["base"] is None
    assert workspace["base_reason"] == "repository has no commits"


def stamp(seconds_ago: float) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return moment.isoformat().replace("+00:00", "Z")


def test_a_session_is_as_old_as_its_last_record_not_its_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "CLAUDE_PROJECTS_DIRECTORY", tmp_path / "projects")
    monkeypatch.setattr(fleetd, "CODEX_SESSIONS_DIRECTORY", tmp_path / "codex")
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    transcript = tmp_path / "projects" / "-work" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    records = [
        {"type": "user", "cwd": "/work", "timestamp": stamp(3 * 3600),
         "message": {"role": "user", "content": "Write the ADR"}},
        {"type": "assistant", "cwd": "/work", "timestamp": stamp(3 * 3600 - 60),
         "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "Done."}]}},
    ]
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
    os.utime(transcript)  # touched just now without a new record, as happens to open sessions
    assert fleetd.SessionTracker().scan() == {}
    [session] = fleetd.SessionTracker(since=time.time() - 4 * 3600).scan().values()
    assert session["status"] == "stopped"
    assert time.time() - session["updated_at"] > 3 * 3600 - 120


def test_a_message_that_ends_on_a_question_carries_it_as_its_ask():
    text = ("Step 4 gives legacy pages one way to start up.\n\n```js\nif (x) { ask()? }\n```\n\n"
            "I'd measure that first. **Should I send that sample to fleet?**")
    [event] = fleetd.ClaudeParser().parse({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    assert event["summary"].startswith("Step 4 gives legacy pages")
    assert event["ask"] == "Should I send that sample to fleet?"
    [plain] = fleetd.ClaudeParser().parse({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}})
    assert "ask" not in plain


def test_a_job_id_prefix_expands_to_the_one_job_it_names(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path)
    for job_id in ("77ee5565-14cb-4d6c-b3d5-b5b878f48169", "a1c3e9"):
        (tmp_path / job_id).mkdir()
        (tmp_path / job_id / "job.json").write_text("{}")
    assert fleetd.expand_job_id("77ee5565") == "77ee5565-14cb-4d6c-b3d5-b5b878f48169"
    assert fleetd.expand_job_id("a1c3e9") == "a1c3e9"
    assert fleetd.expand_job_id("ffff") == "ffff"


def test_a_session_moved_by_mv_reports_that_project_not_its_repository(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fleetd, "CLAUDE_PROJECTS_DIRECTORY", tmp_path / "projects")
    monkeypatch.setattr(fleetd, "CODEX_SESSIONS_DIRECTORY", tmp_path / "codex")
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    monkeypatch.setattr(fleetd, "SESSION_PROJECTS_PATH", tmp_path / "session-projects.json")
    transcript = tmp_path / "projects" / "-work-agent-fleet" / "343fc897-5ed8.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "user", "cwd": "/work/agent-fleet", "timestamp": stamp(60),
                                      "message": {"role": "user", "content": "Plan the invoices"}}) + "\n")
    [session] = fleetd.SessionTracker().scan().values()
    assert session["project"] == "agent-fleet"
    fleetd.command_move(fleetd.argparse.Namespace(job="343fc897", project="invoice-training"))
    assert json.loads(capsys.readouterr().out)["id"] == "343fc897-5ed8"
    [session] = fleetd.SessionTracker().scan().values()
    assert session["project"] == "invoice-training"
