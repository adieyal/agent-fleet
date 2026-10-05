from pathlib import Path
from datetime import datetime, timezone, timedelta

from fleet.modules.execution import JobObservation

import pytest

from fleet.container import configured_container
from tests.pages_fixture import seed_page


@pytest.fixture
def demo():
    container = configured_container()
    return container, seed_page(container)


def test_demo_resolves_store_records_and_preserves_unknown_ids(demo):
    container, ids = demo
    view = container.page_view(project=ids['project'], slug='supplier-migration')
    directives = [node for node in view['nodes'] if node['kind'] != 'prose']
    work, attention, runs, error = directives
    assert work['record']['goal'].startswith('Move current suppliers')
    assert work['record']['progress'] == dict(basis='milestones', complete=0, total=1)
    assert attention['record']['owner'] == 'user'
    assert attention['record']['state'] == 'open'
    assert len(runs['records']) == 1
    assert runs['records'][0]['host'] == 'home'
    assert runs['records'][0]['host_reachable'] is True
    assert error['error'] == 'Unknown work item missing-work'
    assert view['revision'] == ids['revision']
    assert container.page_view(project=ids['project'])['pages'][0]['title'] == 'Supplier migration'


def test_confirmed_revision_ignores_dirty_working_tree_and_supports_history(demo):
    container, ids = demo
    path = Path(container.workspace().management_repository(ids['project'])) / 'pages/supplier-migration.md'
    original = path.read_text()
    path.write_text('# Uncommitted replacement')
    assert container.page_view(project=ids['project'], slug='supplier-migration')['title'] == 'Supplier migration'
    path.write_text(original)
    container.records().write(ids['project'], 'pages/supplier-migration.md', '# Revised\n', key='revision-2', actor='fixture')
    older = container.page_view(project=ids['project'], slug='supplier-migration', revision=ids['revision'])
    assert older['historical'] and older['title'] == 'Supplier migration'
    with pytest.raises(LookupError, match='Unknown confirmed page revision'):
        container.page_view(project=ids['project'], slug='supplier-migration', revision='HEAD')


def test_missing_attention_wrong_project_and_empty_runs(demo):
    container, ids = demo
    other = container.initialized_workspace().edit_registry(lambda registry: registry.create('other')).id
    foreign = container.work().add(project=other, goal='Elsewhere', title='Foreign', actor='fixture')
    body = f'::attention{{id=absent}}\n\n::work{{id={foreign.id}}}\n\n::runs{{project={other} since=7d}}\n\n::runs{{project={ids["project"]} since=1d}}'
    container.records().write(ids['project'], 'pages/errors.md', body, key='errors', actor='fixture')
    old = datetime(1970, 1, 1, tzinfo=timezone.utc)
    container.execution().observe('home', JobObservation('supplier-check', 'done', None, old, old, old))
    nodes = container.page_view(project=ids['project'], slug='errors')['nodes']
    assert nodes[0]['error'] == 'Unknown attention item absent'
    assert 'another project' in nodes[2]['error']
    assert 'another project' in nodes[4]['error']
    assert nodes[6]['records'] == []
    assert 'No runs' in nodes[6]['empty_reason']


def test_missing_page_and_empty_index(demo):
    container, ids = demo
    with pytest.raises(LookupError, match='Page not found'):
        container.page_view(project=ids['project'], slug='absent')
    other = container.initialized_workspace().edit_registry(lambda registry: registry.create('empty')).id
    assert container.page_view(project=other)['empty_reason'] == 'No confirmed pages in this project'


def test_unknown_progress_and_unavailable_hosts_are_explicit(demo):
    container, ids = demo
    task = container.work().add(project=ids['project'], goal='Investigate', title='New task', actor='fixture')
    container.records().write(ids['project'], 'pages/unknown.md',
        f'::work{{id={task.id}}}\n\n::runs{{project={ids["project"]} since=7d}}', key='unknown', actor='fixture')
    container.execution().record_host('home', reachable=False, error='offline')
    nodes = container.page_view(project=ids['project'], slug='unknown')['nodes']
    assert nodes[0]['record']['progress'] == dict(basis='unknown', complete=None, total=None)
    assert nodes[2]['records'][0]['host_reachable'] is False


def test_runs_are_project_scoped_and_sorted_by_start_then_id(demo):
    container, ids = demo
    other = container.initialized_workspace().edit_registry(lambda registry: registry.create('outside')).id
    now = container.store().clock()
    for name, run_id, project in [('older', 'aaa', ids['project']), ('same-a', 'bbb', ids['project']),
                                  ('same-b', 'ccc', ids['project']), ('foreign', 'ddd', other)]:
        container.execution().record_observed('home', dict(id=name, run_id=run_id), project=project)
        container.execution().observe('home', JobObservation(name, 'done', None,
            now - timedelta(hours=1) if name == 'older' else now, now, now))
    runs = next(node for node in container.page_view(project=ids['project'], slug='supplier-migration')['nodes']
                if node['kind'] == 'runs')['records']
    assert [run['id'] for run in runs if run['id'] in ('aaa', 'bbb', 'ccc')] == ['bbb', 'ccc', 'aaa']
    assert not any(run['id'] == 'ddd' for run in runs)


def test_read_snapshot_creates_no_state_history(demo):
    container, ids = demo
    before = container.store().latest_sequence()
    container.page_view(project=ids['project'], slug='supplier-migration')
    container.page_view(project=ids['project'])
    assert container.store().latest_sequence() == before


def test_runs_with_missing_start_are_named_not_silently_hidden(demo):
    container, ids = demo
    container.execution().record_observed('home', dict(id='no-start'), project=ids['project'])
    runs = next(node for node in container.page_view(project=ids['project'], slug='supplier-migration')['nodes']
                if node['kind'] == 'runs')
    assert runs['excluded_reason'] == '1 runs excluded: start time not recorded'
