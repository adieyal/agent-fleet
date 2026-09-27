"""Recorded job documents may only be read from approved host directories."""

import argparse
import json
from pathlib import Path

import pytest

from fleet.remote import fleetd


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Path]:
    home = tmp_path / "fleet"
    directory = home / "jobs" / "job1"
    directory.mkdir(parents=True)
    monkeypatch.setattr(fleetd, "FLEET_HOME", home)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", home / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", home / "config.json")
    record = {"id": "job1", "project": "project", "agent": "codex", "description": "work",
              "cwd": str(tmp_path / "work"), "steps": [], "written_documents": []}
    (directory / "job.json").write_text(json.dumps(record))
    return record, directory


def read(job: dict, directory: Path, path: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    job["written_documents"] = [{"path": str(path), "step": 0}]
    (directory / "job.json").write_text(json.dumps(job))
    fleetd.command_read(argparse.Namespace(job="job1", document="file-0"))
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("kind", ["traversal", "symlink", "absolute"])
def test_read_refuses_recorded_path_outside_approved_roots(
    job: tuple[dict, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str,
) -> None:
    record, directory = job
    secret = tmp_path / "secret.md"
    secret.write_text("private content")
    if kind == "traversal":
        path = directory / ".." / ".." / ".." / "secret.md"
    elif kind == "symlink":
        path = directory / "link.md"
        path.symlink_to(secret)
    else:
        path = secret
    with pytest.raises(SystemExit):
        read(record, directory, path, capsys)
    assert "outside approved document roots" in capsys.readouterr().out


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
