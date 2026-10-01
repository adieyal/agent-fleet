import json

import pytest

from fleet import cli, composition
from fleet.modules.attention import InputObservation, StreamContext
from fleet.modules.authority import Activation, AuthorityRejected
from fleet.modules.execution import GrantResult, JobObservation
from fleet.modules.records import TRIAGE_PATH
from fleet.orchestration import ControllerCommands, triage_prompt


@pytest.fixture
def triage(project_id, tmp_path):
    services = composition.facades(composition.open_store())
    composition.open_workspace(services.store)
    body = dict(goal='Triage', constraints=[], escalation_conditions=[], criteria_it_may_judge=[],
                decision_authority=['retry', 'add_step', 'grant', 'resolve_attention', 'escalate', 'record_decision'],
                host='carbon', runtime='codex', cwd=str(tmp_path), permission='acceptEdits', routing={},
                permissions={'allow': ['Read'], 'escalate': ['Bash(git push:*)']},
                limits={'retries_per_step': 2, 'runs_per_day': 12, 'unclaimed_minutes': 30})
    services.records.write_mandate(project_id, TRIAGE_PATH, json.dumps(body), key='mandate', actor='user')
    activation = services.authority.activate(project=project_id, actor='triage:test', role='triage', mandate_path=TRIAGE_PATH)
    run = services.execution.dispatch(None, project=project_id, host='carbon', runtime='codex',
        payload={'cwd': str(tmp_path)}, actor=activation.actor, activation=activation.id,
        reason='Triage activation', idempotency_key=f'triage:{project_id}:first').run
    calls = []

    def grant(request):
        calls.append(request)
        return GrantResult(request.rules, 1)

    def step(request):
        calls.append(request)
        return 'retry submitted' if request.retry else 'added step 2'

    services.execution.grant, services.execution.step = grant, step
    return services, activation, ControllerCommands(services.store, activation.id), calls, body, run


def item(triage, status='failed', *, owner='agent', job='j', project=None, work_item=None):
    services, activation, *_ = triage
    context = StreamContext('carbon', 'job', job, 'p', activation.project, f'job status {status}', status, 1, step=0)
    return services.attention.raise_item(project=project or activation.project, kind='blocker', owner=owner,
        subject=f'job:carbon:{job}', source='test', source_reference=f'{job}:{status}', headline=f'job {status}',
        context_reference='context', actor='host', stream_context=context, work_item=work_item)


def refusal(triage, *, rules=None, denied=None, occurrence='r1'):
    services, activation, *_ = triage
    observation = InputObservation(1, 'claude', 'job', 'j', 's', 0, 'p', 'input_requested', 'permission',
                                   'PreToolUse', occurrence, 1, 'context',
                                   request={'tool': 'Read', 'rules': rules if rules is not None else ['Read'],
                                            'denied_by': denied or []})
    services.attention.observe_input('carbon', observation, project_id=activation.project)
    return next(entry for entry in services.attention.list() if entry.refusals)


def assert_decision(triage, result, attention, command):
    services, activation, _, _, _, run = triage
    decision = services.decisions.get(result['decision']['id'])
    assert decision.attention_item == attention.id
    assert (decision.activation, decision.mandate_version, decision.actor, decision.source_run) == (
        activation.id, activation.mandate_version, activation.actor, run.id)
    assert decision.affected_work_items == (() if attention.work_item is None else (attention.work_item,))
    assert decision.principle
    assert json.loads(decision.context)['command'] == command
    recorded = json.loads(services.records.read(activation.project, f'decisions/{decision.id}.json'))
    assert recorded['attention_item'] == attention.id and recorded['source_run'] == run.id
    return decision


def test_project_activation_pins_version_and_needs_no_work_item(triage):
    services, activation, _, _, body, _ = triage
    assert services.authority.get(activation.id) == activation and activation.work_item is None
    body['permissions']['allow'] = []
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='new', actor='user')
    assert services.authority.triage_mandate(activation.id).permissions['allow'] == ['Read']


