import json
import subprocess

import pytest

from fleet import composition
from fleet.modules.authority import AuthorityRejected
from fleet.modules.work import EvidenceSpecification


@pytest.fixture
def orchestration(tmp_path):
    store = composition.open_store()
    work = composition.open_work(store)
    item = work.add(project='p', title='Ship', goal='Ship', actor='user')
    judged = work.add_criterion(item.id, text='Review', verification='judged', actor='user')
    accepted = work.add_criterion(item.id, text='Accept', verification='accepted', actor='user')
    checked = work.add_criterion(item.id, text='Test', verification='checked', actor='user',
                                 specification=EvidenceSpecification(str(tmp_path / 'result')))
    root = tmp_path / 'records'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True, timeout=10)
    records = composition.open_records(store)
    records.register('p', root, actor='user')
    records.write_mandate('p', 'mandate.json', json.dumps(dict(goal='Ship', constraints=[],
        escalation_conditions=[], criteria_it_may_judge=[judged.id],
        decision_authority=['dispatch', 'update_progress', 'raise_attention', 'record_decision', 'summary'])),
        key='mandate', actor='user')
    authority = composition.open_authority(store)
    activation = authority.activate(item.id, actor='orchestrator', role='orchestrator', mandate_path='mandate.json')
    run = composition.open_execution(store).dispatch(item.id, actor=activation.actor, activation=activation.id,
        host='local', runtime='codex', payload={'cwd': str(tmp_path)}, reason='Orchestrate',
        idempotency_key=activation.id).run
    from fleet.orchestration import ControllerCommands
    return ControllerCommands(store, activation.id), store, item, judged, accepted, checked, run, root


def test_scripted_routine_decision_summary_and_projection(orchestration):
    commands, store, item, judged, _, _, run, root = orchestration
    commands.execute('progress', {'next_step': 'Review'})
    commands.execute('meet', {'criterion': judged.id, 'evidence': []})
    decision = commands.execute('decide', {'question': 'Approach?', 'answer': 'Use the existing adapter', 'context': 'Routine'})
    commands.execute('summary', dict(purpose='Ship', done='Reviewed', doing='Test', next='Accept'))
    assert composition.open_attention(store).list() == []
    assert decision.activation == commands.activation.id
    assert decision.mandate_version == commands.activation.mandate_version
    assert decision.source_run == run.id
    assert decision.principle is None and decision.guidance is None
    assert commands.execute('state', {})['work_items'][0]['next_step'] == 'Review'
    assert composition.open_work(store).criterion(judged.id).activation == commands.activation.id
    intents = composition.open_records(store).intents()
    authored = [entry for entry in intents if entry['source_run'] == run.id]
    assert len(authored) == 2 and all(entry['state'] == 'confirmed' for entry in authored)
    summary = json.loads((root / f'summaries/{item.id}.json').read_text())
    assert summary['activation'] == commands.activation.id
    assert summary['mandate_version'] == commands.activation.mandate_version
    for entry in authored:
        log = subprocess.run(['git', '-C', str(root), 'show', '-s', '--format=%B', entry['revision']],
                             check=True, capture_output=True, text=True, timeout=10).stdout
        assert run.id in log
    assert decision.id in (root / f'decisions/{decision.id}.json').read_text()
    principled = commands.execute('decide', dict(question='Retry?', answer='Once', context='Flaky',
                                                 principle='Mandate: decision authority'))
    recorded = json.loads((root / f'decisions/{principled.id}.json').read_text())
    assert recorded['principle'] == 'Mandate: decision authority' and recorded['guidance'] is None


def test_rejected_attempt_is_quiet_until_explicit_proposal(orchestration):
    commands, store, _, _, accepted, checked, _, _ = orchestration
    before = store.latest_sequence()
    for criterion in (accepted, checked):
        with pytest.raises(AuthorityRejected):
            commands.execute('meet', {'criterion': criterion.id, 'evidence': []})
    assert store.latest_sequence() == before
    assert composition.open_attention(store).list() == []
    commands.execute('propose', dict(question='Accept?', change='Accept release', reason='Reserved for user'))
    assert len(composition.open_attention(store).list()) == 1


def test_dispatch_and_attention_keep_activation_context(orchestration):
    commands, store, item, _, _, _, _, _ = orchestration
    dispatched = commands.execute('dispatch', dict(host='worker', runtime='codex', payload={'cwd': '/repo'},
        reason='Implement', idempotency_key='implementation'))
    action = composition.open_execution(store).get_action(dispatched.run.action)
    assert (action.work_item, action.activation, action.mandate_version) == (
        item.id, commands.activation.id, commands.activation.mandate_version)
    raised = commands.execute('attention', dict(headline='Review', context_reference='review'))
    assert raised.source_reference == commands.activation.id + ':review'


