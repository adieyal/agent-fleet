"""The HTTP package cannot regain persistence, process, or scanning responsibilities."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'packages/fleet-web/src/fleet_web'
FORBIDDEN_MODULES = {'subprocess', 'sqlite3', 'paramiko', 'fabric', 'fleet.transport',
                     'fleet.infrastructure', 'fleet.services', 'fleet.composition'}
FORBIDDEN_ATTRIBUTES = {'store', 'rglob', 'glob', 'iglob', 'iterdir', 'walk', 'scandir',
                        'listdir', 'Popen', 'system', 'popen', 'ssh', 'repository_remotes'}


def violations(source):
    errors = []
    for node in ast.walk(ast.parse(source)):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or '']
            for alias in node.names:
                if alias.name in FORBIDDEN_ATTRIBUTES or alias.name == 'Store' or alias.name.endswith('Repository'):
                    errors.append((node.lineno, alias.name))
        for module in modules:
            if any(module == banned or module.startswith(banned + '.') for banned in FORBIDDEN_MODULES):
                errors.append((node.lineno, module))
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            errors.append((node.lineno, node.attr))
        if isinstance(node, ast.Name) and node.id in {'Store', 'Repository'}:
            errors.append((node.lineno, node.id))
    return errors


def test_web_has_no_process_persistence_or_scanning_code():
    errors = {str(path.relative_to(ROOT)): violations(path.read_text()) for path in ROOT.rglob('*.py')}
    assert not {path: found for path, found in errors.items() if found}


@pytest.mark.parametrize('source', [
    'import subprocess as process', 'from sqlite3 import connect',
    'from os import walk as scan', 'from fleet.container import Store as Database',
    'from fleet.infrastructure.sqlite import Store', 'container.store()',
    'Path(root).rglob("*.md")', 'for child in root.iterdir(): pass',
    'os.scandir(root)', 'os.system("ssh home")',
])
def test_boundary_guard_rejects_regressions(source):
    assert violations(source)


def test_static_byte_reads_are_presentation():
    assert violations('body = resource.read_bytes()') == []