def test_only_triage_can_omit_work_and_triage_cannot_judge(triage):
    services, activation, _, _, body, _ = triage
    with pytest.raises(AuthorityRejected):
        Activation('id', 'actor', 'orchestrator', 'p', None, 'm', 'v')
    with pytest.raises(AuthorityRejected):
        services.authority.activate(role='orchestrator', actor='actor', mandate_path=TRIAGE_PATH)
    with pytest.raises(AuthorityRejected):
        services.authority.activate(role='triage', actor='actor', project=activation.project, mandate_path='other.json')
    body['criteria_it_may_judge'] = ['criterion']
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='judge', actor='user')
    with pytest.raises(AuthorityRejected, match='judge'):
        services.authority.activate(role='triage', actor='actor', project=activation.project, mandate_path=TRIAGE_PATH)


@pytest.mark.parametrize('change', [dict(project='other'), dict(host='other'), dict(runtime='claude'),
                                   dict(payload={'cwd': '/other'})])
def test_triage_launch_stays_within_pinned_scope(triage, change):
    services, activation, _, _, body, _ = triage
    arguments = dict(project=activation.project, host='carbon', runtime='codex', payload={'cwd': body['cwd']},
        actor=activation.actor, activation=activation.id, reason='Triage', idempotency_key='another') | change
    before = services.store.latest_sequence()
    with pytest.raises(AuthorityRejected, match='launch'):
        services.execution.dispatch(None, **arguments)
    assert services.store.latest_sequence() == before


def test_state_and_prompt_list_only_this_projects_agent_items(triage):
    services, activation, commands, *_ = triage
    agent = item(triage)
    item(triage, owner='user', job='user')
    item(triage, project='other', job='other')
    state = commands.execute('state', {})
    assert [entry['id'] for entry in state['items']] == [agent.id]
    assert state['items'][0]['stream_context']['step'] == 0
    prompt = triage_prompt(activation, services.authority.triage_mandate(activation.id), [agent.id])
    assert activation.id in prompt and activation.mandate_version in prompt and agent.id in prompt
    assert 'scope="refused"' in prompt and 'take-back' in prompt


def test_resolve_and_escalate_record_item_decisions_and_owner_history(triage):
    services, activation, commands, *_ = triage
    resolved = item(triage)
    result = commands.execute('resolve', dict(item=resolved.id, details='job recovered', principle='observed success'))
    assert_decision(triage, result, resolved, 'resolve_attention')
    assert services.attention.get(resolved.id).state == 'resolved'
    escalated = item(triage, job='other')
    sequence = services.store.latest_sequence()
    result = commands.execute('escalate', dict(item=escalated.id, reason='requires a push', principle='no push authority'))
    decision = assert_decision(triage, result, escalated, 'escalate')
    assert decision.answer == 'escalated to the user: requires a push'
    current = services.attention.get(escalated.id)
    assert (current.owner, current.owner_reason, current.owner_actor) == ('user', 'requires a push', activation.actor)
    assert any(row['subject'] == f'attention:{escalated.id}:owner' for row in services.store.history_after(sequence))


def test_explicit_decision_affects_the_items_work(triage):
    services, activation, commands, *_ = triage
    work = services.work.add(project=activation.project, title='Task', goal='Task', actor='user')
    attention = item(triage, work_item=work.id)
    result = commands.execute('record_decision', dict(item=attention.id, question='Why?', answer='Transient failure',
                                                     context='logs inspected', principle='mandate'))
    decision = assert_decision(triage, result, attention, 'record_decision')
    assert json.loads(decision.context)['request']['context'] == 'logs inspected'
    assert services.attention.get(attention.id).state == 'open'


def test_retry_stored_run_queues_claim_and_records_decision_atomically(triage):
    services, activation, commands, calls, body, _ = triage
    target = services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor='user', reason='test', idempotency_key='target').run
    services.execution.observe('carbon', JobObservation(target.remote_job_id, 'failed', 'codex', None, None, None))
    attention = item(triage, job=target.remote_job_id)
    result = commands.execute('retry', dict(item=attention.id, reason='transient'))
    decision = assert_decision(triage, result, attention, 'retry')
    assert result['run'] != target.id and result['created'] is True
    assert services.execution.get_run(result['run']).action == target.action
    assert 'queued' in decision.answer and calls == []
    assert services.attention.get(attention.id).state == 'resolved'


