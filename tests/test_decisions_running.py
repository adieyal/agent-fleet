"""New decisions cross controller/worker boundaries without interrupting a step."""
import argparse
import json
import sys
from datetime import datetime, timezone
from io import StringIO

import pytest

from fleet.container import configured_container
from fleet import transport
from fleet.orchestration import guide
from fleet.remote import fleetd


@pytest.mark.parametrize('scope', ['linked', 'ancestor'])
def test_decision_retries_after_restart_and_dispatch_excludes_history(project_id, monkeypatch, scope):
    services = configured_container().services()
    epic = services.work.add(project=project_id, title='Epic', goal='Ship', actor='user', kind='epic')
    task = services.work.add(project=project_id, title='Task', goal='Ship', actor='user', parent=epic.id)
    other = services.work.add(project=project_id, title='Other', goal='Ship', actor='user')
    early = services.decisions.record_guided(epic.id, actor='user', question='Earlier?', answer='Yes', principle='Brief')
    payload, _ = guide(services.records, task.id, {'steps': [{'prompt': 'Build'}]})
    assert early.answer in payload['steps'][0]['prompt']
    services.workspace.edit_registry(lambda registry: registry.link(project_id, 'fake', 'p'))
    payload['cwd'] = '/repo'
    run = services.execution.dispatch(task.id, host='fake', runtime='codex', payload=payload,
        actor='user', reason='Build', idempotency_key='test').run
    calls = []
    monkeypatch.setattr(transport, 'host_by_name', lambda name: transport.Host(name, None))
    def offline(host, arguments, **kwargs):
        calls.append(arguments)
        raise transport.FleetError('offline')
    monkeypatch.setattr(transport, 'call', offline)
    recent = services.decisions.record_streamed('late-stream', datetime(2020, 1, 1, tzinfo=timezone.utc),
        task.id if scope == 'linked' else epic.id, actor='user', question='Now?', answer='Use blue', principle='Brief', context='', source_run=None)
    assert calls == []  # Stream ingestion persists intent without transport under the state lock.
    services.decisions.record_guided(other.id, actor='user', question='Unrelated?', answer='No', principle='Brief')
    services.execution.retry_decisions()
    delivery, = services.execution.deliveries()
    assert delivery.decision == recent.id
    assert delivery.status == 'pending'
    assert calls[0][0] == 'receive-decision'
    from fleet_web.server import FleetState
    state = FleetState([transport.Host('fake', None)], container=configured_container(store=services.store))
    view = state.with_work({'hosts': [dict(name='fake', jobs=[dict(id=run.remote_job_id)], sessions=[])]})
    received, = view['hosts'][0]['jobs'][0]['decisions_since_dispatch']
    assert received['id'] == recent.id
    assert received['delivery_status'] == 'pending'
    assert received['delivery_error'] == 'offline'
    received_keys = []
    def online(host, arguments, **kwargs):
        received_keys.append(arguments[-1])
        return {'schema_version': 1, 'key': arguments[-1], 'status': 'applied'}
    monkeypatch.setattr(transport, 'call', online)
    restarted = configured_container(configured_container().store()).services()
    restarted.execution.retry_deliveries()
    assert restarted.execution.deliveries()[0].status == 'applied'
    sequence = restarted.store.latest_sequence()
    restarted.execution.retry_deliveries()
    assert restarted.store.latest_sequence() == sequence
    assert received_keys == [delivery.key]


def test_worker_receipt_and_step_boundary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fleetd, 'JOBS_DIRECTORY', tmp_path)
    folder = tmp_path / 'j'
    folder.mkdir()
    step = fleetd.make_step(0, 'Build', 'Build')
    (folder / 'job.json').write_text(json.dumps({'id': 'j', 'steps': [step]}))
    fleetd.write_briefs('j', [step])
    def receive(identity):
        decision = dict(id=identity, question='Colour?', answer='Blue', actor='user', principle='Brief')
        monkeypatch.setattr(sys, 'stdin', StringIO(json.dumps(decision)))
        fleetd.command_receive_decision(argparse.Namespace(job='j', key=identity, schema_version=1))
        capsys.readouterr()
    receive('d1')
    receive('d1')
    with fleetd.locked_job('j') as job:
        fleetd.inject_decisions(job, job['steps'][0])
    job = fleetd.read_job('j')
    assert job['shown_decisions'] == ['d1']
    assert job['steps'][0]['prompt'].count('Question: Colour?') == 1
    assert (folder / 'brief-0.md').read_text() == job['steps'][0]['prompt']
    receive('d2')
    with fleetd.locked_job('j') as job:
        second = fleetd.make_step(1, 'Verify', 'Verify')
        job['steps'].append(second)
        fleetd.inject_decisions(job, second)
    job = fleetd.read_job('j')
    assert job['steps'][1]['shown_decisions'] == ['d2']
    assert job['shown_decisions'] == ['d1', 'd2']
    with fleetd.locked_job('j') as job:
        third = fleetd.make_step(2, 'Finish', 'Finish')
        job['steps'].append(third)
        fleetd.inject_decisions(job, third)
    third = fleetd.read_job('j')['steps'][2]
    assert third['prompt'] == 'Finish'
    assert 'shown_decisions' not in third


def test_fleet_show_lists_received_decisions(monkeypatch, capsys, *, cli_container, override_cli_method):
    from fleet_cli import cli
    job = dict(id='j', project='p', description='Build', agent='codex', status='running',
               steps=[], cwd='/repo', permission='default', events=[],
               decisions_since_dispatch=[dict(id='d1', question='Colour?', answer='Blue',
                                              actor='user', principle='Brief')])
    override_cli_method('references', 'job', lambda ref: (transport.Host('fake', None), 'j'))
    monkeypatch.setattr(transport, 'call', lambda *args, **kwargs: job)
    cli.command_show(argparse.Namespace(job='fake:j', events=0, json=False), container=cli_container)
    output = capsys.readouterr().out
    for text in ('Decisions since dispatch: 1', 'Colour?', 'Blue', 'Actor: user', 'Principle: Brief'):
        assert text in output
