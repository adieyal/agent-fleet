"""Agents record decisions with the principle they relied on; listing by project or epic, newest first."""

import json
from types import SimpleNamespace

import pytest

from fleet.container import configured_container
from fleet_cli import cli
from fleet.transport import LOCAL_FLEETD_SOURCE

GUIDANCE = dict(project="p", epic=None, constitution=dict(path="constitution.md", revision="abc", version=3),
                charter=None)


@pytest.fixture(autouse=True)
def outside_a_job(monkeypatch):
    # These tests may themselves run inside a fleet job.
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)


@pytest.fixture
def tree(project_id):
    work = configured_container().work()
    epic = work.add(project=project_id, title="Transcriber", goal="Read", kind="epic", actor="user")
    task = work.add(project=project_id, title="Task", goal="Ship", parent=epic.id, actor="user")
    other = work.add(project=project_id, title="Elsewhere", goal="Other", actor="user")
    return SimpleNamespace(project=project_id, epic=epic, task=task, other=other)


def run_for(work_item: str, guidance: dict | None, key: str = "job"):
    return configured_container().execution().dispatch(work_item, host='h', runtime='codex', actor='user', reason='Go', idempotency_key=key, remote_job_id=key, guidance=guidance, payload=dict(cwd='/repo', arguments=[], steps=[dict(prompt='Go')], context=None, hold=False)).run


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
    stored = configured_container().decisions().get(decision['id'])
    assert stored.guidance == GUIDANCE and stored.context == "flagged line 4"


def test_the_jobs_own_run_is_found_from_fleet_job_id(tree, capsys, monkeypatch):
    run = run_for(tree.task.id, GUIDANCE, key="job-42")
    monkeypatch.setenv("FLEET_JOB_ID", "job-42")
    assert record(capsys, tree.task.id)["source_run"] == run.id


@pytest.fixture
def host_job(tmp_path, monkeypatch):
    """A fleet job on this host, served by the real fleetd, that this machine's store holds no run for."""
    home = tmp_path / "worker"
    (home / "jobs" / "job-7").mkdir(parents=True)
    (home / "jobs" / "job-7" / "job.json").write_text(json.dumps({"id": "job-7", "steps": [], "cancelled": False}))
    monkeypatch.setenv("FLEET_REMOTE_HOME", str(home))
    monkeypatch.setenv("FLEET_FLEETD_PATH", str(LOCAL_FLEETD_SOURCE))
    monkeypatch.setenv("FLEET_JOB_ID", "job-7")
    return home / "jobs" / "job-7" / "job.json"


def hand(capsys, work_item: str) -> str:
    capsys.readouterr()
    cli.main(["decision", "record", "--work-item", work_item, "--question", "Loosen the check?", "--answer", "No",
              "--principle", "Constitution: anti-goal 2", "--actor", "claude", "--context", "line 4"])
    return capsys.readouterr().out


def test_a_job_whose_run_another_store_holds_hands_the_decision_to_its_stream(tree, host_job, capsys):
    output = hand(capsys, tree.task.id)
    [held] = json.loads(host_job.read_text())["decisions"]
    assert output == (f"Decision {held['id']} handed to the controller via job job-7's stream; "
                      "it is recorded there when the controller next hears from this host.\n")
    assert {key: value for key, value in held.items() if key not in ("id", "time")} == dict(
        work_item=tree.task.id, question="Loosen the check?", answer="No", principle="Constitution: anti-goal 2",
        actor="claude", context="line 4")
    assert configured_container().decisions().list() == []


def test_a_resent_decision_is_a_new_decision_from_the_cli(tree, host_job, capsys):
    # Each CLI call is one decision with its own id; only a re-send of the same id is idempotent (fleetd, ingester).
    hand(capsys, tree.task.id)
    hand(capsys, tree.task.id)
    first, second = json.loads(host_job.read_text())["decisions"]
    assert first["id"] != second["id"]


