"""Object dependencies obey the same boundary as imports."""
import ast
from pathlib import Path

import pytest

import fleet
from fleet.container import Container


def test_services_exposes_only_declared_capabilities():
    services = Container().services()
    assert not hasattr(services, 'container')
    assert not hasattr(type(services), '__getattr__')
    with pytest.raises(AttributeError):
        services.context
    with pytest.raises(AttributeError):
        services.overview


def test_projections_and_module_facades_never_resolve_services():
    root = Path(fleet.__file__).parent
    paths = list((root / 'projections').rglob('*.py')) + list((root / 'modules').glob('*/facade.py'))
    violations = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in {'container', 'services'}:
                violations.append(f'{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)}')
    assert not violations, '\n'.join(violations)


def test_triage_scope_supplies_bound_facades_and_rolls_back_atomically():
    container = Container()
    services = container.services()
    with pytest.raises(RuntimeError, match='rollback'):
        with services.triage_repository.transaction() as scope:
            assert not hasattr(scope, 'unit')
            assert scope.services.work is not services.work
            scope.records.save('p', {'run': 'reserved'})
            scope.services.work.add(project='p', title='Atomic', goal='Test', actor='test')
            raise RuntimeError('rollback')
    assert services.triage_repository.get('p') == {}
    assert services.work.list() == []
    assert services.store.latest_sequence() == 0
    with services.triage_repository.transaction() as scope:
        scope.records.save('p', {'run': 'committed'})
        scope.services.work.add(project='p', title='Atomic', goal='Test', actor='test')
    assert services.triage_repository.get('p') == {'run': 'committed'}
    assert services.work.list()[0].title == 'Atomic'


def test_scheduler_never_extracts_a_repository_unit():
    root = Path(fleet.__file__).parent
    tree = ast.parse((root / 'triage_scheduler.py').read_text())
    assert not [ast.unparse(node) for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr == 'unit']
