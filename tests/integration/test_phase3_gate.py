import json
import subprocess

from fleet.container import configured_container
from fleet import cli, transport


def test_phase3_gate_from_persisted_slice(tmp_path, monkeypatch, capsys, project_id):
    from scripts.checks.phase3_gate import run_scenario

    store = configured_container().store()
    root = tmp_path / 'records'
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True, timeout=10)
    configured_container(store).records().register(project_id, root, actor='user')
    item = configured_container(store).work().add(project=project_id, title='Real slice', goal='Ship', kind='milestone', actor='user')
    def no_hosts(*args, **kwargs):
        raise AssertionError('scenario must use faked hosts')
    monkeypatch.setattr(transport, 'call', no_hosts)
    before = store.latest_sequence()
    attention = configured_container(store).initialized_attention().list()
    report = run_scenario(store, item.id, tmp_path)
    assert store.latest_sequence() == before
    assert configured_container(store).initialized_attention().list() == attention
    assert set(report) == {'working_on', 'complete', 'next', 'failed', 'needs_user'}
    assert report['complete'] == {'basis': 'criteria', 'complete': 1, 'total': 3}
    assert len(report['needs_user']) == 2
    cli.main(['status', 'p', '--json'])
    node = json.loads(capsys.readouterr().out)['work_items'][0]
    assert node['interruptions'] == 0


def test_status_counts_unique_user_items_for_slice_subtree(capsys, project_id):
    store = configured_container().store()
    work, attention = configured_container(store).work(), configured_container(store).initialized_attention()
    first = work.add(project=project_id, title='First', goal='Ship', kind='milestone', actor='user')
    child = work.add(project=project_id, title='Task', goal='Build', parent=first.id, actor='user')
    second = work.add(project=project_id, title='Second', goal='Ship', kind='milestone', actor='user')
    def raise_item(reference, item, owner='user'):
        return attention.raise_item(project=project_id, work_item=item, kind='decision', owner=owner,
            source='manual', source_reference=reference, headline='Choose', context_reference=reference,
            actor='user')
    resolved = raise_item('resolved', child.id)
    raise_item('resolved', child.id)
    attention.resolve(resolved.id, details='Answered', actor='user')
    raise_item('open', first.id)
    raise_item('agent', child.id, 'agent')
    raise_item('elsewhere', second.id)
    raise_item('project', None)
    reopened = configured_container().store()
    before = reopened.latest_sequence()
    cli.main(['status', 'p', '--json'])
    nodes = json.loads(capsys.readouterr().out)['work_items']
    assert {node['id']: node['interruptions'] for node in nodes} == {first.id: 2, second.id: 1}
    cli.main(['status', 'p'])
    output = capsys.readouterr().out
    assert 'Interruptions: 2' in output and 'Interruptions: 1' in output
    assert 'baseline' not in output.lower()
    assert reopened.latest_sequence() == before


def test_operator_runner_targets_supplied_slice(tmp_path, monkeypatch, capsys):
    from scripts.checks.phase3_gate import main

    store = configured_container().store()
    root = tmp_path / 'records'
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True, timeout=10)
    records = configured_container(store).records()
    records.register('p', root, actor='user')
    item = configured_container(store).work().add(project='p', title='Slice', goal='Ship', kind='milestone', actor='user')
    monkeypatch.setattr('sys.argv', ['phase3-gate', item.id])
    main()
    assert 'PASS: Phase 3 gate' in capsys.readouterr().out
    children = [entry for entry in configured_container(store).work().list() if entry.parent == item.id]
    assert children == []
    assert configured_container(store).work().get(item.id) == item
