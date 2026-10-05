import pytest

from fleet.container import configured_container
from fleet_cli import cli


def full_building():
    workspace = configured_container().initialized_workspace()
    workspace.set_capacity(1)
    held = workspace.edit_registry(lambda registry: registry.create('Current floor'))
    return workspace, held


def test_creation_warns_with_store_floor_and_exact_restore(capsys):
    workspace, held = full_building()
    cli.main(['project', 'add', 'Overflow'])
    added = workspace.registry().resolve('Overflow')
    output = capsys.readouterr()
    assert 'has no floor' in output.err
    assert f'floor 1: {held.id} Current floor' in output.err
    assert f'fleet project restore {added} --shutter {held.id}' in output.err


def test_restore_failure_names_store_floors_and_keeps_state(capsys):
    workspace, held = full_building()
    missing = workspace.edit_registry(lambda registry: registry.create('Waiting'))
    workspace.shutter(missing.id)
    before = workspace.snapshot()
    with pytest.raises(SystemExit):
        cli.main(['project', 'restore', missing.id])
    output = capsys.readouterr().err
    assert 'has no floor' in output
    assert f'floor 1: {held.id} Current floor' in output
    assert f'fleet project restore {missing.id} --shutter {held.id}' in output
    assert workspace.snapshot() == before


def test_dispatch_refusal_uses_fresh_store_and_correct_link(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('FLEET_CONFIG', str(tmp_path / 'config.json'))
    workspace, held = full_building()
    missing = workspace.edit_registry(lambda registry: registry.create('Waiting'))
    workspace.edit_registry(lambda registry: registry.link(missing.id, 'worker', 'waiting-label'))
    workspace.shutter(missing.id)
    # A facade obtained before the mutation must still read current store data.
    workspace.edit_registry(lambda registry: registry.rename(held.id, 'Fresh name'))
    (tmp_path / 'workspace.json').write_text(json.dumps({
        'floors': {missing.id: 1}, 'shuttered': {}, 'focus': {'projects': {}, 'labels': {}}}))
    with pytest.raises(ValueError) as error:
        workspace.require_claims_allowed('waiting-label', 'worker')
    assert f'floor 1: {held.id} Fresh name' in str(error.value)
    assert f'fleet project restore {missing.id} --shutter {held.id}' in str(error.value)


def test_free_floor_guidance_and_current_placement():
    workspace, held = full_building()
    missing = workspace.edit_registry(lambda registry: registry.create('Waiting'))
    workspace.shutter(missing.id)
    workspace.shutter(held.id)
    assert f'fleet project restore {missing.id}' in workspace.floor_warning(missing.id)
    assert '--shutter' not in workspace.floor_warning(missing.id)
    assert 'floor 1: free' in workspace.floor_warning(missing.id)
    workspace.restore(missing.id)
    assert workspace.floor_warning(missing.id) is None


def test_send_warns_before_transport_without_changing_dispatch_policy(monkeypatch, capsys):
    workspace, held = full_building()
    missing = workspace.edit_registry(lambda registry: registry.create('Waiting'))
    workspace.edit_registry(lambda registry: registry.link(missing.id, 'worker', 'waiting-label'))
    from fleet import transport
    from fleet.transport import Host
    monkeypatch.setattr(transport, 'host_by_name', lambda name: Host(name, None))
    calls = []

    def call(host, arguments, **kwargs):
        if arguments[0] == 'create':
            output = capsys.readouterr().err
            assert 'has no floor' in output
            assert f'fleet project restore {missing.id} --shutter {held.id}' in output
        calls.append(arguments[0])
        run, = configured_container().execution().runs()
        return dict(id=run.remote_job_id, run_id=run.id, schema_version=4,
                    fingerprint=arguments[arguments.index('--fingerprint') + 1], status='queued',
                    steps=[{}], start_requested=False)

    monkeypatch.setattr(transport, 'call', call)
    cli.main(['send', '--project', missing.id, '--host', 'worker', '--cwd', '/repo',
              '--description', 'Check', '--step', 'Check', '--hold', '--json'])
    assert calls == ['create']
