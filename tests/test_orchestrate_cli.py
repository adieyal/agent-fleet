import json

import pytest

from fleet import cli, composition
from fleet.transport import Host


def test_orchestrate_starts_locally_with_activation_command_set(tmp_path, monkeypatch, capsys):
    import subprocess
    root = tmp_path / 'records'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True, timeout=10)
    item = composition.open_work().add(project='p', title='Ship', goal='Ship', actor='user')
    records = composition.open_records()
    records.register('p', root, actor='user')
    records.write_mandate('p', 'mandate.json', json.dumps(dict(goal='Ship', constraints=[],
        escalation_conditions=[], criteria_it_may_judge=[], decision_authority=['dispatch', 'update_progress'])),
        key='mandate', actor='user')
    calls = []
    def call(host, arguments, **fields):
        assert host.is_local
        run, = composition.open_execution().runs()
        calls.append((arguments, fields))
        return dict(id=run.remote_job_id, run_id=run.id, schema_version=4,
                    fingerprint=arguments[arguments.index('--fingerprint') + 1],
                    status='running' if arguments[0] == 'start' else 'queued', start_requested=False)
    monkeypatch.setattr(cli.transport, 'host_by_name', lambda name: Host(name, None))
    monkeypatch.setattr(cli.transport, 'call', call)
    cli.main(['orchestrate', item.id, '--mandate', 'mandate.json', '--host', 'controller',
              '--runtime', 'codex', '--cwd', str(tmp_path), '--permission', 'danger-full-access'])
    result = json.loads(capsys.readouterr().out)
    action, = composition.open_execution().actions()
    assert action.activation == result['activation']
    assert [entry[0][0] for entry in calls] == ['create', 'start']
    assert calls[0][0][calls[0][0].index('--permission') + 1] == 'danger-full-access'
    prompt = json.loads(calls[0][1]['stdin_text'])[0]['prompt']
    assert result['activation'] in prompt and 'fleet control' in prompt
    cli.main(['control', result['activation'], 'progress', '{"next_step":"Review"}'])
    assert composition.open_work().get(item.id).next_step == 'Review'
    cli.main(['control', result['activation'], 'state', '{}'])
    assert 'Review' in capsys.readouterr().out


def test_orchestrate_refuses_remote_host_before_writing(monkeypatch, capsys):
    monkeypatch.setattr(cli.transport, 'host_by_name', lambda name: Host(name, 'remote'))
    before = composition.open_store().latest_sequence()
    with pytest.raises(SystemExit):
        cli.main(['orchestrate', 'work', '--mandate', 'mandate.json', '--host', 'remote',
                  '--runtime', 'codex', '--cwd', '/repo'])
    assert composition.open_store().latest_sequence() == before
    assert 'controller machine' in capsys.readouterr().err
