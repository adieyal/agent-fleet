import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

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


@pytest.fixture
def memory_path(tmp_path, monkeypatch, empty_store):
    """A temp dir in memory (/dev/shm) holding the store: the test is about ordering, not durability, and 48 synced
    commits and store writes take longer than its limit on a slow disk. Where there is no /dev/shm, tmp_path."""
    if not Path('/dev/shm').is_dir():
        yield tmp_path
        return
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='fleet-test-') as name:
        path = Path(name)
        shutil.copyfile(empty_store, path / 'fleet.db')
        monkeypatch.setenv('FLEET_STORE', str(path / 'fleet.db'))
        yield path


def test_concurrent_records_are_serialized(memory_path):
    tmp_path = memory_path
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
    reopened = open_records(open_store(store.path))
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
        unit.record_change('work:summary:' + item.id, '', 'earlier narrative', 'author')
        unit.record_change('work:summary:' + item.id, 'earlier narrative', json.dumps(legacy), 'author')
    with closing(connect(store.path)) as db:
        db.text_factory = bytes
        before = [tuple(row) for row in db.execute('SELECT * FROM state_history ORDER BY sequence')]
    _, records, repo = setup_records(tmp_path)
    assert work.summary(item.id).purpose == 'unique narrative'
    with closing(connect(store.path)) as db:
        db.text_factory = bytes
        after = [tuple(row) for row in db.execute(
            'SELECT * FROM state_history WHERE sequence <= ? ORDER BY sequence', (before[-1][0],))]
    assert after == before
    with closing(connect(store.path)) as db:
        assert db.execute('SELECT count(*) FROM work_summary').fetchone()[0] == 0
        current_dump = '\n'.join(line for line in db.iterdump()
                                 if not line.startswith('INSERT INTO "state_history"'))
        assert 'unique narrative' not in current_dump
    document, = records.intents()
    assert document['path'] == 'summaries/' + item.id + '.json'
    assert document['revision'] == git(repo, 'rev-parse', 'HEAD')
    assert 'unique narrative' in (repo / document['path']).read_text()
    summary = work.set_summary(item.id, purpose='new narrative', done='Done', doing='Doing',
                               next='Next', authoring_role='user', actor='author')
    assert open_work(store).summary(item.id) == summary
    with closing(connect(store.path)) as db:
        assert 'new narrative' not in '\n'.join(db.iterdump())
    assert 'new narrative' in (repo / 'summaries' / (item.id + '.json')).read_text()


def test_mandate_is_validated_and_revision_is_confirmed(tmp_path):
    _, records, repo = setup_records(tmp_path)
    value = dict(goal='Ship', constraints=['No deploy'], decision_authority=['dispatch'],
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
    with pytest.raises(ValueError, match='path'):
        records.write('p', '../escape', 'body', key='escape', actor='author')


@pytest.mark.parametrize('key,actor', [(' ', 'author'), ('key', ' ')])
def test_write_validates_authorship_through_prepare(tmp_path, monkeypatch, key, actor):
    from unittest.mock import Mock
    store, records, repo = setup_records(tmp_path)
    prepare = Mock(wraps=records.authoring.prepare)
    monkeypatch.setattr(records.authoring, 'prepare', prepare)
    sequence = store.latest_sequence()
    with pytest.raises(ValueError, match='key and actor are required'):
        records.write('p', 'a.md', 'body', key=key, actor=actor)
    prepare.assert_called_once()
    assert records.intents() == []
    assert store.latest_sequence() == sequence


def test_recovery_cannot_replace_a_newer_document_revision(tmp_path, monkeypatch):
    store, records, repo = setup_records(tmp_path)
    original = records.writer.commit
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('crash after commit')
    monkeypatch.setattr(records.writer, 'commit', crash)
    with pytest.raises(SystemExit):
        records.write('p', 'same.md', 'old', key='old', actor='author')
    reopened = open_records(open_store(store.path))
    result = reopened.write('p', 'same.md', 'new', key='new', actor='author')
    reopened.reconcile()
    assert reopened.read('p', 'same.md') == 'new'
    assert result['revision'] == git(repo, 'rev-parse', 'HEAD')


def test_management_registration_cli_and_summary_command(tmp_path, capsys, project_id):
    from fleet import cli
    repo = tmp_path / 'management'
    repo.mkdir()
    git(repo, 'init')
    cli.main(['project', 'management', project_id, str(repo)])
    item = open_work().add(project=project_id, title='Task', goal='Goal', actor='author')
    cli.main(['summary', 'set', item.id, '--purpose', 'Purpose', '--done', 'Done',
              '--doing', 'Doing', '--next', 'Next', '--authoring-role', 'user', '--actor', 'author'])
    capsys.readouterr()
    cli.main(['status', 'p', '--json'])
    assert json.loads(capsys.readouterr().out)['work_items'][0]['summary']['purpose'] == 'Purpose'


def test_a_projects_first_record_creates_its_management_repository(tmp_path):
    store, records, repo = setup_records(tmp_path)
    assert not records.registered('fresh')
    result = records.write('fresh', 'a.md', 'body', key='first', actor='author')
    assert result['state'] == 'confirmed'
    created = Path(os.environ['FLEET_MANAGEMENT']) / 'fresh'
    assert records.registered('fresh') and records.read('fresh', 'a.md') == 'body'
    assert subprocess.run(['git', '-C', str(created), 'log', '--format=%s'], capture_output=True, text=True,
                          check=True).stdout.strip() != ''
