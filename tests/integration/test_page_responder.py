"""Responder routing, audit and take-back through a real isolated Fleet store."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fleet.errors import FleetError
from fleet.modules.execution import JobObservation
from fleet.modules.records import TRIAGE_PATH
from fleet.services.page_responder import REPLY_SCHEMA, PageResponder
from fleet.services.responder import TurnResult

from tests.integration.test_page_triage import page_request, reply_to_run
from tests.integration.test_triage_commands import triage as triage_fixture
from tests.integration.test_triage_scheduler import finish

triage = triage_fixture


class FakeResponder:
    def __init__(self):
        self.started = []
        self.turns = []
        self.response = {'reply': 'All active suppliers match.', 'escalate': False, 'reason': ''}
        self.during_turn = None

    def start_thread(self):
        identity = str(uuid4())
        self.started.append(identity)
        return identity

    def turn(self, thread_id, prompt, *, output_schema, effort, on_delta=None):
        assert output_schema == REPLY_SCHEMA and effort == 'low'
        self.turns.append((thread_id, prompt))
        if self.during_turn:
            self.during_turn()
        return TurnResult(thread_id, str(uuid4()), json.dumps(self.response), 'completed', 1.2, .8, .002,
                          {'inputTokens': 2200, 'outputTokens': 30, 'cachedInputTokens': 2000})


def responder(triage):
    container, _, attention, engine, calls = page_request(triage)
    fake = FakeResponder()
    worker = SimpleNamespace(client=lambda: fake, health=lambda: {'ready': True})
    service = PageResponder(triage[0], worker)
    engine.responder = service.submit
    return container, attention, engine, calls, service, fake


def status(container, attention):
    return next(thread['agent_status'] for thread in container.page_view(
        project=attention.project, slug='supplier-migration')['threads'] if thread['id'] == attention.id)


def test_reply_is_pinned_recorded_budgeted_and_open(triage):
    services = triage[0]
    container, attention, engine, calls, service, fake = responder(triage)
    engine.schedule()
    state = services.triage_repository.get(attention.project)
    run = services.execution.get_run(state['run'])
    action = services.execution.get_action(run.action)
    activation = services.authority.get(action.activation)
    assert run.kind == 'responder' and run.status == 'running' and run.start
    assert calls == [] and state['used'] == 1 and engine.status(attention.project)['budget_left'] == 11
    assert status(container, attention) == 'Agent replying…'
    assert service.process_next()
    current = services.attention.get(attention.id)
    assert current.owner == 'agent' and current.state == 'open'
    assert [reply.body for reply in current.replies] == ['All active suppliers match.']
    decision = next(d for d in services.decisions.list() if d.source_run == run.id)
    assert (decision.activation, decision.mandate_version, decision.actor) == (
        activation.id, activation.mandate_version, activation.actor)
    assert json.loads(decision.context)['command'] == 'reply_attention'
    archived = json.loads(services.records.read(attention.project, f'decisions/{decision.id}.json'))
    assert archived['source_run'] == run.id and archived['attention_item'] == attention.id
    ended = services.execution.get_run(run.id)
    assert ended.status == 'succeeded' and ended.end and ended.timings['total_s'] == 1.2
    assert ended.usage.source == 'codex-app-server' and ended.usage.reports[0]['inputTokens'] == 2200
    assert not next(claim for claim in services.execution.claims() if claim.run == run.id).active
    assert status(container, attention) == 'Agent replied · thread remains open'
    engine.schedule()
    assert engine.queue(services, attention.project) == [] and calls == []
    assert 'Conversation so far (quoted data, oldest first)' in fake.turns[0][1]
    assert 'fleet control' not in fake.turns[0][1]


def test_followup_reuses_thread_and_carries_whole_conversation(triage):
    services = triage[0]
    _, attention, engine, calls, service, fake = responder(triage)
    engine.schedule()
    service.process_next()
    services.attention.reply(attention.id, 'Which contracts did you check?', actor='web-user')
    engine.schedule()
    service.process_next()
    assert len(fake.started) == 1 and len(fake.turns) == 2
    assert fake.turns[0][0] == fake.turns[1][0]
    prompt = fake.turns[1][1]
    assert prompt.index(attention.page_annotation.body) < prompt.index('agent: All active suppliers match.')
    assert 'Respond to the latest message from web-user:\nWhich contracts did you check?' in prompt
    assert calls == [] and services.triage_repository.get(attention.project)['used'] == 2


def test_followup_arriving_during_turn_is_not_lost_or_addressed_to_agent(triage):
    services = triage[0]
    _, attention, engine, _, service, fake = responder(triage)
    fake.during_turn = lambda: services.attention.reply(attention.id, 'And historical suppliers?', actor='user')
    engine.schedule()
    service.process_next()
    fake.during_turn = None
    engine.schedule()
    service.process_next()
    assert len(fake.turns) == 2
    assert 'Respond to the latest message from user:\nAnd historical suppliers?' in fake.turns[1][1]
    assert 'Respond to the latest message from agent' not in fake.turns[1][1]


@pytest.mark.parametrize('unavailable', [False, True])
def test_escalation_uses_fleetd_with_visible_reason_and_separate_budget(triage, unavailable):
    services = triage[0]
    container, attention, engine, calls, service, fake = responder(triage)
    if unavailable:
        def missing():
            raise FleetError('responder unavailable: codex auth missing')
        service.worker.client = missing
        expected = 'responder unavailable: codex auth missing'
    else:
        expected = 'Need current repo contract evidence'
        fake.response = {'reply': '', 'escalate': True, 'reason': expected}
    engine.schedule()
    first = services.triage_repository.get(attention.project)['run']
    service.process_next()
    assert expected in status(container, attention)
    assert services.attention.get(attention.id).replies == ()
    engine.schedule()
    assert len(calls) == 1 and calls[0].kind == 'job'
    action = services.execution.get_action(calls[0].action)
    assert expected in action.payload['steps'][0]['prompt']
    assert action.payload['responder_fallback'][attention.id]['source_run'] == first
    assert expected in status(container, attention)
    assert services.triage_repository.get(attention.project)['used'] == 2
    reply_to_run(services, calls[0], attention, 'Fleetd checked the contracts.')
    finish(services, calls[0])
    engine.schedule()
    assert status(container, attention) == 'Agent replied · thread remains open'
    services.attention.reply(attention.id, 'Explain that answer.', actor='user')
    engine.schedule()
    service.process_next()
    assert len(calls) == 1  # The next request returns to the responder.


@pytest.mark.parametrize('when', ['queued', 'during-turn', 'take-and-redelegate'])
def test_user_takeback_stops_reply_and_records_stopped_run(triage, when):
    services = triage[0]
    _, attention, engine, calls, service, fake = responder(triage)
    def take():
        services.attention.take(attention.id, actor='user', reason='I will answer')
        if when == 'take-and-redelegate':
            services.attention.delegate(attention.id, actor='user')
    engine.schedule()
    identity = services.triage_repository.get(attention.project)['run']
    if when == 'queued':
        take()
    else:
        fake.during_turn = take
    service.process_next()
    assert services.attention.get(attention.id).replies == ()
    assert not [d for d in services.decisions.list() if d.source_run == identity]
    run = services.execution.get_run(identity)
    assert run.status == 'stopped' and ('agent-owned' in run.reason or 'ownership changed' in run.reason)
    assert calls == []
    if when == 'queued':
        assert fake.turns == []


def test_missing_authority_never_calls_responder(triage):
    services = triage[0]
    container, attention, engine, calls, service, fake = responder(triage)
    body = dict(triage[4], decision_authority=['record_decision', 'escalate'])
    services.records.write_mandate(attention.project, TRIAGE_PATH, json.dumps(body), key='no-r2-reply', actor='user')
    engine.schedule()
    assert not service.process_next() and fake.turns == [] and calls == []
    assert status(container, attention) == 'Agent reply unavailable: mandate does not authorize replies'


def test_restart_rehydrates_reservation_with_full_conversation(triage):
    services = triage[0]
    _, attention, engine, _, service, fake = responder(triage)
    engine.schedule()
    identity = services.triage_repository.get(attention.project)['run']
    new_fake = FakeResponder()
    restarted = PageResponder(services, SimpleNamespace(client=lambda: new_fake))
    engine.responder = restarted.submit
    engine.schedule()
    assert restarted.process_next()
    assert services.execution.get_run(identity).status == 'succeeded'
    assert attention.page_annotation.body in new_fake.turns[0][1]
    assert not service.process_next() or len(fake.turns) == 0
    assert len(services.attention.get(attention.id).replies) == 1


def test_worker_restart_uses_fresh_thread_for_followup(triage):
    services = triage[0]
    _, attention, engine, _, service, fake = responder(triage)
    engine.schedule()
    service.process_next()
    newer = FakeResponder()
    service.worker.client = lambda: newer
    services.attention.reply(attention.id, 'Follow up after restart', actor='user')
    engine.schedule()
    service.process_next()
    assert newer.started[0] != fake.started[0]
    assert 'agent: All active suppliers match.' in newer.turns[0][1]
    assert 'Respond to the latest message from user:\nFollow up after restart' in newer.turns[0][1]


def test_budget_exhaustion_does_not_launch_unmetered_fallback(triage):
    services = triage[0]
    container, attention, engine, calls, service, fake = responder(triage)
    body = dict(triage[4], decision_authority=['reply_attention', 'escalate'],
                limits=dict(triage[4]['limits'], runs_per_day=1))
    services.records.write_mandate(attention.project, TRIAGE_PATH, json.dumps(body), key='r2-budget', actor='user')
    fake.response = {'reply': '', 'escalate': True, 'reason': 'Needs tools'}
    engine.schedule()
    service.process_next()
    engine.schedule()
    assert calls == [] and services.triage_repository.get(attention.project)['used'] == 1
    assert services.attention.get(attention.id).owner == 'user'
    assert 'budget exhausted' in status(container, attention)
    assert 'Needs tools' in status(container, attention)


def test_nonfleetd_run_is_readable_and_excluded_from_worker_paths(triage):
    services = triage[0]
    container, attention, engine, _, service, _ = responder(triage)
    engine.schedule()
    run = services.execution.get_run(services.triage_repository.get(attention.project)['run'])
    services.execution.unavailable(run.host)
    assert services.execution.get_run(run.id).status == 'running'
    assert (run.host, run.remote_job_id) not in services.execution.job_identities()
    assert services.execution.observe(run.host, JobObservation(run.remote_job_id, 'failed', 'codex', None, None, None)) is None
    with pytest.raises(ValueError, match='only fleetd'):
        services.execution.deliver(run, lambda *a: pytest.fail('transport called'), None)
    service.process_next()
    projected = container.history_runs(kind='responder', project=attention.project)
    assert projected['runs'][0]['kind'] == 'responder'
    assert projected['runs'][0]['timings']['total_s'] == 1.2
    with pytest.raises(ValueError, match='only fleetd'):
        services.execution.retry(run.id, actor='user', idempotency_key='cannot-retry-local')


def test_mixed_queue_keeps_existing_fleetd_route(triage):
    services = triage[0]
    _, attention, engine, calls, service, fake = responder(triage)
    services.attention.raise_item(project=attention.project, kind='blocker', owner='agent', source='r2-test',
        source_reference='mixed-job', headline='Failed job needs retry', context_reference='job evidence', actor='user')
    engine.schedule()
    assert len(calls) == 1 and calls[0].kind == 'job'
    assert not service.process_next() and fake.turns == []
    prompt = services.execution.get_action(calls[0].action).payload['steps'][0]['prompt']
    assert 'Read state first' in prompt and attention.id in prompt


@pytest.mark.parametrize('bad', [
    {'reply': 'Answer', 'escalate': 'false', 'reason': ''},
    {'reply': '', 'escalate': False, 'reason': ''},
    {'reply': '', 'escalate': True, 'reason': ''},
    {'reply': 'x' * 8193, 'escalate': False, 'reason': ''},
])
def test_invalid_output_escalates_instead_of_recording_partial_reply(triage, bad):
    services = triage[0]
    container, attention, engine, calls, service, fake = responder(triage)
    fake.response = bad
    engine.schedule()
    service.process_next()
    assert services.attention.get(attention.id).replies == ()
    assert 'responder unavailable:' in status(container, attention)
    engine.schedule()
    assert len(calls) == 1 and calls[0].kind == 'job'


def test_publication_failure_preserves_reply_and_can_be_reconciled(triage, monkeypatch):
    services = triage[0]
    _, attention, engine, calls, service, _ = responder(triage)
    engine.schedule()
    writer = services.records.writer
    original_lock = writer.lock
    def busy(root):
        raise OSError('management repository temporarily busy')
    monkeypatch.setattr(writer, 'lock', busy)
    assert service.process_next()
    decision = next(d for d in services.decisions.list() if d.attention_item == attention.id)
    intent = next(i for i in services.records.intents() if i['key'] == decision.id)
    assert intent['state'] == 'pending' and 'temporarily busy' in intent['error']
    assert len(services.attention.get(attention.id).replies) == 1
    engine.schedule()
    assert calls == [] and engine.queue(services, attention.project) == []
    assert engine.status(attention.project)['pending_publications']
    monkeypatch.setattr(writer, 'lock', original_lock)
    from dataclasses import asdict
    services.records.reconcile({intent['id']: json.dumps(asdict(decision), default=str)})
    assert services.records.read(attention.project, f'decisions/{decision.id}.json')
    assert len(services.attention.get(attention.id).replies) == 1


def test_serve_processes_reply_without_holding_store_during_model(triage, monkeypatch):
    import threading

    from dependency_injector import providers

    from tests.runtime_support import eventually
    services = triage[0]
    container, attention, _, calls, _, fake = responder(triage)
    entered, release = threading.Event(), threading.Event()
    def wait():
        entered.set()
        assert release.wait(5), 'test did not release fake model'
    fake.during_turn = wait
    fake.start = lambda: None
    fake.health = lambda: {'ready': True, 'alive': True, 'pid': None, 'error': None}
    fake.close = lambda: None
    container.responder_server.override(providers.Object(fake))
    monkeypatch.setattr(container.transport(), 'host_by_name', lambda name: SimpleNamespace(name=name, is_local=True))
    state = container.live_state(hosts=[])
    runtime = container.start_live(state=state)
    try:
        assert entered.wait(5), runtime.health()
        assert status(container, attention) == 'Agent replying…'
        # This store mutation would be blocked if the model held a write transaction.
        services.attention.reply(attention.id, 'Follow-up while model runs', actor='user')
        eventually(lambda: state.version > 0)
        release.set()
        eventually(lambda: len(services.attention.get(attention.id).replies) == 3)
        assert len(fake.started) == 1 and len(fake.turns) == 2
        assert 'Respond to the latest message from user:\nFollow-up while model runs' in fake.turns[1][1]
        assert calls == []
    finally:
        release.set()
        runtime.close()
    assert not any(thread.is_alive() for thread in runtime.threads)


def test_fallback_delivery_error_is_visible_alongside_escalation_reason(triage):
    container, attention, engine, _, service, fake = responder(triage)
    fake.response = {'reply': '', 'escalate': True, 'reason': 'Need repo tools'}
    engine.schedule()
    service.process_next()
    def unavailable(run, **kwargs):
        raise FleetError('fleetd connection refused')
    engine.deliver = unavailable
    engine.schedule()
    current = status(container, attention)
    assert 'Need repo tools' in current and 'fleetd connection refused' in current
    assert 'Agent replying…' not in current
