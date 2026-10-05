"""Protect the two library entry points available to presentation packages."""
import ast
import inspect
from pathlib import Path
from types import ModuleType

from dependency_injector import providers
from fleet import api, container
from fleet_cli import cli


def imported_public_names(source):
    return [alias.asname or alias.name
            for node in ast.parse(source).body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and getattr(node, 'module', None) != '__future__'
            for alias in node.names
            if not (alias.asname or alias.name).startswith('_')]


def test_container_does_not_reexport_imported_names():
    assert imported_public_names(Path(container.__file__).read_text()) == []
    for name, value in vars(container).items():
        if name.startswith('_') or name == 'annotations':
            continue
        assert not isinstance(value, ModuleType), name
        assert value.__module__ == container.__name__, name


def test_guard_detects_aliased_and_plain_reexports():
    assert imported_public_names('from fleet.transport import Host') == ['Host']
    assert imported_public_names('from fleet.transport import Host as Worker') == ['Worker']
    assert imported_public_names('import fleet.transport as adapter') == ['adapter']
    assert imported_public_names('from fleet.transport import Host as _Host') == []


def test_api_exports_only_values_types_and_errors():
    public = {name for name in vars(api) if not name.startswith('_') and name != 'annotations'}
    assert public == set(api.__all__)
    for name in api.__all__:
        value = getattr(api, name)
        assert not inspect.isfunction(value), name
        assert not isinstance(value, ModuleType), name
    assert issubclass(api.TimeoutExpired, Exception)
    assert api.Host('worker', None).is_local


def test_presentations_import_only_api_and_container():
    root = Path(__file__).resolve().parents[2] / 'packages'
    for package in ('fleet-cli/src/fleet_cli', 'fleet-web/src/fleet_web'):
        for path in (root / package).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    if name and (name == 'fleet' or name.startswith('fleet.')):
                        assert name in {'fleet.api', 'fleet.container'}, (path, node.lineno, name)


def test_cli_helpers_use_overridden_providers(cli_container):
    cli_container.default_actor.override(providers.Callable(lambda: 'injected-actor'))
    cli_container.validate_paths.override(providers.Callable(lambda command: []))
    cli_container.parse_since.override(providers.Callable(lambda text: 'injected-since'))
    cli_container.merge_detected.override(providers.Callable(lambda output: {'injected': output}))
    cli_container.listing_arguments.override(providers.Callable(lambda **kwargs: ['injected']))
    parser = cli.build_parser(container=cli_container)
    assert parser.parse_args(['history', '--since', 'anything']).since == 'injected-since'
    assert cli.default_actor(container=cli_container) == 'injected-actor'
    assert cli.merge_detected('raw', container=cli_container) == {'injected': 'raw'}
    assert cli.list_arguments(type('Arguments', (), {'since': None, 'all': False})(),
                              container=cli_container) == ['injected']


def api_behavior_imports(source):
    forbidden = ('fleet.infrastructure', 'fleet.services', 'fleet.transport',
                 'sqlite3', 'os', 'pathlib', 'shutil', 'glob', 'tempfile', 'subprocess')
    errors = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'subprocess' and all(alias.name == 'TimeoutExpired' for alias in node.names):
                continue  # Exception identity is shared with transport; no process operation is imported.
            modules = [node.module or '']
        else:
            continue
        errors.extend(module for module in modules
                      if any(module == prefix or module.startswith(prefix + '.') for prefix in forbidden))
    return errors


def test_api_cannot_import_process_store_or_filesystem_operations():
    assert api_behavior_imports(Path(api.__file__).read_text()) == []
    for source in ('from fleet.infrastructure.sqlite import Store as _Store',
                   'from fleet.services.configuration import validate_paths as _validate',
                   'from subprocess import run as _run', 'import pathlib as _paths'):
        assert api_behavior_imports(source)
    assert api_behavior_imports('from subprocess import TimeoutExpired') == []
