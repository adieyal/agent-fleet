import json
import runpy
import sys
from pathlib import Path

import pytest

from fleet.container import configured_container
from fleet.cli import main


def register(name):
    return configured_container().initialized_workspace().edit_registry(lambda registry: registry.create(name)).id


@pytest.mark.parametrize('use_id', [False, True])
def test_work_and_status_resolve_project(use_id, capsys):
    identity = register('Restoke V2')
    reference = identity if use_id else 'Restoke V2'
    main(['work', 'add', 'Task', '--project', reference, '--goal', 'Ship', '--actor', 'user'])
    assert json.loads(capsys.readouterr().out)['project'] == identity
    main(['status', reference, '--json'])
    result = json.loads(capsys.readouterr().out)
    assert result['project'] == identity
    assert len(result['work_items']) == 1


@pytest.mark.parametrize('command', ['work', 'status', 'seed'])
@pytest.mark.parametrize('ambiguous', [True, False])
def test_invalid_project_rejected_without_work(command, ambiguous, capsys, monkeypatch):
    candidates = [register('Restoke V2') for _ in range(2)] if ambiguous else []
    configured_container().initialized_workspace()
    sequence = configured_container().store().latest_sequence()
    if command == 'seed':
        script = Path(__file__).parents[1] / 'scripts/seed_supplier_slice.py'
        monkeypatch.setattr(sys, 'argv', [str(script), '--project', 'Restoke V2'])
        with pytest.raises(SystemExit):
            runpy.run_path(str(script), run_name='__main__')
    else:
        args = (['status', 'Restoke V2'] if command == 'status' else
                ['work', 'add', 'Task', '--project', 'Restoke V2', '--goal', 'Ship', '--actor', 'user'])
        with pytest.raises(SystemExit):
            main(args)
    error = capsys.readouterr().err
    assert ('ambiguous' if ambiguous else 'unknown') in error.lower()
    if ambiguous:
        assert all(identity in error for identity in candidates)
        assert 'fleet project merge' in error
    assert configured_container().work().list() == []
    assert configured_container().store().latest_sequence() == sequence


def test_seed_explicit_id(monkeypatch):
    identity = register('Restoke V2')
    register('Restoke V2')
    script = Path(__file__).parents[1] / 'scripts/seed_supplier_slice.py'
    monkeypatch.setattr(sys, 'argv', [str(script), '--project', identity])
    runpy.run_path(str(script), run_name='__main__')
    assert {item.project for item in configured_container().work().list()} == {identity}


def test_attention_and_library_resolve_unique_name(capsys):
    identity = register('Restoke V2')
    main(['attention', 'add', 'Choose', '--project', 'Restoke V2', '--kind', 'decision',
          '--owner', 'user', '--source', 'manual', '--source-reference', 'q',
          '--context-reference', 'doc', '--actor', 'user'])
    assert json.loads(capsys.readouterr().out)['project'] == identity
    main(['attention', 'list', '--project', identity])
    assert json.loads(capsys.readouterr().out)[0]['project'] == identity
    main(['library', 'link', 'https://example.org/report', '--project', 'Restoke V2'])
    assert json.loads(capsys.readouterr().out)['project'] == identity
