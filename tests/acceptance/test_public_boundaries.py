"""Guard public dependencies in presentation acceptance suites, not adapter/unit tests."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# These suites exercise terminal reads or HTTP behavior through public composition.
# Adapter/worker integration suites remain free to inspect their implementations.
ACCEPTANCE = (
    'conftest.py', 'workspace_support.py', 'test_cli_reads.py', 'test_web_building.py',
    'test_web_bench.py', 'test_web_project_identity.py', 'test_web_fixture.py',
)


def forbidden_imports(source):
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            names = [module]
            if module == 'fleet':
                names = [f'fleet.{alias.name}' for alias in node.names]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.func, ast.Name) and node.func.id == '__import__' or (
                isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'):
                names = [node.args[0].value]
        for name in names:
            if not isinstance(name, str) or not (name == 'fleet' or name.startswith('fleet.')):
                continue
            public = name in {'fleet.api', 'fleet.container'} or (
                name.startswith('fleet.modules.') and len(name.split('.')) == 3)
            if not public:
                violations.append((node.lineno, name))
    return violations


@pytest.mark.parametrize('filename', ACCEPTANCE)
def test_acceptance_imports_use_public_surfaces(filename):
    source = ROOT / 'tests' / filename
    assert forbidden_imports(source.read_text()) == [], str(source)


@pytest.mark.parametrize('statement', (
    'from fleet.modules.work.domain import EvidenceSpecification',
    'from fleet.infrastructure.config.workspace import decode_workspace',
    'from fleet.services.fixtures import FixtureState',
    'from fleet import transport',
    'import fleet.services.live',
    '__import__("fleet.services.live")',
    'importlib.import_module("fleet.infrastructure.sqlite")',
))
def test_guard_rejects_private_dependencies(statement):
    assert forbidden_imports(statement)


def test_guard_allows_public_types_and_provider_overrides():
    assert forbidden_imports('''
from fleet.container import Container
from fleet.api import Host
from fleet.modules.work import EvidenceSpecification
container = Container()
container.live_state.override(fake_state)
''') == []
