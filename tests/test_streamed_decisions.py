"""The controller records decisions agents held on jobs on other hosts, once per id, linked to the job's run."""

import argparse
import json
import sys
from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace

import pytest

from fleet.container import configured_container
from fleet import transport

from fleet.projections.decisions import decision_log
from fleet_worker import fleetd
from fleet.ingestion import record_decisions
from fleet.services.live import FleetState, apply_message

GUIDANCE = dict(project="p", epic=None, constitution=dict(path="constitution.md", revision="abc", version=3),
                charter=dict(path="charters/e.md", revision="def", version=2))


@pytest.fixture
def world(project_id):
    store = configured_container().store()
    work = configured_container(store).work()
    task = work.add(project=project_id, title="Task", goal="Ship", actor="user")
    return SimpleNamespace(store=store, project=project_id, task=task, taken=set())


def held(work_item: str, identity: str = "d1", **change) -> dict:
    return {"id": identity, "work_item": work_item, "question": "Loosen the check?", "answer": "No",
            "principle": "Constitution: anti-goal 2", "actor": "codex", "context": "line 4",
            "time": 1700000000.5, **change}


def dispatch(world, work_item: str, job: str, guidance: dict | None = GUIDANCE):
    return configured_container(world.store).execution().dispatch(work_item, host='home', runtime='codex', actor='user', reason='Go', idempotency_key=job, remote_job_id=job, guidance=guidance, payload=dict(cwd='/repo', arguments=[], steps=[dict(prompt='Go')], context=None, hold=False)).run


def report(world, *jobs: dict, project: str | None = None, taken: set | None = None) -> None:
    host = {"name": "home", "ok": True, "jobs": {job["id"]: job for job in jobs}}
    record_decisions(configured_container(world.store).decisions(), configured_container(world.store).execution(), configured_container(world.store).initialized_attention(), host, lambda job: project, world.taken if taken is None else taken)


def test_a_streamed_decision_is_recorded_once_linked_to_its_run_with_pinned_guidance(world):
    run = dispatch(world, world.task.id, "job-1")
    job = {"id": "job-1", "decisions": [held(world.task.id)]}
    report(world, job)
    report(world, job)   # the next report of the same job
    report(world, job, taken=set())   # a re-sent decision after the deck restarted
    [decision] = configured_container(world.store).decisions().list()
    assert (decision.id, decision.source_run, decision.guidance, decision.affected_work_items) == (
        "d1", run.id, GUIDANCE, (world.task.id,))
    assert (decision.principle, decision.actor, decision.context, decision.time) == (
        "Constitution: anti-goal 2", "codex", "line 4", datetime.fromtimestamp(1700000000.5, timezone.utc))
    assert decision.activation is None and decision.attention_item is None


def test_a_job_without_a_run_still_has_its_decision_recorded_and_listed(world):
    report(world, {"id": "job-unlinked", "decisions": [held(world.task.id, "d2")]})
    [decision] = configured_container(world.store).decisions().list()
    assert decision.source_run is None and decision.guidance is None
    log = decision_log(configured_container(world.store).work(), configured_container(world.store).decisions(), project=world.project)
    assert [entry["id"] for entry in log] == ["d2"]


def test_a_run_not_linked_to_work_still_records_against_the_named_work_item(world):
    dispatch(world, world.task.id, "job-1", guidance=None)
    report(world, {"id": "job-1", "decisions": [held(world.task.id), held(world.task.id, "d3")]})
    assert [decision.id for decision in configured_container(world.store).decisions().list()] == ["d1", "d3"]


def test_an_unrecordable_decision_becomes_an_alert_once(world):
    run = dispatch(world, world.task.id, "job-1")
    job = {"id": "job-1", "decisions": [held("no-such-item")]}
    report(world, job)
    report(world, job, taken=set())
    assert configured_container(world.store).decisions().list() == []
    [alert] = [item for item in configured_container(world.store).initialized_attention().list() if item.source == "decision-stream"]
    assert (alert.kind, alert.owner, alert.project, alert.run, alert.source_reference, alert.context_reference) == (
        "alert", "user", world.project, run.id, "d1", "job:home:job-1")
    assert alert.headline.startswith("Agent's decision not recorded:")


