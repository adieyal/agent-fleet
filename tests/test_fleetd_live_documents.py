"""A job's documents are readable from the moment it exists and follow the agent's writes live."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fleet.remote import fleetd


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "fleet"
    monkeypatch.setattr(fleetd, "FLEET_HOME", home)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", home / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", home / "config.json")
    return home


def fleetd_cli(home: Path, *arguments: str) -> dict:
    environment = {**os.environ, "FLEET_HOME": str(home)}
    result = subprocess.run([sys.executable, fleetd.__file__, *arguments], env=environment,
                            capture_output=True, text=True)
    output = json.loads(result.stdout)
    assert result.returncode == 0, output
    return output


def read_document(job_id: str, document_id: str, capsys: pytest.CaptureFixture[str]) -> dict:
    try:
        fleetd.command_read(argparse.Namespace(job=job_id, document=document_id))
    finally:
        output = capsys.readouterr().out
    return json.loads(output)


def eventually(check, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return check()
        except (AssertionError, SystemExit):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def test_step_briefs_and_context_are_documents_when_the_job_is_created(tmp_path: Path, home: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps(["Survey the parser and write notes.md", {"prompt": "Fix what you found",
                                                                          "title": "Fix"}]))
    created = fleetd_cli(home, "create", "--id", "job1", "--project", "example", "--description", "probe",
                         "--agent", "codex", "--cwd", str(work), "--steps-file", str(steps), "--hold")
    briefs = [(document["id"], document["kind"], document["name"], document["step"])
              for document in created["documents"]]
    assert briefs == [("brief-0", "brief", "Step 1 brief", 0), ("brief-1", "brief", "Step 2 brief", 1)]
    assert fleetd_cli(home, "read", "job1", "brief-1")["content"] == "Fix what you found"

    context = home / "jobs" / "job1" / "context"
    (context / "notes").mkdir()
    (context / "notes" / "brief.md").write_text("# Background")
    (context / "data.csv").write_text("a,b")
    documents = fleetd_cli(home, "show", "job1")["documents"]
    assert [document["id"] for document in documents if document["kind"] == "context"] == ["context-notes/brief.md"]
    assert fleetd_cli(home, "read", "job1", "context-notes/brief.md")["content"] == "# Background"

    more = tmp_path / "more.json"
    more.write_text(json.dumps(["Write the report"]))
    added = fleetd_cli(home, "add", "job1", "--steps-file", str(more), "--hold")
    assert [document["name"] for document in added["documents"] if document["kind"] == "brief"][-1] == "Step 3 brief"


def test_context_symlink_out_of_the_job_is_refused(tmp_path: Path, home: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text("private")
    context = home / "jobs" / "job1" / "context"
    context.mkdir(parents=True)
    (context / "leak.md").symlink_to(secret)
    (home / "jobs" / "job1" / "job.json").write_text(json.dumps({"id": "job1", "project": "p", "agent": "codex",
                                                                 "description": "d", "steps": []}))
    with pytest.raises(SystemExit):
        fleetd.command_read(argparse.Namespace(job="job1", document="context-leak.md"))
    assert "outside approved document roots" in capsys.readouterr().out


AGENT = """
import json, sys, time
from pathlib import Path
notes, gate = Path(sys.argv[1]), Path(sys.argv[2])
def say(record):
    print(json.dumps(record), flush=True)
def wait(name):
    while not (gate / name).exists():
        time.sleep(0.02)
# Claude reports a write before it happens; the file lands with the tool result.
say({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Write",
     "input": {"file_path": str(notes), "content": "x"}}]}})
notes.write_text("# Notes\\n\\nfirst draft")
say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}})
wait("first-read")
say({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Edit",
     "input": {"file_path": str(notes)}}]}})
