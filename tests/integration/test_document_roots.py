"""Recorded documents outside approved roots cannot be read."""

import argparse
from pathlib import Path

import pytest

from fleet.remote import fleetd


def test_recorded_documents_outside_roots_are_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    jobs = tmp_path / "jobs"
    job_dir = jobs / "job-1"
    outbox = job_dir / "outbox"
    outbox.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    inside = work / "inside.md"
    inside.write_text("inside")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (outbox / "linked.md").symlink_to(outside)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", jobs)
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    job = {"id": "job-1", "cwd": str(work), "steps": [], "written_documents": [
        {"path": str(inside), "step": 0}, {"path": str(outside), "step": 0}]}
    assert "file-0" in [document["id"] for document in fleetd.job_documents(job)]
    monkeypatch.setattr(fleetd, "read_job", lambda _job_id: job)
    for document_id in ("file-1", "outbox-linked.md"):
        with pytest.raises(SystemExit):
            fleetd.command_read(argparse.Namespace(job="job-1", document=document_id))
        assert "outside approved document roots" in capsys.readouterr().out