def test_a_named_run_is_recorded_here_even_inside_a_job(tree, host_job, capsys):
    run = run_for(tree.task.id, GUIDANCE)
    assert record(capsys, tree.task.id, "--run", run.id)["source_run"] == run.id
    assert "decisions" not in json.loads(host_job.read_text())


def test_a_blank_principle_is_refused_before_it_reaches_the_job(host_job, capsys):
    with pytest.raises(SystemExit):
        cli.main(["decision", "record", "--work-item", "w1", "--question", "Q", "--answer", "A",
                  "--principle", " ", "--actor", "claude"])
    assert "principle" in capsys.readouterr().err
    assert "decisions" not in json.loads(host_job.read_text())


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
    elsewhere = configured_container().initialized_workspace().edit_registry(lambda registry: registry.create('q')).id
    item = configured_container().work().add(project=elsewhere, title='Q', goal='Q', actor='user')
    run = run_for(item.id, None)
    with pytest.raises(SystemExit):
        record(capsys, tree.task.id, "--run", run.id)
    assert "is not in" in capsys.readouterr().err
    assert configured_container().decisions().list() == []


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


def test_user_is_already_a_valid_decision_actor(tree, capsys):
    decision = record(capsys, tree.task.id, '--actor', 'user')
    assert decision['actor'] == 'user'


def test_user_decision_keeps_agent_recorder_in_history(tree, capsys):
    decision = record(capsys, tree.task.id, '--actor', 'user', '--recorded-by', 'codex')
    assert decision['actor'] == 'user'
    store = configured_container().store()
    [change] = store.history(subjects=('decision:' + decision['id'],))
    assert change['actor'] == 'codex'
    assert json.loads(change['to'])['actor'] == 'user'
    cli.main(['history', '--subject', 'decision:' + decision['id'], '--json'])
    history = json.loads(capsys.readouterr().out)
    assert history['entries'][0]['actor'] == 'codex'


def test_worker_cli_hands_both_attribution_roles_to_controller(tree, host_job, capsys):
    cli.main(['decision', 'record', '--work-item', tree.task.id, '--question', 'Ship now?',
              '--answer', 'Wait', '--principle', 'User instruction', '--actor', 'user',
              '--recorded-by', 'codex'])
    assert 'handed to the controller' in capsys.readouterr().out
    [held] = json.loads(host_job.read_text())['decisions']
    assert (held['actor'], held['recorded_by']) == ('user', 'codex')


def test_older_worker_rejects_separate_recorder_without_holding_decision(tree, host_job, capsys, monkeypatch, tmp_path):
    worker = tmp_path / 'old-fleetd.py'
    worker.write_text("""import argparse
import json
import sys
from pathlib import Path
if sys.argv[1:] == ['version']:
    print(json.dumps({'wire_protocol_version': 1}))
    raise SystemExit(0)
parser = argparse.ArgumentParser()
parser.add_argument('command', choices=['decision'])
parser.add_argument('job')
parser.add_argument('--schema-version', type=int)
parser.parse_args()
Path(__file__).with_suffix('.held').write_text(sys.stdin.read())
print(json.dumps({'id': 'old-held'}))
""")
    monkeypatch.setenv('FLEET_FLEETD_PATH', str(worker))
    with pytest.raises(SystemExit):
        cli.main(['decision', 'record', '--work-item', tree.task.id, '--question', 'Ship now?',
                  '--answer', 'Wait', '--principle', 'User instruction', '--actor', 'user',
                  '--recorded-by', 'codex'])
    assert '--recorded-by' in capsys.readouterr().err
    assert not worker.with_suffix('.held').exists()
    assert 'decisions' not in json.loads(host_job.read_text())


@pytest.mark.parametrize('recorder', ['', ' '])
def test_blank_recorder_never_writes_a_decision(tree, capsys, recorder):
    with pytest.raises(SystemExit):
        record(capsys, tree.task.id, '--actor', 'user', '--recorded-by', recorder)
    assert 'recorded_by must be nonblank' in capsys.readouterr().err
    assert configured_container().decisions().list() == []
