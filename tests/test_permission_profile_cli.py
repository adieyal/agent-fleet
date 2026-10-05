"""A named review profile expands into the job's existing explicit grants."""
import json

import pytest

from fleet.container import configured_container
from fleet_cli import cli
from tests.test_cli_help import fake_worker


@pytest.mark.parametrize('build', [False, True])
def test_review_profile_dispatches_and_snapshots_rules(monkeypatch, project_id, build):
    configured_container().initialized_workspace().edit_registry(
        lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    calls = []
    fake_worker(monkeypatch, calls)
    command = ['send', '--project', 'p', '--description', 'Review', '--step', 'Check',
              '--host', 'fake', '--cwd', '/repo', '--hold', '--json',
              '--permission', 'acceptEdits', '--allow-profile', 'review',
              '--allow', 'Bash(grep:*)']
    if build:
        command += ['--allow', 'Bash(docker build:*)']
    cli.main(command)
    create, = [call for call in calls if call[0] == 'create']
    rules = json.loads(create[create.index('--allowed-tools') + 1])
    assert 'Bash(uv run pytest:*)' in rules
    assert 'Bash(sed -n:*)' in rules
    assert 'Bash(lint-imports:*)' in rules
    assert 'Bash(make test:*)' in rules
    assert 'Bash(docker ps:*)' in rules
    assert ('Bash(docker build:*)' in rules) is build
    assert rules.count('Bash(grep:*)') == 1
    assert 'Bash' not in rules and 'Bash(make:*)' not in rules
    assert 'Bash(fleet decision record:*)' not in rules
    run, = configured_container().execution().runs()
    action, = configured_container().execution().actions()
    assert action.id == run.action
    stored = action.payload['arguments']
    assert json.loads(stored[stored.index('--allowed-tools') + 1]) == rules


def test_codex_profile_refused_before_dispatch(monkeypatch, project_id, capsys):
    calls = []
    fake_worker(monkeypatch, calls)
    with pytest.raises(SystemExit) as error:
        cli.main(['send', '--project', 'p', '--description', 'Review', '--step', 'Check',
                  '--host', 'fake', '--cwd', '/repo', '--runtime', 'codex', '--allow-profile', 'review'])
    assert error.value.code == 2
    assert 'Claude jobs only' in capsys.readouterr().err
    assert calls == [] and configured_container().execution().actions() == []


def test_work_item_dispatch_accepts_review_profile(monkeypatch, project_id):
    configured_container().initialized_workspace().edit_registry(
        lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    item = configured_container().work().add(project=project_id, title='Review', goal='Check', actor='user')
    calls = []
    fake_worker(monkeypatch, calls)
    cli.main(['dispatch', item.id, 'Check', '--host', 'fake', '--cwd', '/repo',
              '--runtime', 'claude', '--allow-profile', 'review', '--json'])
    create, = [call for call in calls if call[0] == 'create']
    assert 'Bash(docker ps:*)' in json.loads(create[create.index('--allowed-tools') + 1])


def test_without_profile_does_not_add_test_permissions(monkeypatch, project_id):
    configured_container().initialized_workspace().edit_registry(
        lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    calls = []
    fake_worker(monkeypatch, calls)
    cli.main(['send', '--project', 'p', '--description', 'Review', '--step', 'Check',
              '--host', 'fake', '--cwd', '/repo', '--hold', '--json'])
    create, = [call for call in calls if call[0] == 'create']
    assert '--allowed-tools' not in create


def test_unknown_profile_rejected_before_transport(monkeypatch, capsys):
    calls = []
    fake_worker(monkeypatch, calls)
    with pytest.raises(SystemExit) as error:
        cli.main(['send', '--project', 'p', '--description', 'Review', '--step', 'Check',
                  '--host', 'fake', '--cwd', '/repo', '--allow-profile', 'anything'])
    assert error.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err
    assert calls == []
