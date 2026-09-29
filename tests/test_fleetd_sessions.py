import datetime
import json
import os
import time

from fleet.remote import fleetd


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
    [session] = fleetd.SessionTracker().scan().values()
    assert session["status"] == "idle"
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
