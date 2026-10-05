from fleet.container import configured_container
from fleet import transport
import json

import pytest

from fleet_cli import cli
from fleet.transport import Host


def test_orchestrate_starts_locally_with_activation_command_set(tmp_path, monkeypatch, capsys):
    import subprocess
    root = tmp_path / 'records'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True, timeout=10)
    workspace = configured_container().initialized_workspace()
    project = workspace.edit_registry(lambda registry: registry.create('p')).id
    workspace.edit_registry(lambda registry: registry.link(project, 'controller', 'worker-p'))
    item = configured_container().work().add(project=project, title='Ship', goal='Ship', actor='user')
    criterion = configured_container().work().add_criterion(item.id, text='Review', verification='judged', actor='user')
    records = configured_container().records()
    records.register(project, root, actor='user')
    records.write_mandate(project, 'mandate.json', json.dumps(dict(goal='Ship', constraints=[],
        escalation_conditions=[], criteria_it_may_judge=[criterion.id], decision_authority=['dispatch', 'update_progress'])),
        key='mandate', actor='user')
    calls = []
    def call(host, arguments, **fields):
        assert host.is_local
        run, = configured_container().execution().runs()
        calls.append((arguments, fields))
        return dict(id=run.remote_job_id, run_id=run.id, schema_version=4,
                    fingerprint=arguments[arguments.index('--fingerprint') + 1],
                    status='running' if arguments[0] == 'start' else 'queued', start_requested=False)
    monkeypatch.setattr(transport, 'host_by_name', lambda name: Host(name, None))
    monkeypatch.setattr(transport, 'call', call)
    cli.main(['orchestrate', item.id, '--mandate', 'mandate.json', '--host', 'controller',
              '--runtime', 'codex', '--cwd', str(tmp_path), '--permission', 'danger-full-access'])
    result = json.loads(capsys.readouterr().out)
    action, = configured_container().execution().actions()
    assert action.activation == result['activation']
    assert [entry[0][0] for entry in calls] == ['create', 'start']
    assert calls[0][0][calls[0][0].index('--project') + 1] == 'worker-p'
    assert calls[0][0][calls[0][0].index('--permission') + 1] == 'danger-full-access'
    prompt = json.loads(calls[0][1]['stdin_text'])[0]['prompt']
    assert result['activation'] in prompt and 'fleet control' in prompt
    cli.main(['control', result['activation'], 'progress', '{"next_step":"Review"}'])
    assert configured_container().work().get(item.id).next_step == 'Review'
    cli.main(['control', result['activation'], 'state', '{}'])
    assert 'Review' in capsys.readouterr().out


def test_orchestrate_refuses_remote_host_before_writing(monkeypatch, capsys):
    monkeypatch.setattr(transport, 'host_by_name', lambda name: Host(name, 'remote'))
    before = configured_container().store().latest_sequence()
    with pytest.raises(SystemExit):
        cli.main(['orchestrate', 'work', '--mandate', 'mandate.json', '--host', 'remote',
                  '--runtime', 'codex', '--cwd', '/repo'])
    assert configured_container().store().latest_sequence() == before
    assert 'controller machine' in capsys.readouterr().err


@pytest.mark.parametrize('invalid', ['missing-id', 'Review the release', 'other-work-item'])
def test_orchestrate_rejects_criterion_outside_work_item(tmp_path, monkeypatch, capsys, invalid):
    import subprocess
    from unittest.mock import Mock

    root = tmp_path / 'records'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True, timeout=10)
    work = configured_container().work()
    item = work.add(project='p', title='Ship', goal='Ship', actor='user')
    criterion = work.add_criterion(item.id, text='Review', verification='judged', actor='user')
    if invalid == 'other-work-item':
        other = work.add(project='p', title='Other', goal='Other', actor='user')
        invalid = work.add_criterion(other.id, text='Other review', verification='judged', actor='user').id
    records = configured_container().records()
    records.register('p', root, actor='user')
    records.write_mandate('p', 'mandate.json', json.dumps(dict(goal='Ship', constraints=[],
        escalation_conditions=[], criteria_it_may_judge=[criterion.id, invalid], decision_authority=['dispatch'])),
        key='mandate', actor='user')
    call = Mock(side_effect=AssertionError('invalid mandate must not dispatch'))
    monkeypatch.setattr(transport, 'host_by_name', lambda name: Host(name, None))
    monkeypatch.setattr(transport, 'call', call)
    before = configured_container().store().latest_sequence()
    with pytest.raises(SystemExit) as rejected:
        cli.main(['orchestrate', item.id, '--mandate', 'mandate.json', '--host', 'controller',
                  '--runtime', 'codex', '--cwd', str(tmp_path)])
    assert rejected.value.code != 0
    message = capsys.readouterr().err
    assert 'criteria_it_may_judge' in message
    assert invalid in message
    assert item.id in message
    assert configured_container().store().latest_sequence() == before
    call.assert_not_called()


def test_orchestrate_refuses_a_missing_host_link(monkeypatch, capsys, project_id, tmp_path):
    item = configured_container().work().add(project=project_id, title='Ship', goal='Ship', actor='user')
    monkeypatch.setattr(transport, 'host_by_name', lambda name: Host(name, None))
    monkeypatch.setattr(transport, 'call', lambda *a, **kw: pytest.fail('must not contact worker'))
    import subprocess
    root = tmp_path / 'records'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
    records = configured_container().records()
    records.register(project_id, root, actor='user')
    records.write_mandate(project_id, 'mandate.json', json.dumps(dict(goal='Ship', constraints=[],
        escalation_conditions=[], criteria_it_may_judge=[], decision_authority=['dispatch'])),
        key='mandate', actor='user')
    with pytest.raises(SystemExit):
        cli.main(['orchestrate', item.id, '--mandate', 'mandate.json', '--host', 'controller',
                  '--runtime', 'codex', '--cwd', '/repo'])
    assert f'fleet project link {project_id} controller:p' in capsys.readouterr().err
    assert configured_container().execution().runs() == []
