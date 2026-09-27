"""Only documents under a job's approved roots are offered to readers."""

from pathlib import Path

from fleet.remote import fleetd


def test_recorded_documents_stay_under_job_or_working_directory(tmp_path: Path, monkeypatch) -> None:
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
    job = {"id": "job-1", "cwd": str(work), "steps": [], "written_documents": [
        {"path": str(inside), "step": 0}, {"path": str(outside), "step": 0}]}
    assert [document["id"] for document in fleetd.job_documents(job)] == ["file-0"]
