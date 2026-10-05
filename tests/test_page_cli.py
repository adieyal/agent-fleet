"""Page commands use temporary controller records, never the user's Fleet state."""
import io
import json
from uuid import uuid4

import pytest

from fleet.api import FleetError
from fleet.container import configured_container
from fleet_cli.cli import main


@pytest.fixture
def pages(monkeypatch):
    monkeypatch.delenv('FLEET_JOB_ID', raising=False)
    container = configured_container()
    project = container.initialized_workspace().edit_registry(lambda registry: registry.create('pages-cli')).id
    return container, project


def invoke(container, *arguments):
    main(['page', *arguments], container=container)


def test_write_stdin_list_show_and_history(pages, monkeypatch, capsys, tmp_path):
    container, project = pages
    body = '# First title\n\nOriginal prose\n'
    monkeypatch.setattr('sys.stdin', io.StringIO(body))
    invoke(container, 'write', project, 'plan', '--actor', 'codex', '--key', 'first')
    output = capsys.readouterr().out
    first = container.page_view(project, 'plan')['revision']
    assert f'/pages/{project}/plan revision {first}' in output
    monkeypatch.setattr('sys.stdin', io.StringIO(body))
    invoke(container, 'write', project, 'plan', '--actor', 'codex', '--key', 'first')
    assert first in capsys.readouterr().out
    assert len(container.records().intents()) == 1
    source = tmp_path / 'page.md'
    source.write_text('# Second title\n\nUpdated prose\n')
    invoke(container, 'write', project, 'plan', '--file', str(source), '--actor', 'editor')
    capsys.readouterr()
    invoke(container, 'ls', project)
    listed = capsys.readouterr().out.strip().split('\t')
    assert listed[:2] == ['plan', 'Second title']
    assert listed[2] == container.page_view(project, 'plan')['revision']
    assert listed[3] == 'editor' and listed[4]
    invoke(container, 'show', project, 'plan', '--version', '1')
    assert capsys.readouterr().out == body
    invoke(container, 'show', project, 'plan')
    assert capsys.readouterr().out == source.read_text()
    invoke(container, 'show', project, 'plan', '--version', '1', '--json')
    view = json.loads(capsys.readouterr().out)
    assert view['historical'] and view['revision'] == first
    assert view['nodes'][0]['kind'] == 'prose'


def test_resolved_json(pages, capsys, tmp_path):
    container, project = pages
    work = container.work().add(project=project, kind='task', title='Review page', goal='Verify', actor='fixture')
    source = tmp_path / 'page.md'
    source.write_text(f'# Review\n\n::work{{id={work.id} block=review-work}}\n')
    invoke(container, 'write', project, 'review', '--file', str(source), '--actor', 'codex')
    capsys.readouterr()
    invoke(container, 'show', project, 'review', '--json')
    node = json.loads(capsys.readouterr().out)['nodes'][1]
    assert node['record']['id'] == work.id and node['block'] == 'review-work'


@pytest.mark.parametrize('body,message', [
    ('# Plan\n\n::unknown{id=x}\n', 'Line 3: Unknown directive'),
    ('# Plan\n\n::work{id=bad}\n', 'Line 3: Invalid work ID'),
    ('::attention{id=bad}', 'Line 1: Invalid attention ID'),
    ('::runs{project=bad since=7d}', 'Line 1: Invalid project ID'),
    ('::runs{project=p-1234abcd since=0d}', 'Line 1: Invalid since'),
    ('::work{broken}', 'Line 1: Invalid attribute'),
    (f'::work{{id={uuid4()} block=bad_id}}', 'Line 1: Invalid block ID'),
    (f'::work{{id={uuid4()} block=same}}\n\n::work{{id={uuid4()} block=same}}', 'Line 3: Duplicate block ID'),
])
def test_invalid_directives_do_not_write(pages, monkeypatch, capsys, body, message):
    container, project = pages
    monkeypatch.setattr('sys.stdin', io.StringIO(body))
    with pytest.raises(SystemExit) as error:
        invoke(container, 'write', project, 'plan', '--actor', 'codex')
    assert error.value.code == 2 and message in capsys.readouterr().err
    assert container.records().intents() == []
    assert not container.records().registered(project)


def test_invalid_slug_and_missing_pages(pages, monkeypatch, capsys):
    container, project = pages
    monkeypatch.setattr('sys.stdin', io.StringIO('# Plan'))
    with pytest.raises(SystemExit):
        invoke(container, 'write', project, '../plan', '--actor', 'codex')
    assert 'Invalid page slug' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        invoke(container, 'show', project, 'missing')
    assert 'Page not found' in capsys.readouterr().err
    invoke(container, 'ls', project)
    assert 'No confirmed pages' in capsys.readouterr().out


def test_worker_job_refuses_without_records(pages, monkeypatch, capsys):
    container, project = pages
    monkeypatch.setenv('FLEET_JOB_ID', str(uuid4()))
    monkeypatch.setattr('sys.stdin', io.StringIO('# Worker page'))
    with pytest.raises(SystemExit):
        invoke(container, 'write', project, 'plan', '--actor', 'codex')
    assert 'outbox and report it' in capsys.readouterr().err
    assert container.records().intents() == []


def test_controller_job_records_source_run(pages, monkeypatch):
    container, project = pages
    container.execution().record_observed('carbon', dict(id='page-job', status='running', runtime='codex'), project=project)
    run = next(run for run in container.execution().runs() if run.remote_job_id == 'page-job')
    monkeypatch.setenv('FLEET_JOB_ID', 'page-job')
    container.page_command('write', project, 'plan', body='# Plan', actor='codex', key='job-write')
    assert container.records().document(project, 'pages/plan.md')['source_run'] == run.id


def test_key_conflict_and_unknown_version(pages):
    container, project = pages
    container.page_command('write', project, 'plan', body='# Plan', actor='codex', key='same')
    with pytest.raises(FleetError, match='payload changed'):
        container.page_command('write', project, 'plan', body='# Changed', actor='codex', key='same')
    with pytest.raises(FleetError, match='no version 0'):
        container.page_command('show', project, 'plan', version=0)


def test_unchanged_body_is_a_new_version_with_its_author(pages):
    container, project = pages
    first = container.page_command('write', project, 'plan', body='# Plan', actor='first')
    second = container.page_command('write', project, 'plan', body='# Plan', actor='second')
    assert first['revision'] != second['revision']
    listed = container.page_command('ls', project)['pages'][0]
    assert listed['author'] == 'second' and listed['revision'] == second['revision']
    assert container.page_command('show', project, 'plan', version=1)['revision'] == first['revision']
    assert container.page_command('show', project, 'plan', version=2)['revision'] == second['revision']
