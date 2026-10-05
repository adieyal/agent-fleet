import json
from types import SimpleNamespace

import pytest
from fleet.container import configured_container
from fleet_cli import cli
from tests.integration.test_triage_commands import triage, item
from tests.integration.test_triage_scheduler import scheduler, finish


def test_no_policy_delegation_rejected_without_owner_write(project_id, capsys):
    services = configured_container(configured_container().store()).services()
    a = services.attention.raise_item(project=project_id, owner='user', kind='blocker', source='test',
        source_reference='a', headline='a', context_reference='a', actor='user')
    before = services.store.history_after(0)
    with pytest.raises(ValueError, match='no confirmed triage mandate'):
        services.attention.delegate(a.id, actor='user')
    assert services.attention.get(a.id).owner == 'user'
    assert services.store.history_after(0) == before
    with pytest.raises(SystemExit):
        cli.main(['attention', 'delegate', a.id, '--actor', 'user'])
    assert 'no confirmed triage mandate' in capsys.readouterr().err
    assert services.attention.get(a.id).owner == 'user'


def test_budget_recovery_names_reset_policy_and_decision(triage):
    services, activation, _, _, body, _ = triage
    body['limits']['runs_per_day'] = 1
    from fleet.modules.records import TRIAGE_PATH
    services.records.write_mandate(activation.project, TRIAGE_PATH, json.dumps(body), key='budget', actor='user')
    a = item(triage)
    engine, calls = scheduler(triage)
    engine.schedule()
    finish(services, services.execution.get_run(calls[0][0]))
    engine.schedule()
    reason = services.attention.get(a.id).owner_reason
    assert 'resets at' in reason and 'fleet triage policy show' in reason
    assert 'Decide whether' in reason
    assert engine.status(a.project)['budget_resets_at']


def test_unreachable_recovery_does_not_offer_duplicate_dispatch(triage):
    services, *_ = triage
    a = item(triage)
    engine, calls = scheduler(triage)
    from fleet.errors import FleetError
    from datetime import timedelta
    def unavailable(*args, **kw):
        raise FleetError('connection refused')
    engine.deliver = unavailable
    engine.schedule()
    now = services.store.clock()
    services.store.clock = lambda: now + timedelta(minutes=31)
    engine.schedule()
    reason = services.attention.get(a.id).owner_reason
    assert 'fleet run show' in reason and 'Decide whether' in reason
    assert 'do not launch a duplicate' in reason


def test_human_status_and_json(triage, capsys):
    services, activation, *_ = triage
    a = item(triage)
    engine, _ = scheduler(triage)
    engine.delivery_error(a.project, 'connection refused')
    cli.main(['triage', 'status', a.project])
    output = capsys.readouterr().out
    assert 'Queue: 1' in output and 'connection refused' in output and 'Budget: 12' in output
    assert 'fleet triage policy show' in output
    cli.main(['triage', 'status', a.project, '--json'])
    assert json.loads(capsys.readouterr().out)['queue'] == [a.id]


def test_sample_parses_with_conservative_explicit_choices():
    from pathlib import Path
    from fleet.modules.records import TriageMandate
    text = Path('docs/triage-policy.md').read_text().split('```json\n')[1].split('```')[0]
    policy = TriageMandate.parse(text)
    assert policy.criteria_it_may_judge == []
    assert policy.decision_authority == ['record_decision', 'escalate']
    assert all(owner == 'user' for owner in policy.routing.values())
    assert policy.host == 'CHOOSE_CONTROLLER_HOST'


def test_handover_receipts_and_json(triage, capsys):
    services, *_ = triage
    a = item(triage, owner='user')
    cli.main(['attention', 'delegate', a.id, '--actor', 'user'])
    assert 'Delegated; stays open' in capsys.readouterr().out
    cli.main(['attention', 'take', a.id, '--actor', 'user'])
    assert 'process may continue for other items' in capsys.readouterr().out
    cli.main(['attention', 'delegate', a.id, '--actor', 'user', '--json'])
    assert json.loads(capsys.readouterr().out)['owner'] == 'agent'
    cli.main(['triage', 'policy', 'show', a.project, '--json'])
    assert json.loads(capsys.readouterr().out)['policy']['criteria_it_may_judge'] == []
