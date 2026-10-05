import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest


from fleet.container import configured_container
from fleet.errors import FleetError
from fleet.modules.attention import StreamContext
from fleet.modules.execution import JobObservation
from fleet.modules.records import TRIAGE_PATH
from fleet.triage_scheduler import TriageScheduler
from tests.integration.test_triage_commands import triage as triage_fixture, item

triage = triage_fixture


def scheduler(triage):
    services, *_, source = triage
    services.workspace.edit_registry(lambda registry: registry.link(triage[1].project, 'carbon', 'agent-fleet'))
    finish(services, source)
    calls = []
    return TriageScheduler(services, lambda run, **kw: calls.append((run.id, kw)),
                           lambda name: SimpleNamespace(is_local=True, name=name)), calls


def finish(services, run, status='done'):
    services.execution.observe(run.host, JobObservation(run.remote_job_id, status, run.runtime, None, None, None))


def test_coalesces_and_reserves_once_across_controllers(triage):
    services, *_ = triage
    a, b = item(triage), item(triage, job='b')
    engine, calls = scheduler(triage)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: engine.schedule(), range(2)))
    assert len(calls) == 1
    state = services.triage_repository.get(a.project)
    assert set(state['items']) == {a.id, b.id} and state['used'] == 1
    run = services.execution.get_run(state['run'])
    action = services.execution.get_action(run.action)
    assert action.work_item is None and a.id in action.payload['steps'][0]['prompt']
    assert engine.status(a.project)['budget_left'] == 11


def test_end_without_action_requeues_once_then_escalates(triage):
    services, *_ = triage
    a = item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    finish(services, services.execution.get_run(calls[-1][0]))
    engine.schedule()
    assert len(calls) == 2
    finish(services, services.execution.get_run(calls[-1][0]))
    engine.schedule()
    assert len(calls) == 2
    assert services.attention.get(a.id).owner == 'user'
    assert 'ended without acting' in services.attention.get(a.id).owner_reason


def test_budget_escalates_queue_and_resets_next_day(triage):
    services, activation, _, _, body, _ = triage
    body['limits']['runs_per_day'] = 1
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='budget', actor='user')
    a = item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    finish(services, services.execution.get_run(calls[-1][0]))
    engine.schedule()
    assert 'budget exhausted' in services.attention.get(a.id).owner_reason
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(days=1)
    services.attention.commands.clock = services.store.clock
    item(triage, job='tomorrow')
    engine.schedule()
    assert len(calls) == 2


def test_server_down_timeout_precedes_launch(triage):
    services, *_ = triage
    a = item(triage)
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=31)
    engine, calls = scheduler(triage)
    engine.schedule()
    assert calls == [] and services.attention.get(a.id).owner == 'user'
    assert 'fleet web was down' in services.attention.get(a.id).owner_reason


def test_unreachable_unknown_retains_reservation_and_escalates(triage):
    services, *_ = triage
    # The 31-minute wait stays within one budget day, even near midnight.
    now = services.store.clock().replace(hour=12, minute=0, second=0, microsecond=0)
    services.store.clock = lambda: now
    services.attention.commands.clock = services.store.clock
    a = item(triage)
    engine, calls = scheduler(triage)
    def unavailable(run, **kw):
        calls.append(run.id)
        raise FleetError('connection refused')
    engine.deliver = unavailable
    engine.schedule()
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=31)
    engine.schedule()
    state = services.triage_repository.get(a.project)
    assert len(set(calls)) == 1 and state['used'] == 1
    assert services.attention.get(a.id).owner == 'user'
    assert 'outcome unknown' in services.attention.get(a.id).owner_reason


def test_no_mandate_means_no_dispatch(project_id):
    services = configured_container(configured_container().store()).services()
    services.attention.raise_item(project=project_id, owner='agent', kind='blocker', source='test',
        source_reference='a', headline='a', context_reference='a', actor='user')
    engine = TriageScheduler(services, lambda *a: pytest.fail('dispatch'), None)
    engine.schedule()
    assert engine.status(project_id)['mandate_version'] is None


def test_own_triage_failure_and_retry_limit_route_user(triage):
    services, activation, controller, _, body, source = triage
    context = StreamContext('carbon', 'job', source.remote_job_id, 'p', activation.project,
                            'job status failed', 'failed', 1, step=0)
    assert services.attention.route(activation.project, 'blocker', context)[0] == 'user'
    target = services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor='user', reason='work', idempotency_key='work').run
    finish(services, target, 'failed')
    a = item(triage, job=target.remote_job_id)
    result = controller.execute('retry', dict(item=a.id, reason='transient failure'))
    body['limits']['retries_per_step'] = 1
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='limit', actor='user')
    retried = services.execution.get_run(result['run'])
    context = StreamContext('carbon', 'job', retried.remote_job_id, 'p', activation.project,
                            'job status failed', 'failed', 1, step=0)
    owner, reason = services.attention.route(activation.project, 'blocker', context)
    assert owner == 'user' and result['decision']['id'] in reason