def test_successful_run_cannot_close_unevidenced_work(orchestration):
    from fleet.modules.execution import JobObservation
    commands, store, item, _, _, _, run, _ = orchestration
    composition.open_execution(store).observe(run.host, JobObservation(run.remote_job_id, 'done', 'codex', None, None, None))
    with pytest.raises(AuthorityRejected, match='criteria'):
        commands.execute('progress', {'condition': 'complete'})
    assert composition.open_work(store).get(item.id).condition != 'complete'


@pytest.mark.parametrize('command,payload', [
    ('progress', {'next_step': 'Go'}), ('meet', {'criterion': 'missing', 'evidence': []}),
    ('attention', {'headline': 'Review', 'context_reference': 'review'}),
    ('dispatch', {'host': 'local', 'runtime': 'codex', 'payload': {'cwd': '/tmp'}, 'reason': 'Go', 'idempotency_key': 'child'}),
    ('decide', {'question': 'Q', 'answer': 'A', 'context': 'C'}),
    ('summary', {'purpose': 'P', 'done': 'D', 'doing': 'D', 'next': 'N'}),
    ('propose', {'question': 'Q', 'change': 'C', 'reason': 'R'}),
])
def test_every_write_checks_authority(orchestration, monkeypatch, command, payload):
    commands, store, _, judged, _, _, _, _ = orchestration
    if command == 'meet':
        payload['criterion'] = judged.id
    calls = []
    def reject(name, work_item, **context):
        calls.append((name, context))
        raise AuthorityRejected('test rejection')
    monkeypatch.setattr(composition.open_authority(store), 'require', reject)
    before = store.latest_sequence()
    with pytest.raises(AuthorityRejected, match='test rejection'):
        commands.execute(command, payload)
    assert calls[0][1]['activation'] == commands.activation.id
    assert store.latest_sequence() == before


def test_facades_are_shared_per_store(orchestration):
    _, store, _, _, _, _, _, _ = orchestration
    work = composition.open_work(store)
    assert work is composition.open_work(store)
    assert work.records is composition.open_records(store)
    assert composition.open_execution(store).work is work


def test_decision_commit_failure_keeps_atomic_intent(orchestration, monkeypatch):
    commands, store, _, _, _, _, run, _ = orchestration
    records = composition.open_records(store)
    def fail(root, intent, body):
        reopened = composition.facades(composition.open_store(store.path))
        decision, = reopened.decisions.list()
        pending = next(entry for entry in reopened.records.intents() if entry['key'] == decision.id)
        assert pending['state'] == 'pending'
        raise ValueError('commit refused')
    monkeypatch.setattr(records.writer, 'commit', fail)
    decision = commands.execute('decide', dict(question='Q', answer='A', context='C'))
    intent = next(entry for entry in records.intents() if entry['key'] == decision.id)
    assert intent['state'] == 'failed' and intent['error'] == 'commit refused'
    assert intent['source_run'] == run.id


def test_orchestrator_queries_own_run_without_scans(orchestration, monkeypatch):
    commands, store, _, _, _, _, run, _ = orchestration
    def no_scan():
        raise AssertionError('full scan')
    execution = composition.open_execution(store)
    monkeypatch.setattr(execution, 'actions', no_scan)
    monkeypatch.setattr(execution, 'runs', no_scan)
    decision = commands.execute('decide', dict(question='Q', answer='A', context='C'))
    assert decision.source_run == run.id
    with pytest.raises(LookupError):
        execution.activation_run('another-activation', commands.activation.id)


def test_decision_rolls_back_when_intent_cannot_be_saved(orchestration, monkeypatch):
    from fleet.infrastructure.sqlite.records import RecordsRepository
    commands, store, _, _, _, _, _, _ = orchestration
    before = store.latest_sequence()
    def fail(self, intent):
        raise ValueError('intent refused')
    monkeypatch.setattr(RecordsRepository, 'save', fail)
    with pytest.raises(ValueError, match='intent refused'):
        commands.execute('decide', dict(question='Q', answer='A', context='C'))
    assert composition.open_decisions(store).list() == []
    assert store.latest_sequence() == before


