"""fleetd holds an agent's decisions on its job, once per id, and reports them in the job's summary."""

import argparse
import json
import sys
from io import StringIO

import pytest

from fleet_worker import fleetd

DECISION = {"id": "d1", "work_item": "w1", "question": "Loosen the check?", "answer": "No",
            "principle": "Constitution: anti-goal 2", "actor": "claude", "context": "", "time": 1700000000.5}


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path)
    (tmp_path / "job1").mkdir()
    (tmp_path / "job1" / "job.json").write_text(json.dumps({
        "id": "job1", "project": "p", "description": "Decide", "agent": "claude", "cwd": str(tmp_path),
        "permission": "default", "created_at": 1.0, "steps": [], "cancelled": False}))
    return "job1"


def hold(monkeypatch, capsys, job: str, decision: dict) -> dict:
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(decision)))
    fleetd.command_decision(argparse.Namespace(job=job, schema_version=1))
    return json.loads(capsys.readouterr().out)


def test_a_decision_is_held_once_per_id_and_reported_in_the_summary(job, monkeypatch, capsys):
    for _ in range(2):
        assert hold(monkeypatch, capsys, job, DECISION) == {"schema_version": 1, "id": "d1", "status": "held"}
    second = {**DECISION, "id": "d2", "question": "Split the module?"}
    hold(monkeypatch, capsys, job, second)
    stored = fleetd.read_job(job)
    assert stored["decisions"] == [DECISION, second]
    assert fleetd.job_summary(stored, 0)["decisions"] == [DECISION, second]
    events = [event for event in fleetd.read_events(job, 10) if event.get("status") == "decision"]
    assert [event["summary"] for event in events] == ["decision recorded: Loosen the check?",
                                                      "decision recorded: Split the module?"]


def test_the_same_id_with_another_decision_is_refused(job, monkeypatch, capsys):
    hold(monkeypatch, capsys, job, DECISION)
    with pytest.raises(SystemExit):
        hold(monkeypatch, capsys, job, {**DECISION, "answer": "Yes"})
    assert "changed payload" in capsys.readouterr().out
    assert fleetd.read_job(job)["decisions"] == [DECISION]


@pytest.mark.parametrize("change", [{"principle": " "}, {"question": ""}, {"time": "now"}, {"work_item": None}])
def test_an_incomplete_decision_is_refused(job, monkeypatch, capsys, change):
    with pytest.raises(SystemExit):
        hold(monkeypatch, capsys, job, {**DECISION, **change})
    assert "decision" in json.loads(capsys.readouterr().out)["error"]
    assert "decisions" not in fleetd.read_job(job)


def test_a_job_without_decisions_reports_none(job):
    assert fleetd.job_summary(fleetd.read_job(job), 0)["decisions"] == []