def test_new_queue_times_out_while_project_run_is_live(triage):
    services, *_ = triage
    item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    finish(services, services.execution.get_run(calls[0][0]), 'running')
    new = item(triage, job='later')
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=31)
    engine.schedule()
    assert len(calls) == 1
    assert services.attention.get(new.id).owner == 'user'
    decision = next(d for d in services.decisions.list() if d.attention_item == new.id)
    assert decision.source_run == calls[0][0] and decision.principle
    assert services.records.read(new.project, f'decisions/{decision.id}.json') is not None


def test_nonlocal_host_rejects_without_consuming_budget(triage):
    services, *_ = triage
    a = item(triage)
    engine, calls = scheduler(triage)
    engine.host = lambda name: SimpleNamespace(is_local=False)
    engine.schedule()
    assert calls == []
    state = services.triage_repository.get(a.project)
    assert 'controller machine' in state['error'] and 'used' not in state
    assert engine.status(a.project)['budget_left'] == 12


def test_cli_status_resolves_registered_project_name(triage, capsys, *, cli_container):
    from fleet_cli import cli
    services, activation, *_ = triage
    a = item(triage)
    project = next(p for p in services.workspace.registry().projects.values() if p.id == activation.project)
    cli.command_triage_status(SimpleNamespace(project=project.name, json=True), container=cli_container)
    result = json.loads(capsys.readouterr().out)
    assert result['project'] == activation.project and result['queue'] == [a.id]
    assert result['live_run'] is None and result['budget_left'] == 12


def test_scheduler_prompt_includes_pinned_constitution(triage):
    services, activation, *_ = triage
    services.records.write_guidance(activation.project, '# Rules\nUse isolated tests.', actor='user')
    item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    action = services.execution.get_action(services.execution.get_run(calls[0][0]).action)
    assert 'Use isolated tests.' in action.payload['steps'][0]['prompt']
    context = services.authority.get(action.activation)
    assert context.actor == 'triage:' + context.id
    assert action.guidance['constitution']['revision'] in action.payload['steps'][0]['prompt']
    assert str(services.store.path.parent) in action.payload['arguments']
    assert services.workspace.management_repository(activation.project) in action.payload['arguments']


def test_guard_archival_recovers_after_publication_failure(triage, monkeypatch):
    services, *_ = triage
    a = item(triage)
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=31)
    engine, calls = scheduler(triage)
    original = services.records.publish
    def interrupted(*args, **kwargs):
        raise OSError('disk temporarily unavailable')
    monkeypatch.setattr(services.records, 'publish', interrupted)
    engine.schedule()
    assert services.attention.get(a.id).owner == 'user'
    decision = next(d for d in services.decisions.list() if d.attention_item == a.id)
    assert services.records.read(a.project, f'decisions/{decision.id}.json') is None
    assert 'disk temporarily unavailable' in engine.status(a.project)['delivery_error']
    monkeypatch.setattr(services.records, 'publish', original)
    engine.schedule()
    assert services.records.read(a.project, f'decisions/{decision.id}.json') is not None
    assert len([d for d in services.decisions.list() if d.attention_item == a.id]) == 1


def test_new_delegation_resets_waiting_period(triage):
    services, *_ = triage
    item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    finish(services, services.execution.get_run(calls[0][0]), 'running')
    a = item(triage, job='waiting')
    engine.schedule()
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=31)
    services.attention.commands.clock = services.store.clock
    services.attention.take(a.id, actor='user')
    services.attention.delegate(a.id, actor='user', note='Try again now')
    engine.schedule()
    assert services.attention.get(a.id).owner == 'agent'


def test_observed_failure_dispatch_retry_then_failure_escalates(triage):
    from fleet.orchestration import ControllerCommands
    services, activation, _, _, body, _ = triage
    body['limits']['retries_per_step'] = 1
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='one-retry', actor='user')
    target = services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor='user', reason='Fixture work', idempotency_key='fixture-work').run
    finish(services, target, 'failed')
    host = dict(name='carbon', ok=True, sessions=[], jobs=[dict(id=target.remote_job_id, project='p',
        project_id=activation.project, status='failed', updated_at=1,
        steps=[dict(index=0, status='failed', title='test', started_at=1, message=None, answered_by=None)])])
    services.attention.observe(host)
    first, = services.attention.list(state='open')
    assert first.owner == 'agent'
    engine, calls = scheduler(triage)
    engine.schedule()
    engine.schedule()
    assert len(calls) == 1
    triage_run = services.execution.get_run(calls[0][0])
    triage_action = services.execution.get_action(triage_run.action)
    controller = ControllerCommands(services, triage_action.activation)
    result = controller.execute('retry', dict(item=first.id, reason='Transient fixture failure'))
    retried = services.execution.get_run(result['run'])
    finish(services, retried, 'failed')
    host['jobs'][0]['id'] = retried.remote_job_id
    host['jobs'][0]['updated_at'] = 2
    host['jobs'][0]['steps'][0]['started_at'] = 2
    services.attention.observe(host)
    second, = services.attention.list(state='open')
    assert second.owner == 'user' and result['decision']['id'] in second.owner_reason
    assert services.attention.get(first.id).state == 'resolved'


