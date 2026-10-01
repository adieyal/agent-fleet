import json
from dataclasses import replace

import pytest

from fleet.composition import facades, open_store, open_workspace
from fleet.modules.attention import InputObservation, Refusal, StreamContext
from fleet.modules.attention.domain.routing import RoutingHistory, route
from fleet.modules.records import TRIAGE_PATH, TriageMandate


def body():
    return dict(goal='Triage jobs', constraints=[], decision_authority=['retry', 'grant', 'escalate'],
                escalation_conditions=[], criteria_it_may_judge=[], host='carbon', runtime='codex',
                cwd='/repo', permission='acceptEdits', routing={},
                permissions={'allow': ['Read', 'Bash(uv run pytest:*)'], 'escalate': ['Bash(git push:*)']},
                limits={'retries_per_step': 2, 'runs_per_day': 12, 'unclaimed_minutes': 30})


@pytest.fixture
def mandate():
    return TriageMandate.parse(json.dumps(body()))


def context(status='failed', owner_type='job'):
    return StreamContext('carbon', owner_type, 'j', 'p', None, f'job status {status}', status, 1, step=0)


@pytest.mark.parametrize('section,key', [(None, 'extra'), ('routing', 'question'),
                                       ('permissions', 'deny'), ('limits', 'budget')])
def test_unknown_keys_list_valid_keys(section, key):
    data = body()
    (data if section is None else data[section])[key] = 'x'
    with pytest.raises(ValueError, match='valid keys:'):
        TriageMandate.parse(json.dumps(data))


@pytest.mark.parametrize('change', [dict(runtime='other'), dict(goal=''), dict(limits={}),
                                  dict(decision_authority=['shell']), dict(permissions={'allow': [], 'escalate': 'Read'})])
def test_invalid_mandate(change):
    with pytest.raises(ValueError):
        TriageMandate.parse(json.dumps(body() | change))


@pytest.mark.parametrize('status,owner', [('failed', 'agent'), ('stalled', 'agent'),
                                       ('lost', 'agent'), ('blocked', 'user')])
def test_job_routing(mandate, status, owner):
    assert route('blocker', context(status), mandate)[0] == owner
    assert route('blocker', context(status), None)[0] == 'user'


@pytest.mark.parametrize('kind,ctx', [('decision', None), ('blocker', None), ('alert', context()),
                                    ('decision', context(owner_type='session'))])
def test_user_first_sources(mandate, kind, ctx):
    assert route(kind, ctx, mandate, reason='raiser reason') == ('user', 'raiser reason')


def test_blocked_override_and_guardrails(mandate):
    assert route('blocker', context('blocked'), replace(mandate, routing={'blocked': 'agent'}))[0] == 'agent'
    assert 'supervisor' in route('blocker', context('blocked'), mandate)[1]
    assert route('blocker', context(), mandate, RoutingHistory(triage_run=True))[0] == 'user'
    owner, reason = route('blocker', context(), mandate, RoutingHistory(retry_decisions=('d1', 'd2')))
    assert owner == 'user' and 'd1, d2' in reason


@pytest.mark.parametrize('rules,denied,owner', [(('Read',), (), 'agent'),
    (('Read', 'Bash(uv run pytest:*)'), (), 'agent'), (('Read', 'Edit'), (), 'user'),
    (('Bash(git push:*)',), (), 'user'), (None, (), 'user'), ((), (), 'user'),
    (('Read',), ('deny Read',), 'user')])
def test_refusal_policy(mandate, rules, denied, owner):
    refusal = Refusal('r', 'Read', '', '', rules, 1, denied)
    result = route('decision', context(), mandate, refusals=(refusal,))
    assert result[0] == owner
    if owner == 'user':
        assert result[1]
    assert route('decision', context(), None, refusals=(refusal,))[0] == 'user'


def test_escalate_overrides_allow_and_every_refusal_needs_rules(mandate):
    refusal = Refusal('r', 'Read', '', '', ('Read',), 1)
    overlap = replace(mandate, permissions={'allow': ['Read'], 'escalate': ['Read']})
    assert route('decision', context(), overlap, refusals=(refusal,))[0] == 'user'
    assert route('decision', context(), mandate, refusals=(refusal, replace(refusal, rules=())))[0] == 'user'
    assert route('decision', context(), mandate, RoutingHistory(triage_run=True), refusals=(refusal,))[0] == 'user'


def test_invalid_recorded_policy_is_visible(project_id):
    services = facades(open_store())
    open_workspace(services.store)
    services.records.write(project_id, TRIAGE_PATH, '{"bad": true}', key='bad', actor='test')
    with pytest.raises(ValueError, match='valid keys:'):
        services.attention.route(project_id, 'blocker', context())


def test_recorded_mandate_routes_only_new_occurrences(project_id):
    services = facades(open_store())
    open_workspace(services.store)
    host = dict(name='carbon', ok=True, sessions=[], jobs=[dict(id='j', project='p', project_id=project_id,
                status='failed', steps=[dict(index=0, status='failed', title='test', started_at=1,
                message=None, answered_by=None)], updated_at=1)])
    services.attention.observe(host)
    first, = services.attention.list()
    assert first.owner == 'user'
    result = services.records.write_mandate(project_id, TRIAGE_PATH, json.dumps(body()), key='triage', actor='test')
    assert result['state'] == 'confirmed'
    services.attention.observe(host)
    assert services.attention.get(first.id).owner == 'user'
    host['jobs'][0]['status'] = 'lost'
    host['jobs'][0]['steps'][0]['status'] = 'running'
    services.attention.observe(host)
    lost, = services.attention.list(state='open')
    assert lost.owner == 'agent' and lost.stream_context.step == 0
    services.attention.take(lost.id, actor='user', reason='I will handle it')
    services.attention.observe(host)
    assert services.attention.get(lost.id).owner == 'user'
    assert services.records.mandate_version(project_id, TRIAGE_PATH)[1].host == 'carbon'


def test_input_batch_routed_once_and_session_stays_user(project_id):
    services = facades(open_store())
    open_workspace(services.store)
    services.records.write_mandate(project_id, TRIAGE_PATH, json.dumps(body()), key='triage', actor='test')
    observation = InputObservation(1, 'claude', 'job', 'j', 's', 0, 'p', 'input_requested',
        'permission', 'PreToolUse', 'r1', 1, 'ctx', request={'tool': 'Read', 'rules': ['Read']})
    services.attention.observe_input('carbon', observation, project_id=project_id)
    first, = services.attention.list()
    assert first.owner == 'agent'
    services.attention.take(first.id, actor='user')
    services.attention.observe_input('carbon', replace(observation, source_event_id='r2', observed_at=2), project_id=project_id)
    assert services.attention.get(first.id).owner == 'user'
    services.attention.observe_input('carbon', replace(observation, owner_type='session', job_id=None), project_id=project_id)
    assert all(item.owner == 'user' for item in services.attention.list())
