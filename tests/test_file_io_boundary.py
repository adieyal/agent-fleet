"""Records and context/storage services choose workflows; adapters perform I/O."""
import ast
from pathlib import Path

import fleet


def test_a03_workflows_have_no_direct_filesystem_operations():
    root = Path(fleet.__file__).parent
    names = ('modules/records/facade.py', 'services/context.py', 'services/storage.py')
    operations = {'mkdir', 'write_text', 'read_text', 'rglob', 'stat', 'is_file', 'is_symlink',
                  'resolve', 'expanduser', 'cwd', 'open'}
    violations = []
    for name in names:
        for node in ast.walk(ast.parse((root / name).read_text())):
            if isinstance(node, ast.Import):
                for imported in node.names:
                    if imported.name in {'os', 'tempfile', 'subprocess', 'shutil'}:
                        violations.append(f'{name}:{node.lineno}: import {imported.name}')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in operations:
                violations.append(f'{name}:{node.lineno}: {ast.unparse(node.func)}')
    assert not violations, '\n'.join(violations)
