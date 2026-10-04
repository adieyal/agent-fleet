from types import SimpleNamespace

import pytest

from fleet import cli, composition
from fleet.errors import FleetError
from fleet.modules.workspace.domain.projects import Project, Registry


def test_resolver_rejects_ambiguous_prefix_and_name():
    registry = Registry([Project('p-12345678', 'Demo'), Project('p-12345679', 'Demo')])
    assert registry.resolve('p-12345678') == 'p-12345678'
    for reference in ['p-123', 'Demo']:
        with pytest.raises(FleetError, match='ambiguous'):
            registry.resolve(reference)
    with pytest.raises(FleetError, match='fleet project add NAME --link HOST:LABEL'):
        registry.resolve('unregistered-label')


@pytest.mark.parametrize('case', ['missing', 'multiple', 'raw', 'wrong-work'])
def test_send_refuses_before_transport_or_run(monkeypatch, capsys, case):
    workspace = composition.open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('Demo'))
    reference = project.id
    if case in ('multiple', 'raw', 'wrong-work'):
        workspace.edit_registry(lambda registry: registry.link(project.id, 'fake', 'worker-demo'))
    if case == 'multiple':
        workspace.edit_registry(lambda registry: registry.link(project.id, 'fake', 'second'))
    if case == 'raw':
        reference = 'worker-demo'
    arguments = ['send', '-p', reference, '-d', 'Ship', '--step', 'Ship', '--host', 'fake', '--cwd', '/repo']
    if case == 'wrong-work':
        other = workspace.edit_registry(lambda registry: registry.create('Other'))
        item = composition.open_work().add(project=other.id, title='Task', goal='Ship', actor='user')
        arguments += ['--work-item', item.id]
    monkeypatch.setattr(cli.transport, 'host_by_name', lambda name: SimpleNamespace(name=name))
    def unexpected(*args, **kwargs):
        pytest.fail('must refuse before transport')
    monkeypatch.setattr(cli.transport, 'call', unexpected)
    with pytest.raises(SystemExit):
        cli.main(arguments)
    error = capsys.readouterr().err.replace('\n', '')
    if case == 'missing':
        assert f'fleet project link {project.id} fake:Demo' in error
    elif case == 'raw':
        assert 'fleet project ls' in error and 'fleet project add' in error
    elif case == 'multiple':
        assert 'multiple labels' in error and 'second' in error and 'worker-demo' in error
    else:
        assert 'work item belongs to another project' in error
    assert composition.open_execution().runs() == []


def test_listing_matches_each_hosts_link(monkeypatch, *, cli_container):
    workspace = composition.open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('Demo'))
    for host, label in [('a', 'alpha'), ('b', 'beta')]:
        workspace.edit_registry(lambda registry: registry.link(project.id, host, label))
    hosts = [SimpleNamespace(name=name) for name in ['a', 'b']]
    reports = [SimpleNamespace(host=host, jobs=[{'project': label} for label in ['alpha', 'beta', 'Demo']])
               for host in hosts]
    monkeypatch.setattr(cli.transport, 'gather', lambda *args: reports)
    monkeypatch.setattr(cli.transport, 'gather_sessions', lambda *args: {
        'a': [{'project': 'alpha'}, {'project': 'beta'}], 'b': [{'project': 'beta'}]})
    arguments = cli.build_parser().parse_args(['ls', '-p', project.id[:6]])
    result, sessions = cli.gather_listing(hosts, arguments, container=cli_container)
    assert [report.jobs for report in result] == [[{'project': 'alpha'}], [{'project': 'beta'}]]
    assert sessions == {'a': [{'project': 'alpha'}], 'b': [{'project': 'beta'}]}


def test_move_validates_all_hosts_before_writes(monkeypatch, override_cli_method, cli_container):
    workspace = composition.open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('Demo'))
    workspace.edit_registry(lambda registry: registry.link(project.id, 'a', 'alpha'))
    override_cli_method('references', 'job_or_session', lambda reference: (SimpleNamespace(name=reference), 'job'))
    calls = []
    monkeypatch.setattr(cli.transport, 'call', lambda *args: calls.append(args))
    with pytest.raises(SystemExit):
        cli.main(['mv', 'a', 'b', 'Demo'], container=cli_container)
    assert calls == []
    cli.main(['mv', 'a', project.id[:6]], container=cli_container)
    assert calls[0][1] == ['mv', 'job', 'alpha']