def test_acknowledged_agent_item_is_still_queued(triage):
    services, *_ = triage
    a = item(triage)
    services.attention.acknowledge(a.id, actor='user')
    engine, calls = scheduler(triage)
    engine.schedule()
    assert len(calls) == 1 and engine.status(a.project)['queue'] == [a.id]


def test_scheduler_uses_linked_host_label(triage):
    services, *_ = triage
    item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    action = services.execution.get_action(services.execution.get_run(calls[0][0]).action)
    arguments = action.payload['arguments']
    assert arguments[arguments.index('--project') + 1] == 'agent-fleet'
    assert action.project == triage[1].project


def test_scheduler_refuses_a_missing_host_link(request):
    triage_case = request.getfixturevalue('triage')
    services, activation, *_ = triage_case
    attention = item(triage_case)
    engine, calls = scheduler(triage_case)
    services.workspace.edit_registry(lambda registry: registry.unlink('carbon', 'agent-fleet'))
    actions = services.execution.actions()
    engine.schedule()
    assert calls == []
    assert services.execution.actions() == actions
    state = services.triage_repository.get(activation.project)
    assert state.get('run') is None and state.get('used', 0) == 0
    assert 'no link on host carbon' in state['error']
    assert f'fleet project link {activation.project}' in state['error']
    assert services.attention.get(attention.id).owner == 'agent'


def test_idle_scheduler_does_not_lookup_historical_run_actions(triage, monkeypatch):  # noqa: F811
    from fleet.infrastructure.sqlite.execution import ExecutionRepository
    services, *_, source = triage
    engine, calls = scheduler(triage)
    lookups = []
    original = ExecutionRepository.get_action
    def get_action(repository, identity):
        lookups.append(identity)
        return original(repository, identity)
    monkeypatch.setattr(ExecutionRepository, 'get_action', get_action)
    engine.schedule()
    engine.schedule()
    assert calls == []
    assert source.action not in lookups


def test_scheduler_queue_decodes_only_its_owned_items(triage, monkeypatch):  # noqa: F811
    import fleet.infrastructure.sqlite.attention as persistence
    services = triage[0]
    agent = item(triage, job='agent')
    item(triage, job='user', owner='user')
    decoded = []
    original = persistence.decode
    def decode(row):
        decoded.append(row['id'])
        return original(row)
    monkeypatch.setattr(persistence, 'decode', decode)
    assert [i.id for i in TriageScheduler.queue(services, agent.project)] == [agent.id]
    assert decoded == [agent.id]


def test_unchanged_delivery_health_avoids_write_scope(triage, monkeypatch):  # noqa: F811
    services = triage[0]
    engine, _ = scheduler(triage)
    engine.delivery_error(triage[1].project, None)
    monkeypatch.setattr(services.triage_repository, 'transaction',
                        lambda: pytest.fail('unchanged delivery health opened a write scope'))
    engine.delivery_error(triage[1].project, None)


def test_idle_schedule_reuses_only_same_store_revision_and_day(triage, monkeypatch):  # noqa: F811
    services = triage[0]
    engine, calls = scheduler(triage)
    engine.schedule()  # Establish durable day/error/default fields.
    engine.schedule()
    reserves = []
    original = engine.reserve
    def reserve(project):
        reserves.append(project)
        return original(project)
    monkeypatch.setattr(engine, 'reserve', reserve)
    engine.schedule()
    assert reserves == []
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(days=1)
    engine.schedule()
    assert reserves == [triage[1].project]
    assert services.triage_repository.get(triage[1].project)['day'] == services.store.clock().date().isoformat()
    services.attention.commands.clock = services.store.clock
    item(triage, job='new-generation')
    engine.schedule()
    assert len(calls) == 1


def test_no_queue_unknown_run_still_retries_on_time(triage):  # noqa: F811
    services = triage[0]
    attention = item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    services.attention.take(attention.id, actor='user', reason='handle manually')
    engine.schedule()
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=1)
    engine.schedule()
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[1][1] == {'reconcile': True}
