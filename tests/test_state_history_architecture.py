"""History mutations are confined to retention pruning."""

import ast
from pathlib import Path
import re


def test_state_history_is_append_only_except_retention():
    root = Path(__file__).resolve().parents[1]
    mutation = re.compile(
        r'\b(?:UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+["`\[]?state_history\b',
        re.IGNORECASE,
    )
    allowed = []
    violations = []
    for path in (root / 'fleet').rglob('*.py'):
        tree = ast.parse(path.read_text())
        retention_nodes = set()
        if path.relative_to(root).as_posix() == 'fleet/infrastructure/sqlite/store.py':
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == 'record_change':
                    retention_nodes.update(ast.walk(node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not mutation.search(node.value):
                continue
            location = f'{path.relative_to(root)}:{node.lineno}'
            if node in retention_nodes and node.value == 'DELETE FROM state_history WHERE time < ?':
                allowed.append(location)
            else:
                violations.append(location)
    assert not violations, f'state_history mutations outside retention: {violations}'
    assert len(allowed) == 1
