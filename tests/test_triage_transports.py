import argparse
import json
from io import StringIO

import pytest

from fleet.container import configured_container
from fleet import transport
from fleet.modules.execution import StepRequest
from fleet.remote import fleetd


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, 'JOBS_DIRECTORY', tmp_path / 'jobs')
    directory = tmp_path / 'jobs' / 'j'
    directory.mkdir(parents=True)
    job = dict(id='j', project='p', agent='claude', cwd=str(tmp_path), permission='default',
               description='triage test', steps=[fleetd.make_step(0, 'original', 'Original')])
    job['steps'][0]['status'] = 'blocked'
    job['steps'][0]['work_item'] = 'work'
    (directory / 'job.json').write_text(json.dumps(job))
    launches = []
    monkeypatch.setattr(fleetd, 'launch_runner', launches.append)
    return directory, launches


def test_keyed_grant_rejects_changed_rules(worker, monkeypatch, capsys):
    directory, launches = worker
    arguments = argparse.Namespace(job='j', step=0, key='grant', schema_version=1)
    for _ in range(2):
        monkeypatch.setattr(fleetd.sys, 'stdin', StringIO('["Read"]'))
        fleetd.command_grant(arguments)
    assert launches == ['j']
    before = (directory / 'job.json').read_text()
    capsys.readouterr()
    monkeypatch.setattr(fleetd.sys, 'stdin', StringIO('["Edit"]'))
    with pytest.raises(SystemExit):
        fleetd.command_grant(arguments)
    assert 'changed payload' in capsys.readouterr().out
    assert (directory / 'job.json').read_text() == before and launches == ['j']


def test_keyed_add_rejects_changed_prompt_and_preserves_inherited_work(worker, tmp_path, capsys):
    directory, launches = worker
    path = tmp_path / 'steps.json'
    path.write_text(json.dumps([dict(prompt='reply', title='Reply')]))
    arguments = argparse.Namespace(job='j', steps_file=str(path), retry=False, hold=False,
                                    key='add', answers=0, schema_version=1)
    for _ in range(2):
        fleetd.command_add(arguments)
    assert launches == ['j'] and fleetd.read_job('j')['steps'][1]['work_item'] == 'work'
    before = (directory / 'job.json').read_text()
    path.write_text(json.dumps([dict(prompt='different', title='Reply')]))
    capsys.readouterr()
    with pytest.raises(SystemExit):
        fleetd.command_add(arguments)
    assert 'changed payload' in capsys.readouterr().out
    assert (directory / 'job.json').read_text() == before and launches == ['j']


@pytest.mark.parametrize('answers', [None, 0])
def test_step_transport_sends_key_and_validates_worker_confirmation(monkeypatch, answers):
    calls = []
    request = StepRequest('carbon', 'j', 'key', 'reply', 'Reply', answers)
    monkeypatch.setattr(transport, 'host_by_name', lambda name: name)

    def call(host, arguments, *, stdin_text):
        calls.append((host, arguments, json.loads(stdin_text)))
        return dict(schema_version=1, key='key', status='applied', answers=answers, steps=[2])

    monkeypatch.setattr(transport, 'call', call)
    sender = configured_container().step()
    assert sender(request) == 'added step 3 to job j'
    host, arguments, steps = calls[0]
    assert host == 'carbon' and arguments[:2] == ['add', 'j'] and '--key' in arguments
    assert ('--answers' in arguments) is (answers is not None)
    assert steps == [dict(prompt='reply', title='Reply')]
    monkeypatch.setattr(transport, 'call', lambda *args, **kwargs: dict(schema_version=1, key='wrong', status='applied'))
    with pytest.raises(transport.FleetError, match='confirm'):
        sender(request)


def test_legacy_retry_transport_is_explicit_and_confirms_job(monkeypatch):
    calls = []
    monkeypatch.setattr(transport, 'host_by_name', lambda name: name)

    def call(host, arguments, *, stdin_text):
        calls.append((arguments, stdin_text))
        return dict(id='j', status='queued')

    monkeypatch.setattr(transport, 'call', call)
    assert 'queued' in configured_container().step()(StepRequest('carbon', 'j', 'key', retry=True))
    assert calls == [(['add', 'j', '--steps-file', '/dev/stdin', '--retry'], '[]')]