def test_legacy_retry_is_recorded_before_send_and_cannot_repeat_after_lost_reply(triage):
    services, _, commands, calls, *_ = triage
    attention = item(triage)

    def fail(request):
        assert len(services.decisions.list()) == 1
        calls.append(request)
        raise RuntimeError('reply lost')

    services.execution.step = fail
    with pytest.raises(RuntimeError, match='reply lost'):
        commands.execute('retry', dict(item=attention.id, reason='transient'))
    assert services.attention.get(attention.id).state == 'open'
    with pytest.raises(AuthorityRejected, match='already requested'):
        commands.execute('retry', dict(item=attention.id, reason='try again'))
    assert len(calls) == len(services.decisions.list()) == 1


def test_legacy_retry_success_records_request_and_confirmation(triage):
    services, _, commands, calls, *_ = triage
    attention = item(triage, status='lost')
    result = commands.execute('retry', dict(item=attention.id, reason='worker reported lost'))
    assert_decision(triage, result, attention, 'retry')
    assert calls[0].retry is True and services.attention.get(attention.id).state == 'resolved'
    assert 'retry submitted' in services.attention.get(attention.id).resolution_details


@pytest.mark.parametrize('status,answers', [('failed', None), ('blocked', 0)])
def test_add_step_keeps_title_and_answers_blocked_step(triage, status, answers):
    services, _, commands, calls, *_ = triage
    attention = item(triage, status=status)
    result = commands.execute('add_step', dict(item=attention.id, prompt='Use the local fixture', title='Fix test'))
    assert_decision(triage, result, attention, 'add_step')
    assert (calls[0].prompt, calls[0].title, calls[0].answers, calls[0].key) == (
        'Use the local fixture', 'Fix test', answers, f'{attention.id}:add_step')
    assert services.attention.get(attention.id).state == 'resolved'


def test_grant_uses_pinned_rules_and_records_current_batch(triage):
    services, activation, commands, calls, body, _ = triage
    attention = refusal(triage)
    body['permissions']['allow'] = []
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='remove', actor='user')
    result = commands.execute('grant', dict(item=attention.id, scope='refused'))
    assert_decision(triage, result, attention, 'grant')
    assert calls[0].rules == ('Read',) and calls[0].key == f'{attention.id}:refused'
    assert services.attention.get(attention.id).state == 'resolved'


@pytest.mark.parametrize('rules,denied,scope', [(['Read'], [], 'bash'), (['Edit'], [], 'refused'),
    (['Bash(git push:*)'], [], 'refused'), (['Read'], ['deny Read'], 'refused'), ([], [], 'refused')])
def test_grants_reject_outside_authority_without_effects(triage, rules, denied, scope):
    services, _, commands, calls, *_ = triage
    attention = refusal(triage, rules=rules, denied=denied)
    if attention.owner == 'user':
        services.attention.delegate(attention.id, actor='user')
    before = services.store.latest_sequence()
    with pytest.raises((AuthorityRejected, ValueError)):
        commands.execute('grant', dict(item=attention.id, scope=scope))
    assert calls == [] and services.decisions.list() == [] and services.store.latest_sequence() == before


def test_new_refusal_in_existing_agent_batch_is_rechecked(triage):
    services, _, commands, calls, *_ = triage
    attention = refusal(triage)
    refusal(triage, rules=['Edit'], occurrence='r2')
    assert services.attention.get(attention.id).owner == 'agent'
    with pytest.raises(AuthorityRejected, match='not allowed'):
        commands.execute('grant', dict(item=attention.id, scope='refused'))
    assert calls == [] and services.decisions.list() == []


def test_takeback_during_grant_keeps_owner_and_audits_completed_effect(triage):
    services, _, commands, _, *_ = triage
    attention = refusal(triage)

    def take(request):
        services.attention.take(attention.id, actor='user', reason='I will handle this')
        return GrantResult(request.rules, 1)

    services.execution.grant = take
    result = commands.execute('grant', dict(item=attention.id, scope='refused'))
    assert_decision(triage, result, attention, 'grant')
    current = services.attention.get(attention.id)
    assert current.owner == 'user' and current.state == 'open' and current.owner_reason == 'I will handle this'


