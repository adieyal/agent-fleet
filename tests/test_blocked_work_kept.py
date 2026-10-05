"""Audit 1 batch 1: blocked or failed work stays visible after the horizon, and `fleet rm` won't delete it unasked."""
from fleet import transport
import argparse
import json
import time

import pytest

from fleet_cli import cli
from fleet.remote import fleetd
from fleet.transport import Host

DAY = 24 * 3600
STEP_STATUS = {"done": "done", "failed": "failed", "blocked": "blocked", "queued": "pending"}


def write_job(jobs, job_id, status, *, age=0.0, outbox=()):
    steps = [{**fleetd.make_step(0, "work", None), "status": STEP_STATUS.get(status, "done")}]
    job = {"id": job_id, "agent": "claude", "project": "p", "description": job_id, "cwd": str(jobs),
           "permission": "read-only", "created_at": 1, "updated_at": time.time() - age, "runner_pid": None,
           "steps": steps, **({"cancelled": True} if status == "cancelled" else {})}
    (jobs / job_id / "outbox").mkdir(parents=True, exist_ok=True)
    (jobs / job_id / "job.json").write_text(json.dumps(job))
    for name in outbox:
        (jobs / job_id / "outbox" / name).write_text(name)
    (jobs / job_id / "result-0.md").write_text("result")
    assert fleetd.derive_status(job) == status


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    return tmp_path / "jobs"


def test_only_done_cancelled_and_lost_jobs_age_out_of_ls(jobs, monkeypatch):
    for status in ("done", "cancelled", "failed", "blocked"):
        write_job(jobs, f"old-{status}", status, age=2 * DAY)
    write_job(jobs, "new-done", "done")
    listed = []
    monkeypatch.setattr(fleetd, "emit", listed.append)
    fleetd.command_list(argparse.Namespace(all=False, since_hours=24, events=0))
    assert sorted(job["id"] for job in listed[0]["jobs"]) == ["new-done", "old-blocked", "old-failed"]


def test_the_stream_keeps_old_blocked_work_and_says_why_a_job_left(jobs, monkeypatch):
    for status in ("done", "failed", "blocked"):
        write_job(jobs, f"old-{status}", status, age=2 * DAY)
    write_job(jobs, "ageing", "done")
    write_job(jobs, "deleted", "done")
    messages = []
    monkeypatch.setattr(fleetd, "emit", messages.append)
    monkeypatch.setattr(fleetd.SessionTracker, "scan", lambda self: {})
    monkeypatch.setattr(fleetd.PipelineTracker, "scan", lambda self: [])
    monkeypatch.setattr(fleetd.os, "close", lambda _: None)
    passes = []

    def between_passes(_):
        passes.append(None)
        if len(passes) == 1:   # one job ages out, another is deleted
            record = json.loads((jobs / "ageing" / "job.json").read_text())
            record["updated_at"] = time.time() - 2 * DAY
            (jobs / "ageing" / "job.json").write_text(json.dumps(record))
            fleetd.shutil.rmtree(jobs / "deleted")
        else:
            raise BrokenPipeError()
    monkeypatch.setattr(fleetd.time, "sleep", between_passes)
    fleetd.command_stream(argparse.Namespace(since_hours=24, events=0, session_interval=1, heartbeat=1, interval=1))
    shown = {message["job"]["id"] for message in messages if message.get("type") == "job"}
    assert shown == {"old-failed", "old-blocked", "ageing", "deleted"}
    removed = {message["id"]: message["reason"] for message in messages if message.get("type") == "removed"}
    assert removed == {"ageing": "aged", "deleted": "deleted"}


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_FLEETD_PATH", fleetd.__file__)
    monkeypatch.setenv("FLEET_REMOTE_HOME", str(tmp_path / "worker"))
    host = Host("worker", None)
    monkeypatch.setattr(transport, "host_by_name", lambda name: host)
    return tmp_path / "worker" / "jobs"


@pytest.mark.parametrize("status", ["blocked", "queued"])
def test_rm_refuses_unfinished_work_unless_forced(worker, status, capsys):
    write_job(worker, "job1", status, outbox=("findings.md",))
    with pytest.raises(SystemExit):
        cli.main(["rm", "worker:job1"])
    assert f"job is {status}, not finished" in " ".join(capsys.readouterr().err.split())
    assert (worker / "job1" / "outbox" / "findings.md").exists()
    cli.main(["rm", "--force", "worker:job1"])
    assert not (worker / "job1").exists()
    assert "for good" in capsys.readouterr().out


def test_rm_of_a_finished_job_prints_what_it_deleted(worker, capsys):
    write_job(worker, "job1", "failed", outbox=("findings.md", "chart.png"))
    cli.main(["rm", "worker:job1"])
    assert not (worker / "job1").exists()
    assert " ".join(capsys.readouterr().out.split()) == (
        "removed failed job worker:job1 for good: its directory, 2 outbox file(s) and 1 step result(s)")


def test_rm_help_says_it_cannot_be_undone(capsys):
    with pytest.raises(SystemExit):
        cli.main(["rm", "--help"])
    assert "cannot be undone" in capsys.readouterr().out
