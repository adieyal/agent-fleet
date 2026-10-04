import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from dependency_injector import providers

from fleet import cli
from fleet.container import Container


def test_main_uses_injected_query_and_one_store(cli_container, capsys):
    original = cli_container.store
    factory = providers.Factory(original.provides, *original.args, **original.kwargs)
    stores = []

    def create_store():
        store = factory()
        stores.append(store)
        return store

    cli_container.store.override(providers.ThreadSafeSingleton(create_store))
    cli_container.initialized_workspace.override(SimpleNamespace(resolve_project=lambda value: 'resolved'))
    cli_container.initialized_attention.override(object())
    seen = []

    def query(*, project):
        seen.append((project, cli_container.services().store))
        return {'project': project, 'work_items': [], 'attention': []}

    cli_container.project_status.override(providers.Factory(query))
    cli.main(['status', 'prefix', '--json'], container=cli_container)
    assert json.loads(capsys.readouterr().out)['project'] == 'resolved'
    assert len(stores) == 1
    assert seen == [('resolved', stores[0])]


def test_help_does_not_resolve_the_store(cli_container):
    cli_container.store.override(providers.Callable(lambda: pytest.fail('store opened for help')))
    with pytest.raises(SystemExit) as exited:
        cli.main(['--help'], container=cli_container)
    assert exited.value.code == 0


def test_invocations_with_distinct_containers_keep_work_isolated(tmp_path, cli_container, capsys):
    first = cli_container
    second = Container()
    second.settings.override(dict(second.settings(), store_path=tmp_path / 'second.db'))
    first.initialized_workspace().edit_registry(lambda registry: registry.create('first'))
    second.initialized_workspace().edit_registry(lambda registry: registry.create('second'))
    for container, name in ((first, 'first'), (second, 'second')):
        cli.main(['work', 'add', 'Task', '--project', name, '--goal', 'Test', '--actor', 'test'], container=container)
        assert json.loads(capsys.readouterr().out)['project'] == container.workspace().resolve_project(name)
    assert first.work().list()[0].project != second.work().list()[0].project
    assert first.store() is not second.store()


def test_cli_has_one_container_bootstrap_and_no_legacy_factory_calls():
    tree = ast.parse(Path(cli.__file__).read_text())
    constructors = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'Container']
    assert len(constructors) == 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert not node.func.id.startswith('open_')
            assert node.func.id != 'facades'