@pytest.mark.parametrize('command,payload', [('resolve', {'details': 'fixed', 'principle': 'mandate'}),
    ('escalate', {'reason': 'needs user', 'principle': 'mandate'}), ('retry', {'reason': 'transient'}),
    ('add_step', {'prompt': 'fix', 'title': 'fix'}), ('grant', {'scope': 'refused'}),
    ('record_decision', {'question': 'Q', 'answer': 'A', 'context': '', 'principle': 'mandate'})])
@pytest.mark.parametrize('ownership', ['user', 'other-project', 'resolved'])
def test_every_command_rejects_invalid_item_scope(triage, command, payload, ownership):
    services, _, commands, calls, *_ = triage
    attention = item(triage, owner='user' if ownership == 'user' else 'agent',
                     project='other' if ownership == 'other-project' else None)
    if ownership == 'resolved':
        services.attention.resolve(attention.id, details='handled', actor='user')
    before = services.store.latest_sequence()
    with pytest.raises(AuthorityRejected):
        commands.execute(command, dict(item=attention.id, **payload))
    assert calls == [] and services.decisions.list() == [] and services.store.latest_sequence() == before


def test_no_legacy_work_commands_or_actor_spoofing(triage):
    services, activation, commands, *_ = triage
    attention = item(triage)
    before = services.store.latest_sequence()
    for command in ('dispatch', 'progress', 'meet', 'delegate', 'summary', 'attention', 'propose'):
        with pytest.raises(AuthorityRejected):
            commands.execute(command, {})
    with pytest.raises(ValueError, match='fields'):
        commands.execute('resolve', dict(item=attention.id, details='fixed', principle='mandate', actor='user'))
    with pytest.raises(AuthorityRejected, match='actor'):
        services.authority.require_triage('retry', attention, actor='other', activation=activation.id)
    with pytest.raises(AuthorityRejected, match='controls'):
        services.authority.require('record_decision', None, actor=activation.actor, activation=activation.id)
    assert services.store.latest_sequence() == before


def test_blank_escalation_and_wrong_kinds_are_rejected(triage):
    services, _, commands, calls, *_ = triage
    attention = item(triage, status='stalled')
    before = services.store.latest_sequence()
    with pytest.raises(ValueError, match='reason'):
        commands.execute('escalate', dict(item=attention.id, reason=' ', principle='mandate'))
    for command, fields in [('retry', dict(reason='transient')), ('add_step', dict(prompt='fix', title='fix'))]:
        with pytest.raises(AuthorityRejected):
            commands.execute(command, dict(item=attention.id, **fields))
    assert calls == [] and services.store.latest_sequence() == before


def test_triage_cannot_retry_its_own_run(triage):
    services, _, commands, calls, _, run = triage
    attention = item(triage, job=run.remote_job_id)
    with pytest.raises(AuthorityRejected, match='triage run'):
        commands.execute('retry', dict(item=attention.id, reason='transient'))
    result = commands.execute('escalate', dict(item=attention.id, reason='triage failed', principle='no loops'))
    assert_decision(triage, result, attention, 'escalate')
    assert calls == [] and services.attention.get(attention.id).owner == 'user'


def test_decision_failure_rolls_back_escalation(triage, monkeypatch):
    services, _, commands, *_ = triage
    attention = item(triage)
    before = services.store.latest_sequence()

    def fail(*args):
        raise RuntimeError('decision insert failed')

    monkeypatch.setattr(type(services.decisions.repository), 'insert', fail)
    with pytest.raises(RuntimeError, match='insert failed'):
        commands.execute('escalate', dict(item=attention.id, reason='needs user', principle='mandate'))
    assert services.attention.get(attention.id).owner == 'agent'
    assert services.decisions.list() == [] and services.store.latest_sequence() == before


def test_cli_retry_delivers_the_queued_run(triage, monkeypatch, capsys):
    services, activation, _, _, body, _ = triage
    target = services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor='user', reason='test', idempotency_key='target').run
    services.execution.observe('carbon', JobObservation(target.remote_job_id, 'failed', 'codex', None, None, None))
    attention = item(triage, job=target.remote_job_id)
    delivered = []
    monkeypatch.setattr(cli, 'deliver_dispatch', lambda run, **kwargs: delivered.append(run.id))
    cli.main(['control', activation.id, 'retry', json.dumps(dict(item=attention.id, reason='transient'))])
    result = json.loads(capsys.readouterr().out)
    assert delivered == [result['run']] and delivered[0] != target.id


