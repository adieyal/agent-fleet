import json
import subprocess
import sys
from contextlib import closing

import pytest

from fleet.composition import open_records, open_store, open_work
from fleet.infrastructure.sqlite.store import connect


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                          text=True, timeout=10).stdout.strip()


def setup_records(tmp_path):
    repo = tmp_path / 'records'
    repo.mkdir()
    git(repo, 'init')
    store = open_store()
    records = open_records(store)
    records.register('p', repo, actor='author')
    return store, records, repo


def test_concurrent_records_are_serialized(tmp_path):
    store, records, repo = setup_records(tmp_path)
    signal = tmp_path / 'start'
    script = '''
import sys, time
from pathlib import Path
from fleet.composition import open_records
while not Path(sys.argv[1]).exists(): time.sleep(.001)
r = open_records()
for i in range(24):
    key = sys.argv[2] + str(i)
    result = r.write('p', 'shared.txt', key, key=key, actor='author', source_run='run')
    assert result['state'] == 'confirmed'
'''
    before = store.latest_sequence()
    children = [subprocess.Popen([sys.executable, '-c', script, str(signal), str(i)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(2)]
    signal.touch()
    results = [child.communicate(timeout=10) for child in children]
    for child, (output, error) in zip(children, results):
        assert child.returncode == 0, output + error
    assert git(repo, 'rev-list', '--count', 'HEAD') == '48'
    assert len(store.history_after(before)) == 96
    assert len(records.intents()) == 48
    assert all(row['revision'] for row in records.intents())


@pytest.mark.parametrize('committed', [False, True])
def test_crash_reconciles_without_claiming_unconfirmed_revision(tmp_path, monkeypatch, committed):
    store, records, repo = setup_records(tmp_path)
    original = records.writer.commit
    def crash(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise SystemExit('crash')
    monkeypatch.setattr(records.writer, 'commit', crash)
    with pytest.raises(SystemExit):
        records.write('p', 'a.md', 'body', key='once', actor='author', source_run='run')
    intent, = records.intents()
    assert intent['revision'] is None
    reopened = open_records(store)
    reopened.reconcile()
    result, = reopened.intents()
    assert result['state'] == ('confirmed' if committed else 'failed')
    assert result['revision'] == (git(repo, 'rev-parse', 'HEAD') if committed else None)
    before = store.latest_sequence()
    reopened.reconcile()
    assert store.latest_sequence() == before


def test_summary_cutover_and_new_writes_have_no_stored_body(tmp_path):
    store = open_store()
    work = open_work(store)
    item = work.add(project='p', title='Task', goal='Goal', actor='author')
    legacy = dict(id=item.id, purpose='unique narrative', done='Done', doing='Doing', next='Next',
                  authoring_role='user', updated=store.clock().isoformat())
    with store.unit_of_work() as unit:
        unit.connection.execute('INSERT INTO work_summary VALUES (?, ?)', (item.id, json.dumps(legacy)))
        unit.record_change('work:summary:' + item.id, '', json.dumps(legacy), 'author')
    _, records, repo = setup_records(tmp_path)
    assert work.summary(item.id).purpose == 'unique narrative'
    with closing(connect(store.path)) as db:
        assert db.execute('SELECT count(*) FROM work_summary').fetchone()[0] == 0
        assert 'unique narrative' not in '\n'.join(db.iterdump())
    summary = work.set_summary(item.id, purpose='new narrative', done='Done', doing='Doing',
                               next='Next', authoring_role='user', actor='author')
    assert open_work(store).summary(item.id) == summary
    with closing(connect(store.path)) as db:
        assert 'new narrative' not in '\n'.join(db.iterdump())
    assert 'new narrative' in (repo / 'summaries' / (item.id + '.json')).read_text()


def test_mandate_is_validated_and_revision_is_confirmed(tmp_path):
    _, records, repo = setup_records(tmp_path)
    value = dict(goal='Ship', constraints=['No deploy'], decision_authority=['Plan'],
                 escalation_conditions=['Risk'], criteria_it_may_judge=['c1'])
    result = records.write_mandate('p', 'mandate.json', json.dumps(value), key='m', actor='author')
    mandate = records.mandate('p', 'mandate.json')
    assert mandate.goal == 'Ship'
    assert mandate.criteria_it_may_judge == ['c1']
    assert result['revision'] == git(repo, 'rev-parse', 'HEAD')
    with pytest.raises(ValueError, match='mandate'):
        records.write_mandate('p', 'bad.json', '{"goal": "x"}', key='bad', actor='author')
    before = len(records.intents())
    assert records.write_mandate('p', 'mandate.json', json.dumps(value), key='m', actor='author') == result
    assert len(records.intents()) == before
    with pytest.raises(ValueError, match='key'):
        records.write('p', 'mandate.json', 'changed', key='m', actor='author')


def test_failed_write_and_missing_registration_are_explicit(tmp_path):
    store, records, repo = setup_records(tmp_path)
    (repo / 'uncommitted').write_text('user draft')
    result = records.write('p', 'a.md', 'body', key='failed', actor='author')
    assert result['state'] == 'failed'
    assert result['revision'] is None
    assert 'uncommitted' in result['error']
    assert records.read('p', 'a.md') is None
    with pytest.raises(ValueError, match='not registered'):
        records.write('missing', 'a.md', 'body', key='x', actor='author')
    with pytest.raises(ValueError, match='path'):
        records.write('p', '../escape', 'body', key='escape', actor='author')


def test_recovery_cannot_replace_a_newer_document_revision(tmp_path, monkeypatch):
    store, records, repo = setup_records(tmp_path)
    original = records.writer.commit
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('crash after commit')
    monkeypatch.setattr(records.writer, 'commit', crash)
    with pytest.raises(SystemExit):
        records.write('p', 'same.md', 'old', key='old', actor='author')
    reopened = open_records(store)
    result = reopened.write('p', 'same.md', 'new', key='new', actor='author')
    reopened.reconcile()
    assert reopened.read('p', 'same.md') == 'new'
    assert result['revision'] == git(repo, 'rev-parse', 'HEAD')


def test_management_registration_cli_and_summary_command(tmp_path, capsys):
    from fleet import cli
    repo = tmp_path / 'management'
    repo.mkdir()
    git(repo, 'init')
    cli.main(['project', 'management', 'p', str(repo)])
    item = open_work().add(project='p', title='Task', goal='Goal', actor='author')
    cli.main(['summary', 'set', item.id, '--purpose', 'Purpose', '--done', 'Done',
              '--doing', 'Doing', '--next', 'Next', '--authoring-role', 'user', '--actor', 'author'])
    capsys.readouterr()
    cli.main(['status', 'p', '--json'])
    assert json.loads(capsys.readouterr().out)['work_items'][0]['summary']['purpose'] == 'Purpose'
