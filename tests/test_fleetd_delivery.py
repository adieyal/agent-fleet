import argparse
import json
import sys

import pytest

from fleet.remote import fleetd


def test_delivery_key_applies_one_resumed_step(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "job1"
    directory.mkdir()
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path)
    job = {"id": "job1", "agent": "claude", "session_id": "session1", "steps": [],
           "runner_pid": None, "cancelled": False, "permission": "default", "project": "p",
           "description": "Delivery check"}
    (directory / "job.json").write_text(json.dumps(job))
    starts = []
    def run_step(job, step):
        command = fleetd.agent_command(job, step, job["session_id"])
        assert command[command.index("--resume") + 1] == "session1"
        starts.append(step["prompt"])
        return {"ok": True, "summary": "Received", "text": "Received"}
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(fleetd, "run_step", run_step)
    monkeypatch.setattr(fleetd.signal, "signal", lambda *args: None)
    monkeypatch.setattr(fleetd, "launch_runner", fleetd.run_job)
    arguments = argparse.Namespace(job="job1", key="answer1", schema_version=1)
    from io import StringIO
    for _ in range(2):
        monkeypatch.setattr(sys, "stdin", StringIO("Proceed"))
        fleetd.command_deliver(arguments)
        assert json.loads(capsys.readouterr().out)["status"] == "applied"
    result = fleetd.read_job("job1")
    assert len(result["steps"]) == 1
    assert result["steps"][0]["prompt"] == "Proceed"
    assert result["session_id"] == "session1"
    assert starts == ["Proceed"]
    monkeypatch.setattr(sys, "stdin", StringIO("Different"))
    with pytest.raises(SystemExit):
        fleetd.command_deliver(arguments)
    assert "changed payload" in capsys.readouterr().out


def test_delivery_does_not_acknowledge_a_runner_that_never_started(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "job1"
    directory.mkdir()
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path)
    (directory / "job.json").write_text(json.dumps({"id": "job1", "session_id": "session1",
        "steps": [], "runner_pid": None, "cancelled": False}))
    monkeypatch.setattr(fleetd, "launch_runner", lambda job: None)
    from io import StringIO
    for _ in range(2):
        monkeypatch.setattr(sys, "stdin", StringIO("Proceed"))
        with pytest.raises(SystemExit):
            fleetd.command_deliver(argparse.Namespace(job="job1", key="k", schema_version=1))
        assert "not started" in capsys.readouterr().out
    assert len(fleetd.read_job("job1")["steps"]) == 1
