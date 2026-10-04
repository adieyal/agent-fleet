"""Naming the work a step serves: `--step-work-item` on fleet send/add, a `work_item` field in a JSON steps file,
and on the worker, which keeps it per step and carries it onto answers and permission continuations."""
import argparse
import json
from types import SimpleNamespace

import pytest

from fleet import cli
from fleet.composition import open_execution, open_work
from fleet.errors import FleetError
from fleet.remote import fleetd


def parsed(*arguments):
    return cli.build_parser().parse_args(["add", "h:job", *arguments])


def test_a_step_work_item_names_the_step_before_it():
    arguments = parsed("-s", "first", "--step-work-item", "w-1", "-s", "second", "-s", "third",
                       "--step-work-item", "w-3")
    assert cli.read_steps(arguments) == [{"prompt": "first", "work_item": "w-1"}, {"prompt": "second"},
                                         {"prompt": "third", "work_item": "w-3"}]


@pytest.mark.parametrize("arguments", [("--step-work-item", "w-1", "-s", "first"),
                                       ("-s", "first", "--step-work-item", "w-1", "--step-work-item", "w-2")])
def test_a_step_work_item_must_follow_its_step_once(arguments, capsys):
    with pytest.raises(SystemExit):
        parsed(*arguments)
    assert "must follow the --step it names" in capsys.readouterr().err


def test_a_json_steps_file_names_work_per_step(tmp_path):
    path = tmp_path / "steps.json"
    path.write_text(json.dumps(["plain", {"prompt": "named", "work_item": "w-2"}]))
    assert cli.read_steps(parsed("-f", str(path))) == [{"prompt": "plain"}, {"prompt": "named", "work_item": "w-2"}]


def test_fleet_add_refuses_work_outside_the_jobs_project(project_id, monkeypatch, *, cli_container, override_cli_method):
    work = open_work()
    own = work.add(project=project_id, title="Own", goal="Here", actor="user")
    other = work.add(project="elsewhere", title="Other", goal="There", actor="user")
    open_execution().link("h", "job", own.id, actor="user")
    sent = []
    override_cli_method('references', 'job', lambda reference: (SimpleNamespace(name="h"), "job"))
    monkeypatch.setattr(cli.transport, "call", lambda host, arguments, stdin_text=None: {
        "status": "queued", "steps": [{"index": 0, "status": "done"}]} if arguments[0] == "show" else sent.append(
        json.loads(stdin_text)) or {"status": "queued", "steps": [{}]})
    with pytest.raises(FleetError, match="another project"):
        cli.command_add(parsed("-s", "go", "--step-work-item", other.id), container=cli_container)
    with pytest.raises(FleetError):
        cli.command_add(parsed("-s", "go", "--step-work-item", "w-missing"), container=cli_container)
    assert sent == []
    cli.command_add(parsed("-s", "go", "--step-work-item", own.id), container=cli_container)
    assert sent == [[{"prompt": "go", "work_item": own.id}]]


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "launch_runner", lambda job_id: None)
    return tmp_path


def create(worker, steps):
    path = worker / "steps.json"
    path.write_text(json.dumps(steps))
    fleetd.command_create(argparse.Namespace(
        id="job", run_id=None, fingerprint=None, schema_version=None, project="p", description="D", agent="claude",
        model=None, cwd=str(worker), permission="default", steps_file=str(path), keep_going=False, hold=True,
        allowed_tools=None, add_dir=[], env=[]))
    return fleetd.read_job("job")


def test_the_worker_keeps_each_steps_work_item(worker, capsys):
    job = create(worker, ["plain", {"prompt": "named", "work_item": "w-2"}])
    assert [step.get("work_item") for step in job["steps"]] == [None, "w-2"]
    assert "work_item" not in job["steps"][0]   # a job's definition without step work is as it was
    assert [step["work_item"] for step in fleetd.job_summary(job, 0)["steps"]] == [None, "w-2"]


def test_the_worker_refuses_a_work_item_that_is_not_an_id(worker, capsys):
    with pytest.raises(SystemExit):
        create(worker, [{"prompt": "named", "work_item": 7}])
    assert "must be a work item ID" in capsys.readouterr().out


def block_first_step():
    with fleetd.locked_job("job") as job:
        job["steps"][0]["status"] = "blocked"


def answer(worker, key, step):
    path = worker / "answer.json"
    path.write_text(json.dumps([step]))
    fleetd.command_add(argparse.Namespace(job="job", steps_file=str(path), retry=False, hold=True, key=key,
                                          answers=0, schema_version=1))


def test_an_answer_serves_the_blocked_steps_work_unless_it_names_its_own(worker, capsys):
    create(worker, [{"prompt": "named", "work_item": "w-1"}])
    block_first_step()
    answer(worker, "first", {"prompt": "Yes"})
    assert fleetd.read_job("job")["steps"][1]["work_item"] == "w-1"
    with fleetd.locked_job("job") as job:
        job["steps"][1]["status"] = "blocked"
        job["steps"][0]["answered_by"] = None
    answer(worker, "second", {"prompt": "Switch", "work_item": "w-9"})
    assert fleetd.read_job("job")["steps"][2]["work_item"] == "w-9"


def test_a_permission_continuation_serves_the_refused_steps_work(worker, monkeypatch, capsys):
    create(worker, ["plain", {"prompt": "named", "work_item": "w-2"}])
    monkeypatch.setattr(fleetd.sys, "stdin", SimpleNamespace(read=lambda: json.dumps(["Bash(ls:*)"])))
    fleetd.command_grant(argparse.Namespace(job="job", step=1, key="k", schema_version=1))
    assert fleetd.read_job("job")["steps"][2]["work_item"] == "w-2"
