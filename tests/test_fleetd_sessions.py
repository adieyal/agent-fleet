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