notes.write_text("# Notes\\n\\nsecond draft")
say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}})
wait("second-read")
notes.write_text("# Notes\\n\\nfinal")
say({"type": "result", "subtype": "success", "result": "done"})
"""


def test_written_markdown_is_readable_while_the_step_runs(tmp_path: Path, home: Path,
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    work, gate = tmp_path / "work", tmp_path / "gate"
    work.mkdir()
    gate.mkdir()
    notes = work / "notes.md"
    directory = home / "jobs" / "job1"
    directory.mkdir(parents=True)
    record = {"id": "job1", "project": "p", "agent": "claude", "description": "d", "cwd": str(work),
              "steps": [{"index": 0, "title": "Write notes"}]}
    (directory / "job.json").write_text(json.dumps(record))
    monkeypatch.setattr(fleetd, "agent_command",
                        lambda *_: [sys.executable, "-c", AGENT, str(notes), str(gate)])
    runner = threading.Thread(target=fleetd.run_step, args=(record, record["steps"][0]))

    def reads(content: str) -> dict:
        document = read_document("job1", "file-0", capsys)
        assert document["content"] == content
        return document

    runner.start()
    try:
        assert eventually(lambda: reads("# Notes\n\nfirst draft"))["path"] == str(notes)
        (gate / "first-read").touch()
        eventually(lambda: reads("# Notes\n\nsecond draft"))
        assert runner.is_alive()
    finally:
        (gate / "first-read").touch()
        (gate / "second-read").touch()
        runner.join(timeout=10)
    # the final copy at step end still catches writes the stream never mentioned
    assert read_document("job1", "file-0", capsys)["content"] == "# Notes\n\nfinal"


def test_a_symlink_the_agent_writes_through_is_never_copied_live(tmp_path: Path, home: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("private")
    (work / "link.md").symlink_to(secret)
    directory = home / "jobs" / "job1"
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"id": "job1", "steps": []}))
    mirror = fleetd.DocumentMirror("job1")
    mirror.watch(fleetd.record_written_documents("job1", str(work), ["link.md"], 0))
    mirror.sync()
    assert not (directory / "artifacts").exists()
    assert "artifact" not in fleetd.read_job("job1")["written_documents"][0]


def stream_messages(home: Path):
    environment = {**os.environ, "FLEET_HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / "claude"),
                   "CODEX_HOME": str(home / "codex")}
    process = subprocess.Popen([sys.executable, fleetd.__file__, "stream", "--interval", "0.05",
                                "--heartbeat", "0.2"], env=environment, stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None

    def next_job(timeout: float = 5) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(process.stdout.readline())
            if message["type"] == "job":
                return message["job"]
        raise AssertionError("no job message")
    return process, next_job


@pytest.mark.parametrize("folder", ["outbox/drafts", "artifacts", "context"])
def test_the_stream_re_emits_a_job_when_only_a_document_changes(home: Path, folder: str) -> None:
    directory = home / "jobs" / "job1"
    (directory / folder).mkdir(parents=True)
    document = directory / folder / "file-0.md"
    document.write_text("# one")
    job = {"id": "job1", "project": "p", "agent": "claude", "description": "d", "cwd": str(home),
           "permission": "default", "created_at": time.time(), "updated_at": time.time(),
           "steps": [{"index": 0, "title": "t", "prompt": "p", "status": "running", "started_at": time.time()}],
           "written_documents": [{"path": "/elsewhere/file-0.md", "step": 0, "artifact": str(document)}]
           if folder == "artifacts" else []}
    (directory / "job.json").write_text(json.dumps(job))
    process, next_job = stream_messages(home)
    try:
        [before] = next_job()["documents"]
        with open(document, "a") as handle:   # an in-place edit leaves the folder's own mtime alone
            handle.write("\n\nmore text")
        [after] = next_job()["documents"]
        assert after["id"] == before["id"] and after["size"] > before["size"]
    finally:
        process.kill()
        process.wait(timeout=5)


def test_local_notes_are_never_documents(tmp_path: Path, home: Path) -> None:
    """CLAUDE.local.md and other *.local.md files are private: not recorded when written, not listed in the outbox."""
    parser = fleetd.ClaudeParser()
    [event] = parser.parse({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1",
                            "name": "Edit", "input": {"file_path": "/work/CLAUDE.local.md"}}]}})
    assert "paths" not in event
    directory = home / "jobs" / "job1"
    (directory / "outbox").mkdir(parents=True)
    (directory / "outbox" / "notes.local.md").write_text("private")
    (directory / "outbox" / "report.md").write_text("# Report")
    job = {"id": "job1", "steps": [], "written_documents": []}
    assert [document["id"] for document in fleetd.job_documents(job)] == ["outbox-report.md"]