def test_project_maintenance_accepts_name_and_prefix(capsys):
    workspace = composition.open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('Demo'))
    cli.main(['project', 'rename', project.id[:6], 'Renamed'])
    cli.main(['project', 'repo', 'add', 'Renamed', 'https://example.org/repo'])
    assert workspace.registry().get(project.id).repositories == ['https://example.org/repo']


def test_library_removes_existing_registered_name_key(tmp_path):
    workspace = composition.open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('Demo'))
    config = cli.transport.load_config()
    config['libraries'] = {'Demo': str(tmp_path)}
    cli.transport.save_config(config)
    cli.main(['library', 'rm', project.id[:6]])
    assert cli.transport.load_config()['libraries'] == {}


def test_exact_unique_name_precedes_id_prefix():
    registry = Registry([Project('p-12345678', 'p'), Project('p-abcdef12', 'Other')])
    assert registry.resolve('p') == 'p-12345678'
    assert registry.resolve('p-ab') == 'p-abcdef12'


@pytest.mark.parametrize('kind', ['name', 'prefix'])
@pytest.mark.parametrize('command', ['link', 'repo-rm', 'management', 'restore', 'merge', 'library-add'])
def test_remaining_project_commands_resolve_references(tmp_path, capsys, command, kind):
    import subprocess

    workspace = composition.open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('Demo'))
    reference = project.name if kind == 'name' else project.id[:6]
    if command == 'link':
        cli.main(['host', 'add', 'fake', '--local'])
        cli.main(['project', 'link', reference, 'fake:worker-demo'])
        assert workspace.registry().project_for('fake', 'worker-demo').id == project.id
    elif command == 'repo-rm':
        workspace.edit_registry(lambda registry: registry.add_repository(project.id, 'https://example.org/repo'))
        cli.main(['project', 'repo', 'rm', reference, 'https://example.org/repo'])
        assert workspace.registry().get(project.id).repositories == []
    elif command == 'management':
        subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
        cli.main(['project', 'management', reference, str(tmp_path)])
        assert workspace.management_repository(project.id) == str(tmp_path)
    elif command == 'restore':
        workspace.move_in(['fake'], 'worker-demo', name='Other')
        workspace.link_in(project.id, ['fake'], 'demo')
        workspace.shutter(project.id)
        cli.main(['project', 'restore', reference, '--shutter', 'Other'])
        assert project.id in workspace.floors_snapshot()
        assert workspace.registry().resolve('Other') in workspace.shuttered_snapshot()
    elif command == 'merge':
        other = workspace.edit_registry(lambda registry: registry.create('Other'))
        cli.main(['project', 'merge', reference, 'Other' if kind == 'name' else other.id[:6]])
        assert other.id not in workspace.registry().projects
    else:
        cli.main(['library', 'add', reference, str(tmp_path)])
        assert cli.transport.load_config()['libraries'][project.id] == str(tmp_path)


@pytest.mark.parametrize('command', [
    ['ls'], ['watch'], ['send'], ['mv'], ['library', 'add'], ['library', 'rm'],
    ['library', 'link'], ['work', 'add'], ['status'], ['triage', 'status'],
    ['decision', 'list'], ['attention', 'add'], ['attention', 'list'], ['history', 'runs'],
    ['project', 'rename'], ['project', 'link'], ['project', 'merge'], ['project', 'restore'],
    ['project', 'repo', 'add'], ['project', 'repo', 'rm'], ['project', 'management'],
    ['guidance', 'show'], ['guidance', 'edit'], ['guidance', 'history'],
])
def test_project_help_is_consistent(capsys, command):
    with pytest.raises(SystemExit) as stopped:
        cli.main([*command, '--help'])
    assert stopped.value.code == 0
    assert 'project (ID, prefix or name)' in ' '.join(capsys.readouterr().out.split())
