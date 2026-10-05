"""Page requests are bounded triage work; replying leaves their threads open."""
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet.container import configured_container
from fleet_cli import cli
from fleet.modules.authority import AuthorityRejected
from fleet.modules.records import TRIAGE_PATH
from fleet.orchestration import ControllerCommands
from fleet.triage_scheduler import TriageScheduler
from tests.integration.test_triage_commands import triage as triage_fixture, item
from tests.integration.test_triage_scheduler import finish
from tests.pages_fixture import seed_page


triage = triage_fixture

def allow_reply(services, project, body):
    body = dict(body, decision_authority=body['decision_authority'] + ['reply_attention'])
    services.records.write_mandate(project, TRIAGE_PATH, json.dumps(body), key=str(uuid4()), actor='user')
    return services.authority.activate(project=project, actor='triage:reply', role='triage', mandate_path=TRIAGE_PATH)


def test_reply_requires_pinned_authority_and_is_repeatable(triage):
    services, activation, commands, _, body, _ = triage
    attention = item(triage)
    with pytest.raises(AuthorityRejected, match='reply_attention'):
        commands.execute('reply', dict(item=attention.id, body='Hello'))
    active = allow_reply(services, activation.project, body)
    with pytest.raises(AuthorityRejected, match='reply_attention'):
        commands.execute('reply', dict(item=attention.id, body='Still pinned to old mandate'))
    services.execution.dispatch(None, project=active.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor=active.actor, activation=active.id,
        reason='Reply', idempotency_key='reply-test')
    commands = ControllerCommands(services, active.id)
    for text in ['First answer', 'More evidence']:
        result = commands.execute('reply', dict(item=attention.id, body=text))
        assert result['decision']['source_run']
    current = services.attention.get(attention.id)
    assert current.state == 'open' and current.owner == 'agent'
    assert [(m.actor, m.body) for m in current.replies] == [
        (active.actor, 'First answer'), (active.actor, 'More evidence')]
    services.attention.take(attention.id, actor='user', reason='I will answer')
    with pytest.raises(AuthorityRejected, match='agent-owned'):
        commands.execute('reply', dict(item=attention.id, body='Too late'))


def test_cli_control_accepts_reply(triage, capsys):
    services, activation, _, _, body, _ = triage
    attention = item(triage)
    active = allow_reply(services, activation.project, body)
    services.execution.dispatch(None, project=active.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor=active.actor, activation=active.id,
        reason='Reply', idempotency_key='reply-cli')
    cli.main(['control', active.id, 'reply', json.dumps(dict(item=attention.id, body='From the CLI'))])
    assert json.loads(capsys.readouterr().out)['decision']['source_run']
    assert [m.body for m in services.attention.get(attention.id).replies] == ['From the CLI']


def page_request(triage):
    services, active, _, _, body, _ = triage
    finish(services, triage[-1])
    container = configured_container(services.store)
    seeded = seed_page(container)
    project = seeded['project']
    allow_reply(services, project, body)
    services.workspace.edit_registry(lambda registry: registry.link(project, 'carbon', 'supplier-demo'))
    comment = container.page_change(project=project, slug='supplier-migration', operation='comment',
        revision=seeded['revision'], comment_id=str(uuid4()), headline='Contract evidence',
        body='Please verify the supplier contract evidence.', reason='Agent must answer.', actor='user', owner='agent',
        selector={'type': 'FragmentSelector', 'value': 'supplier-work'})
    calls = []
    engine = TriageScheduler(services, lambda run, **kw: calls.append(run),
                             lambda name: SimpleNamespace(is_local=True, name=name))
    return container, seeded, services.attention.get(comment['id']), engine, calls


def reply_to_run(services, run, attention, text='All active suppliers match.'):
    action = services.execution.get_action(run.action)
    return ControllerCommands(services, action.activation).execute('reply', dict(item=attention.id, body=text))


def test_page_brief_queue_reply_and_follow_up(triage):
    services = triage[0]
    container, seeded, attention, engine, calls = page_request(triage)
    assert engine.queue(services, attention.project)[0].id == attention.id
    engine.schedule()
    assert len(calls) == 1
    prompt = services.execution.get_action(calls[0].action).payload['steps'][0]['prompt']
    for text in ['supplier-migration', 'Supplier migration', 'markdown', 'resolved_block',
                 'Active supplier slice', attention.page_annotation.body, 'Do not resolve unless asked']:
        assert text in prompt
    reply_to_run(services, calls[0], attention)
    finish(services, calls[0])
    engine.schedule()
    assert len(calls) == 1 and engine.queue(services, attention.project) == []
    view = container.page_view(project=attention.project, slug='supplier-migration')
    thread = next(t for t in view['threads'] if t['id'] == attention.id)
    assert thread['state'] == 'open' and thread['answers'] == []
    assert thread['agent_status'] == 'Agent replied · thread remains open'
    # The request gets a fresh queue timeout even when the open thread has been idle for hours.
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(hours=2)
    services.attention.commands.clock = services.store.clock
    services.attention.reply(attention.id, 'Which contracts did you check?', actor='web-user')
    engine.schedule()
    assert len(calls) == 2
    assert 'Which contracts did you check?' in services.execution.get_action(calls[1].action).payload['steps'][0]['prompt']
    reply_to_run(services, calls[1], attention, 'The signed contracts.')
    # A follow-up arriving while the run is live must survive completion of that run.
    services.attention.reply(attention.id, 'What about historical suppliers?', actor='user')
    finish(services, calls[1])
    engine.schedule()
    assert len(calls) == 3


def test_page_failure_and_budget_are_visible(triage):
    services = triage[0]
    container, seeded, attention, engine, calls = page_request(triage)
    engine.schedule()
    finish(services, calls[0], 'failed')
    engine.schedule()
    thread = container.page_view(project=attention.project, slug='supplier-migration')['threads'][0]
    assert 'failed' in thread['agent_status'] and thread['owner'] == 'user'
    services.attention.delegate(attention.id, actor='user')
    with services.triage_repository.transaction() as scope:
        state = scope.records.get(attention.project)
        state['used'] = 12
        scope.records.save(attention.project, state)
    engine.schedule()
    thread = container.page_view(project=attention.project, slug='supplier-migration')['threads'][0]
    assert 'budget exhausted' in thread['agent_status']


def test_missing_reply_authority_is_visible_and_does_not_launch(triage):
    services = triage[0]
    container, _, attention, engine, calls = page_request(triage)
    policy = dict(triage[4], decision_authority=['record_decision', 'escalate'])
    services.records.write_mandate(attention.project, TRIAGE_PATH, json.dumps(policy), key='no-reply', actor='user')
    engine.schedule()
    assert calls == []
    thread = container.page_view(project=attention.project, slug='supplier-migration')['threads'][0]
    assert thread['agent_status'] == 'Agent reply unavailable: mandate does not authorize replies'
