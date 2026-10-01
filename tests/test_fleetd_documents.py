"""Recorded job documents may only be read from approved host directories."""

import argparse
import json
import sys
from pathlib import Path

import pytest

from fleet.remote import fleetd


def test_trace_availability_survives_pruning(job):
    record, directory = job
    record.update(permission="read-only", created_at=1, updated_at=2)
    trace = directory / "events.jsonl"
    trace.write_text('{"kind":"text","summary":"working"}\n')
    signature = fleetd.job_signature(directory)
    assert fleetd.job_summary(record, 1)["trace"] == {"path": str(trace), "availability": "available",
        "size": trace.stat().st_size, "mtime": trace.stat().st_mtime_ns, "raw": []}
    trace.unlink()
    assert fleetd.job_signature(directory) != signature
    assert fleetd.job_summary(record, 1)["trace"] == {"path": str(trace), "availability": "unavailable",
        "size": None, "mtime": None, "raw": []}


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Path]:
    home = tmp_path / "fleet"
    directory = home / "jobs" / "job1"
    directory.mkdir(parents=True)
    monkeypatch.setattr(fleetd, "FLEET_HOME", home)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", home / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", home / "config.json")
    record = {"id": "job1", "project": "project", "agent": "codex", "description": "work",
              "cwd": str(tmp_path / "work"), "steps": [fleetd.make_step(0, "Write notes", "Write notes")],
              "written_documents": []}
    (directory / "job.json").write_text(json.dumps(record))
    return record, directory


