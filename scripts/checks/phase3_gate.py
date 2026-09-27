"""Operator-run Phase 3 scenario beneath a real slice, with faked hosts."""

import argparse
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from fleet import composition
from fleet.modules.authority import AuthorityRejected
from fleet.modules.execution import JobObservation
from fleet.modules.work import EvidenceSpecification
from fleet.orchestration import ControllerCommands


def run_scenario(store, slice_id: str, directory: Path) -> dict:
    services = composition.facades(store)
    parent = services.work.get(slice_id)
    if parent.kind != 'milestone':
        raise ValueError('the slice must be a milestone')
    item = services.work.add(project=parent.project, parent=parent.id, kind='milestone',
        title='Phase 3 gate', goal='Verify delegated acceptance', actor='user')
    judged = services.work.add_criterion(item.id, text='Routine review', verification='judged', actor='user')
    accepted = services.work.add_criterion(item.id, text='User acceptance', verification='accepted', actor='user')
    checked = services.work.add_criterion(item.id, text='Recorded test result', verification='checked',
        specification=EvidenceSpecification(str(directory / 'absent-evidence')), actor='user')
    path = f'phase3/{uuid4()}.json'
    services.records.write_mandate(item.project, path, json.dumps(dict(goal=item.goal,
        constraints=[], escalation_conditions=['Reserved acceptance'], criteria_it_may_judge=[judged.id],
        decision_authority=['dispatch', 'update_progress', 'record_decision'])), key=path, actor='user')
    activation = services.authority.activate(item.id, actor='orchestrator', role='orchestrator', mandate_path=path)
    commands = ControllerCommands(store, activation.id)
    run = commands.execute('dispatch', dict(host='phase3-fake-controller', runtime='codex',
        payload={'cwd': str(directory)}, reason='Scripted orchestrator', idempotency_key=activation.id)).run
    before_attention = services.attention.list(project=item.project)
    commands.execute('progress', {'next_step': 'Ask user to review acceptance'})
    commands.execute('meet', {'criterion': judged.id, 'evidence': []})
    decision = commands.execute('decide', dict(question='Implementation approach?',
        answer='Reuse existing adapters', context='Routine choice within mandate'))
    assert services.attention.list(project=item.project) == before_attention
    assert decision.activation == activation.id and decision.mandate_version == activation.mandate_version
    assert decision.source_run == run.id

    for criterion in (accepted, checked):
        sequence = store.latest_sequence()
        try:
            commands.execute('meet', {'criterion': criterion.id, 'evidence': []})
        except AuthorityRejected as error:
            reason = str(error)
        else:
            raise AssertionError('unevidenced or user-reserved criterion was accepted')
        assert store.latest_sequence() == sequence
        assert services.work.criterion(criterion.id).state == 'unmet'
        commands.execute('propose', dict(question=f'Review {criterion.text}?',
            change=f'Acceptance of criterion {criterion.id}', reason=reason))

    failed = commands.execute('dispatch', dict(host='phase3-fake-worker', runtime='codex',
        payload={'cwd': str(directory)}, reason='Test failure', idempotency_key=activation.id + '-failure')).run
    services.execution.observe(failed.host, JobObservation(failed.remote_job_id, 'failed', 'codex', None, None, None))
    observation = JobObservation(run.remote_job_id, 'done', 'codex', None, None, None)
    services.execution.observe(run.host, observation)
    sequence = store.latest_sequence()
    services.execution.observe(run.host, observation)
    assert store.latest_sequence() == sequence
    assert services.execution.get_run(run.id).status == 'succeeded'
    try:
        commands.execute('progress', {'condition': 'complete'})
    except AuthorityRejected:
        pass
    else:
        raise AssertionError('successful run closed work without evidence and authority')
    assert services.work.get(item.id).condition != 'complete'

    # Reopen everything: the reviewer has only persisted records, never host state.
    reopened = composition.open_store(store.path)
    sequence = reopened.latest_sequence()
    state = ControllerCommands(reopened, activation.id).execute('state', {})
    def find(nodes):
        for node in nodes:
            if node['id'] == item.id:
                return node
            match = find(node['children'])
            if match is not None:
                return match
        return None
    node = find(state['work_items'])
    report = dict(working_on=node['goal'], complete=node['progress'], next=node['next_step'],
        failed=[entry['id'] for entry in node['runs'] if entry['status'] == 'failed'],
        needs_user=[entry['headline'] for entry in node['attention'] if entry['owner'] == 'user'])
    assert report['working_on'] == item.goal
    assert report['complete'] == dict(basis='criteria', complete=1, total=3)
    assert report['next'] == 'Ask user to review acceptance'
    assert report['failed'] == [failed.id]
    assert len(report['needs_user']) == 2 and node['interruptions'] == 2
    assert node['decisions'][0]['id'] == decision.id
    assert reopened.latest_sequence() == sequence
    return report


def main():
    parser = argparse.ArgumentParser(description='Run the scripted Phase 3 gate against a real slice. '
        'Persists a Phase 3 gate milestone beneath it, a gate-specific mandate and routine decision in its '
        'registered management repository, two proposals for the user, and faked host outcomes. '
        'Requires a writable store and clean management repository. Does not contact hosts or run an AI agent. '
        'Gate records remain for review; each invocation adds a new gate milestone. No M3 baseline exists yet.')
    parser.add_argument('slice', help='real milestone work-item ID in the configured Fleet store')
    args = parser.parse_args()
    store = composition.open_store()
    with tempfile.TemporaryDirectory(prefix='fleet-phase3-') as temporary:
        directory = Path(temporary)
        print(json.dumps(run_scenario(store, args.slice, directory), indent=2))
    print('PASS: Phase 3 gate; two deliberate interruptions, no baseline comparison.')


if __name__ == '__main__':
    main()