def test_retry_limit_follows_action_across_new_remote_job_ids(triage):
    services, activation, commands, _, body, _ = triage
    run = services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor='user', reason='test', idempotency_key='target').run
    action = run.action
    decisions = []
    for _ in range(2):
        services.execution.observe('carbon', JobObservation(run.remote_job_id, 'failed', 'codex', None, None, None))
        attention = item(triage, job=run.remote_job_id)
        result = commands.execute('retry', dict(item=attention.id, reason='transient'))
        decision = assert_decision(triage, result, attention, 'retry')
        assert json.loads(decision.context)['retry_action'] == action
        decisions.append(decision.id)
        run = services.execution.get_run(result['run'])
    services.execution.observe('carbon', JobObservation(run.remote_job_id, 'failed', 'codex', None, None, None))
    attention = item(triage, job=run.remote_job_id)
    before = services.store.latest_sequence()
    with pytest.raises(AuthorityRejected, match='retry limit 2') as error:
        commands.execute('retry', dict(item=attention.id, reason='again'))
    assert all(identity in str(error.value) for identity in decisions)
    assert services.store.latest_sequence() == before and services.attention.get(attention.id).state == 'open'


def test_retry_insert_failure_rolls_back_queued_claim(triage, monkeypatch):
    services, activation, commands, _, body, _ = triage
    target = services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor='user', reason='test', idempotency_key='target').run
    services.execution.observe('carbon', JobObservation(target.remote_job_id, 'failed', 'codex', None, None, None))
    attention = item(triage, job=target.remote_job_id)
    before = services.store.latest_sequence()
    runs, claims = services.execution.runs(), services.execution.claims()

    def fail(*args):
        raise RuntimeError('decision insert failed')

    monkeypatch.setattr(type(services.decisions.repository), 'insert', fail)
    with pytest.raises(RuntimeError, match='insert failed'):
        commands.execute('retry', dict(item=attention.id, reason='transient'))
    assert services.execution.runs() == runs and services.execution.claims() == claims
    assert services.store.latest_sequence() == before and services.attention.get(attention.id).state == 'open'


def test_mandate_must_authorize_the_specific_command(triage):
    services, activation, _, calls, body, _ = triage
    body['decision_authority'] = ['escalate']
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='restricted', actor='user')
    restricted = services.authority.activate(project=activation.project, actor='triage:restricted', role='triage', mandate_path=TRIAGE_PATH)
    services.execution.dispatch(None, project=activation.project, host='carbon', runtime='codex',
        payload={'cwd': body['cwd']}, actor=restricted.actor, activation=restricted.id,
        reason='Triage', idempotency_key=restricted.id)
    commands = ControllerCommands(services.store, restricted.id)
    attention = item(triage)
    before = services.store.latest_sequence()
    with pytest.raises(AuthorityRejected, match='does not authorize retry'):
        commands.execute('retry', dict(item=attention.id, reason='transient'))
    assert calls == [] and services.store.latest_sequence() == before


def test_grant_completion_does_not_consume_refusals_added_during_send(triage):
    services, _, commands, _, *_ = triage
    attention = refusal(triage)

    def add_request(request):
        refusal(triage, rules=['Edit'], occurrence='r2')
        return GrantResult(request.rules, 1)

    services.execution.grant = add_request
    result = commands.execute('grant', dict(item=attention.id, scope='refused'))
    assert_decision(triage, result, attention, 'grant')
    assert services.attention.get(attention.id).state == 'open'


def test_undispatched_activation_cannot_send_or_mutate(triage):
    services, activation, _, calls, *_ = triage
    new = services.authority.activate(project=activation.project, actor='triage:new', role='triage', mandate_path=TRIAGE_PATH)
    attention = item(triage)
    before = services.store.latest_sequence()
    with pytest.raises(AuthorityRejected, match='source run'):
        ControllerCommands(services.store, new.id).execute('retry', dict(item=attention.id, reason='transient'))
    assert calls == [] and services.store.latest_sequence() == before