def read(job: dict, directory: Path, path: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    job["written_documents"] = [{"path": str(path), "step": 0}]
    (directory / "job.json").write_text(json.dumps(job))
    fleetd.command_read(argparse.Namespace(job="job1", document="file-0"))
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("kind", ["symlink", "not markdown"])
def test_read_refuses_recorded_path_outside_approved_roots(
    job: tuple[dict, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str,
) -> None:
    record, directory = job
    secret = tmp_path / "secret.md"
    secret.write_text("private content")
    if kind == "symlink":
        path = directory / "link.md"
        path.symlink_to(secret)
    else:
        path = tmp_path / "secret.txt"
        path.write_text("private content")
    with pytest.raises(SystemExit):
        read(record, directory, path, capsys)
    assert "outside approved document roots" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["traversal", "absolute"])
def test_markdown_the_agent_wrote_is_readable_wherever_it_lives(
    job: tuple[dict, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str,
) -> None:
    record, directory = job
    probe = tmp_path / "review" / "probe.md"
    probe.parent.mkdir()
    probe.write_text("# Probe")
    path = directory / ".." / ".." / ".." / "review" / "probe.md" if kind == "traversal" else probe
    assert read(record, directory, path, capsys)["content"] == "# Probe"


def test_outbox_symlink_reads_only_inside_the_projects_library_root(
    job: tuple[dict, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent did not write an outbox symlink, so it resolves only into a root: the project's library is one."""
    record, directory = job
    library = tmp_path / "repo"
    (library / "docs").mkdir(parents=True)
    (library / "docs" / "plan.md").write_text("# Plan")
    (directory / "outbox").mkdir()
    (directory / "outbox" / "plan.md").symlink_to(library / "docs" / "plan.md")
    (directory / "job.json").write_text(json.dumps(record))
    with pytest.raises(SystemExit):
        fleetd.command_read(argparse.Namespace(job="job1", document="outbox-plan.md"))
    assert "outside approved document roots" in capsys.readouterr().out

    client_config = tmp_path / "client.json"
    client_config.write_text(json.dumps({"libraries": {"project": str(library), "other": str(tmp_path)}}))
    monkeypatch.setenv("FLEET_CONFIG", str(client_config))
    fleetd.command_read(argparse.Namespace(job="job1", document="outbox-plan.md"))
    assert json.loads(capsys.readouterr().out)["content"] == "# Plan"
    record["project"] = "unrelated"
    (directory / "job.json").write_text(json.dumps(record))
    with pytest.raises(SystemExit):
        fleetd.command_read(argparse.Namespace(job="job1", document="outbox-plan.md"))


def test_read_allows_job_directory_and_explicit_root(
    job: tuple[dict, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    record, directory = job
    report = directory / "outbox" / "report.md"
    report.parent.mkdir()
    report.write_text("# Job report")
    assert read(record, directory, report, capsys)["content"] == "# Job report"

    approved = tmp_path / "approved"
    approved.mkdir()
    artifact = approved / "artifact.md"
    artifact.write_text("# Copied artifact")
    fleetd.CONFIG_PATH.write_text(json.dumps({"document_roots": [str(approved)]}))
    assert read(record, directory, artifact, capsys)["content"] == "# Copied artifact"


@pytest.mark.parametrize("where", ["cwd", "outside the cwd"])
def test_run_step_copies_written_document_for_read(
    job: tuple[dict, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], where: str,
) -> None:
    record, directory = job
    cwd = Path(record["cwd"])
    cwd.mkdir()
    source = cwd / "notes.md" if where == "cwd" else tmp_path / "ralph" / "v2-review" / "notes.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Agent notes")
    event = {"type": "item.completed", "item": {"type": "file_change", "changes":
             [{"path": str(source), "kind": "add"}]}}
    script = f"import json; print(json.dumps({event!r}))"
    monkeypatch.setattr(fleetd, "agent_command", lambda *_args: [sys.executable, "-c", script])

    fleetd.run_step(record, {"index": 0, "title": "Write notes"})
    saved = fleetd.read_job("job1")
    document = fleetd.job_documents(saved)[0]
    assert document["id"] == "file-0"
    assert document["path"] == str(source)
    fleetd.command_read(argparse.Namespace(job="job1", document="file-0"))
    assert json.loads(capsys.readouterr().out)["content"] == "# Agent notes"
    assert list((directory / "artifacts").glob("*"))
    source.unlink()   # the copy outlives the worktree
    fleetd.command_read(argparse.Namespace(job="job1", document="file-0"))
    assert json.loads(capsys.readouterr().out)["content"] == "# Agent notes"


def test_run_step_does_not_copy_a_recorded_symlink(
    job: tuple[dict, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record, directory = job
    cwd = Path(record["cwd"])
    cwd.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("private content")
    path = cwd / "link.md"
    path.symlink_to(secret)
    record["written_documents"] = [{"path": str(path), "step": 0}]
    (directory / "job.json").write_text(json.dumps(record))
    monkeypatch.setattr(fleetd, "agent_command", lambda *_args: [sys.executable, "-c", "pass"])

    fleetd.run_step(record, {"index": 0, "title": "Write notes"})
    with pytest.raises(SystemExit):
        fleetd.command_read(argparse.Namespace(job="job1", document="file-0"))
    assert "outside approved document roots" in capsys.readouterr().out


def test_batch12_outbox_lists_every_file_including_empty_and_nested(job, capsys):
    record, directory = job
    outbox = directory / 'outbox'
    (outbox / 'nested').mkdir(parents=True)
    files = {'report.md': b'# Report', 'notes.txt': b'notes', 'data.json': b'{}',
             'nested/archive.zip': b'PK\x03\x04', 'empty.csv': b''}
    for name, content in files.items():
        (outbox / name).write_bytes(content)
    documents = fleetd.job_documents(record)
    assert {doc['name'] for doc in documents} == set(files)
    assert next(doc for doc in documents if doc['name'] == 'empty.csv')['size'] == 0
    fleetd.command_read(argparse.Namespace(job='job1', document='outbox-nested/archive.zip'))
    reply = json.loads(capsys.readouterr().out)
    assert 'fleet pull' in reply['content'] and 'cannot preview' in reply['content']
    # Listing arbitrary extensions must not grant access through an outbox symlink.
    secret = directory.parent.parent.parent / 'secret.bin'
    secret.write_bytes(b'private')
    (outbox / 'secret.bin').symlink_to(secret)
    with pytest.raises(SystemExit):
        fleetd.command_read(argparse.Namespace(job='job1', document='outbox-secret.bin'))
    assert 'outside approved document roots' in capsys.readouterr().out
