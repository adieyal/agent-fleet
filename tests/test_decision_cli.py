"""Agents record decisions with the principle they relied on; listing by project or epic, newest first."""

import json
from types import SimpleNamespace

import pytest

from fleet import cli, composition

GUIDANCE = dict(project="p", epic=None, constitution=dict(path="constitution.md", revision="abc", version=3),
                charter=None)


@pytest.fixture(autouse=True)
def outside_a_job(monkeypatch):
    # These tests may themselves run inside a fleet job.
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)


@pytest.fixture
def tree(project_id):
    work = composition.open_work()
    epic = work.add(project=project_id, title="Transcriber", goal="Read", kind="epic", actor="user")
    task = work.add(project=project_id, title="Task", goal="Ship", parent=epic.id, actor="user")
    other = work.add(project=project_id, title="Elsewhere", goal="Other", actor="user")
    return SimpleNamespace(project=project_id, epic=epic, task=task, other=other)


def run_for(work_item: str, guidance: dict | None, key: str = "job"):
    return composition.open_execution().dispatch(work_item, host="h", runtime="codex", actor="user", reason="Go",
        idempotency_key=key, remote_job_id=key, guidance=guidance,
        payload=dict(cwd="/repo", arguments=[], steps=[dict(prompt="Go")], context=None, hold=False)).run


def record(capsys, work_item: str, *extra: str) -> dict:
    capsys.readouterr()
    cli.main(["decision", "record", "--work-item", work_item, "--question", "Loosen the check?", "--answer", "No",
              "--principle", "Constitution: anti-goal 2", "--actor", "claude", *extra])
    return json.loads(capsys.readouterr().out)


def test_record_with_a_run_carries_its_pinned_guidance(tree, capsys):
    run = run_for(tree.task.id, GUIDANCE)
    decision = record(capsys, tree.task.id, "--run", run.id, "--context", "flagged line 4")
    assert (decision["principle"], decision["source_run"], decision["guidance"]) == (
        "Constitution: anti-goal 2", run.id, GUIDANCE)
    assert decision["activation"] is None and decision["affected_work_items"] == [tree.task.id]
    stored = composition.open_decisions().get(decision["id"])
    assert stored.guidance == GUIDANCE and stored.context == "flagged line 4"


def test_the_jobs_own_run_is_found_from_fleet_job_id(tree, capsys, monkeypatch):
    run = run_for(tree.task.id, GUIDANCE, key="job-42")
    monkeypatch.setenv("FLEET_JOB_ID", "job-42")
    assert record(capsys, tree.task.id)["source_run"] == run.id
    monkeypatch.setenv("FLEET_JOB_ID", "unrecorded")
    decision = record(capsys, tree.task.id)
    assert decision["source_run"] is None and decision["guidance"] is None


def test_without_a_run_the_guidance_version_is_unknown(tree, capsys):
    decision = record(capsys, tree.task.id)
    assert decision["source_run"] is None and decision["guidance"] is None


def test_unguided_run_records_no_guidance(tree, capsys):
    run = run_for(tree.task.id, None)
    assert record(capsys, tree.task.id, "--run", run.id)["guidance"] is None


@pytest.mark.parametrize("field", ["--principle", "--question"])
def test_blank_principle_or_question_is_refused(tree, capsys, field):
    arguments = ["decision", "record", "--work-item", tree.task.id, "--question", "Q", "--answer", "A",
                 "--principle", "P", "--actor", "claude"]
    arguments[arguments.index(field) + 1] = "  "
    with pytest.raises(SystemExit):
        cli.main(arguments)
    assert "question and principle are required" in capsys.readouterr().err


def test_a_run_from_another_project_is_refused(tree, capsys):
    elsewhere = composition.open_workspace().edit_registry(lambda registry: registry.create("q")).id
    item = composition.open_work().add(project=elsewhere, title="Q", goal="Q", actor="user")
    run = run_for(item.id, None)
    with pytest.raises(SystemExit):
        record(capsys, tree.task.id, "--run", run.id)
    assert "is not in" in capsys.readouterr().err
    assert composition.open_decisions().list() == []


def test_list_by_project_and_epic_newest_first_with_unknown_principles(tree, capsys):
    first = record(capsys, tree.task.id)
    second = record(capsys, tree.epic.id)
    outside = record(capsys, tree.other.id)
    cli.main(["attention", "add", "Ship it?", "--project", tree.project, "--work-item", tree.task.id,
              "--kind", "decision", "--owner", "user", "--source", "manual", "--source-reference", "r",
              "--context-reference", "README.md", "--actor", "user"])
    attention = json.loads(capsys.readouterr().out)["id"]
    cli.main(["answer", attention, "Yes"])
    answered = json.loads(capsys.readouterr().out)["id"]

    cli.main(["decision", "list", "--epic", tree.epic.id, "--json"])
    assert [entry["id"] for entry in json.loads(capsys.readouterr().out)] == [answered, second["id"], first["id"]]
    cli.main(["decision", "list", "--project", "p", "--json"])
    entries = json.loads(capsys.readouterr().out)
    assert [entry["id"] for entry in entries] == [answered, outside["id"], second["id"], first["id"]]
    assert entries[3]["work_items"] == [{"id": tree.task.id, "title": "Task"}]

    cli.main(["decision", "list", "--epic", tree.epic.id])
    output = capsys.readouterr().out
    assert output.index("Answer: Yes") < output.index("Principle: unknown") < output.index("Principle: Constitution")
    assert f"user on Task ({tree.task.id})" in output and "Guidance: unknown" in output
    cli.main(["status", tree.project])
    assert "Principle: unknown" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["decision", "list", "--epic", tree.task.id])
    assert "is a task, not an epic" in capsys.readouterr().err


def test_list_with_no_decisions(tree, capsys):
    cli.main(["decision", "list", "--project", tree.project])
    assert capsys.readouterr().out == "No decisions recorded.\n"