def test_decision_recovery_failure_preserves_commit_error(orchestration, monkeypatch):
    commands, store, _, _, _, _, _, _ = orchestration
    records = composition.open_records(store)
    def commit(*args):
        raise ValueError('original commit error')
    def find(*args):
        raise ValueError('recovery error')
    monkeypatch.setattr(records.writer, 'commit', commit)
    monkeypatch.setattr(records.writer, 'find', find)
    with pytest.raises(ValueError, match='original commit error'):
        commands.execute('decide', dict(question='Q', answer='A', context='C'))
    decision, = composition.open_decisions(store).list()
    intent = next(entry for entry in records.intents() if entry['key'] == decision.id)
    assert intent['state'] == 'failed' and intent['error'] == 'original commit error'


def test_complete_requires_separate_accept_authority(orchestration):
    commands, store, item, judged, accepted, checked, _, _ = orchestration
    work = composition.open_work(store)
    commands.execute('meet', {'criterion': judged.id, 'evidence': []})
    work.meet(accepted.id, actor='user')
    evidence = checked.specification.reference
    from pathlib import Path
    Path(evidence).write_text('passed')
    commands.execute('meet', {'criterion': checked.id, 'evidence': [evidence]})
    with pytest.raises(AuthorityRejected, match='accept'):
        commands.execute('progress', {'condition': 'complete'})
    records = composition.open_records(store)
    mandate = json.loads(records.read('p', 'mandate.json'))
    mandate['decision_authority'].append('accept')
    records.write_mandate('p', 'mandate.json', json.dumps(mandate), key='accept', actor='user')
    # Existing activations retain the old grant.
    with pytest.raises(AuthorityRejected, match='accept'):
        commands.execute('progress', {'condition': 'complete'})
    activation = composition.open_authority(store).activate(item.id, actor='orchestrator',
        role='orchestrator', mandate_path='mandate.json')
    from fleet.orchestration import ControllerCommands
    ControllerCommands(store, activation.id).execute('progress', {'condition': 'complete'})
    assert work.get(item.id).condition == 'complete'


def test_real_scripted_orchestrator_process(orchestration, tmp_path, monkeypatch, capsys):
    import os
    import sys
    from fleet import cli
    from fleet.remote import fleetd
    from fleet.transport import Host

    _, store, item, _, _, _, _, root = orchestration
    agent = tmp_path / 'scripted-agent'
    agent.write_text(f'''#!{sys.executable}
import contextlib, io, json, re, sys
from fleet import cli
activation = re.search(r"fleet control ([a-f0-9-]+) COMMAND", sys.argv[2]).group(1)
with contextlib.redirect_stdout(io.StringIO()):
    cli.main(['control', activation, 'progress', json.dumps(dict(next_step='User review'))])
    cli.main(['control', activation, 'decide', json.dumps(dict(question='Adapter?', answer='Reuse it', context='Routine'))])
    cli.main(['control', activation, 'summary', json.dumps(dict(purpose='Ship', done='Decided', doing='Review', next='Accept'))])
print(json.dumps(dict(type='result', subtype='success', result='Routine work recorded')))
''')
    agent.chmod(0o755)
    home = tmp_path / 'worker'
    home.mkdir()
    (home / 'config.json').write_text(json.dumps({'claude': str(agent)}))
    monkeypatch.setenv('FLEET_HOME', str(home))
    monkeypatch.setenv('PYTHONPATH', os.getcwd())
    monkeypatch.setattr(cli.transport, 'host_by_name', lambda name: Host(name, None))

    def call(host, arguments, *, stdin_text=None):
        if arguments[0] == 'start':
            # Own the runner in the foreground so the test cannot leave a process behind.
            subprocess.run([sys.executable, fleetd.__file__, '_run', arguments[1]],
                           check=True, capture_output=True, text=True, timeout=10)
            run_id = arguments[arguments.index('--run-id') + 1]
            arguments = ['reconcile', run_id, *arguments[arguments.index('--fingerprint'):]]
        reply = subprocess.run([sys.executable, fleetd.__file__, *arguments], input=stdin_text,
                               check=True, capture_output=True, text=True, timeout=10)
        return json.loads(reply.stdout)

    monkeypatch.setattr(cli.transport, 'call', call)
    cli.main(['orchestrate', item.id, '--mandate', 'mandate.json', '--host', 'controller',
              '--runtime', 'claude', '--cwd', str(tmp_path)])
    output = json.loads(capsys.readouterr().out)
    assert composition.open_execution(store).get_run(output['run']).status == 'succeeded'
    assert composition.open_work(store).get(item.id).condition != 'complete'
    decision, = composition.open_decisions(store).list()
    assert decision.source_run == output['run']
    assert composition.open_attention(store).list() == []
    assert json.loads((root / f'summaries/{item.id}.json').read_text())['activation'] == output['activation']
