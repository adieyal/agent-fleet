"""Documents outside approved roots cannot be read, unless the agent wrote them itself."""

import argparse
from pathlib import Path

import pytest

from fleet_worker import fleetd


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
    job = {"id": "job-1", "project": "p", "agent": "codex", "description": "d", "cwd": str(work), "steps": [],
           "written_documents": [
        {"path": str(inside), "step": 0}, {"path": str(outside), "step": 0}]}
    assert "file-0" in [document["id"] for document in fleetd.job_documents(job)]
    monkeypatch.setattr(fleetd, "read_job", lambda _job_id: job)
    # the agent wrote outside.md itself, so it reads wherever it lives; the outbox link it did not write does not
    fleetd.command_read(argparse.Namespace(job="job-1", document="file-1"))
    assert '"content": "outside"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        fleetd.command_read(argparse.Namespace(job="job-1", document="outbox-linked.md"))
    assert "outside approved document roots" in capsys.readouterr().out