def test_without_a_run_the_alert_goes_to_the_jobs_project(world):
    report(world, {"id": "job-2", "decisions": [held("no-such-item")]}, project=world.project)
    [alert] = [item for item in configured_container(world.store).initialized_attention().list() if item.source == "decision-stream"]
    assert alert.project == world.project and alert.run is None


def test_an_unreachable_host_records_nothing(world):
    host = {"name": "home", "ok": False, "jobs": {"job-1": {"id": "job-1", "decisions": [held(world.task.id)]}}}
    record_decisions(configured_container(world.store).decisions(), configured_container(world.store).execution(), configured_container(world.store).initialized_attention(), host, lambda job: None, set())
    assert configured_container(world.store).decisions().list() == []


def test_a_decision_held_by_fleetd_reaches_the_store_through_the_job_stream(world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    (tmp_path / "jobs" / "job-1").mkdir(parents=True)
    (tmp_path / "jobs" / "job-1" / "job.json").write_text(json.dumps({
        "id": "job-1", "project": "p", "description": "Decide", "agent": "codex", "cwd": str(tmp_path),
        "permission": "default", "created_at": 1.0, "steps": [], "cancelled": False}))
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(held(world.task.id))))
    fleetd.command_decision(argparse.Namespace(job="job-1", schema_version=1))
    run = dispatch(world, world.task.id, "job-1")
    state = FleetState([transport.Host('home', 'home')], container=configured_container(store=world.store))
    apply_message(state, state.hosts[0], {"type": "hello"})
    apply_message(state, state.hosts[0], {"type": "job", "job": fleetd.job_summary(fleetd.read_job("job-1"), 0)})
    [decision] = configured_container(world.store).decisions().list()
    assert (decision.id, decision.source_run, decision.guidance) == ("d1", run.id, GUIDANCE)


@pytest.mark.parametrize("restart", [False, True])
def test_streamed_decision_reconciles_on_history_worker_instead_of_job_message(world, monkeypatch, restart):
    import threading

    run = dispatch(world, world.task.id, 'job-1')
    state = FleetState([transport.Host('home', None)], container=configured_container(store=world.store))
    state.schedule_triage = lambda: None
    state.keep_documents = lambda *args: None
    state.by_host['home']['ok'] = True
    reconcile = state.execution.reconcile_decisions

    def forbidden():
        pytest.fail('job ingestion reconciled the entire decision store')

    monkeypatch.setattr(state.execution, 'reconcile_decisions', forbidden)
    now = datetime.now(timezone.utc).timestamp()
    apply_message(state, state.hosts[0], {'type': 'job', 'job': {
        'id': 'job-1', 'project': world.project, 'status': 'running', 'agent': 'codex',
        'steps': [{'index': 0, 'title': 'Build', 'status': 'running',
                   'started_at': now, 'finished_at': None}], 'documents': [], 'updated_at': now,
        'decisions': [held(world.task.id, time=now + 1)]}})
    assert state.execution.deliveries() == []
    if restart:
        monkeypatch.setattr(state.execution, 'reconcile_decisions', reconcile)
        state = FleetState([transport.Host('home', None)], container=configured_container(store=world.store))
        state.schedule_triage = lambda: None
        reconcile = state.execution.reconcile_decisions
    stop = threading.Event()

    def reconcile_once():
        assert not state.changed._is_owned()
        reconcile()
        stop.set()

    monkeypatch.setattr(state.execution, 'reconcile_decisions', reconcile_once)
    state.follow_history(stop)
    delivery, = state.execution.deliveries()
    assert (delivery.decision, delivery.run, delivery.status) == ('d1', run.id, 'pending')


def test_streamed_user_decision_preserves_its_recorder(world):
    run = dispatch(world, world.task.id, 'job-user')
    entry = held(world.task.id, actor='user', recorded_by='codex')
    report(world, {'id': 'job-user', 'decisions': [entry]})
    report(world, {'id': 'job-user', 'decisions': [entry]}, taken=set())
    [decision] = configured_container(world.store).decisions().list()
    assert (decision.actor, decision.source_run) == ('user', run.id)
    [change] = world.store.history(subjects=('decision:' + decision.id,))
    assert change['actor'] == 'codex'
