"""Worker commands must not create a second controller store."""
import pytest

from fleet_cli import cli


@pytest.mark.parametrize('command', [
    ['status', 'p-controller'],
    ['attention', 'list', '--project', 'p-controller'],
    ['work', 'set', 'controller-item', '--next-step', 'Review', '--actor', 'codex'],
    ['criterion', 'meet', 'controller-criterion', '--actor', 'codex'],
    ['decision', 'record', '--work-item', 'controller-item', '--run', 'controller-run',
     '--question', 'Q', '--answer', 'A', '--principle', 'P', '--actor', 'codex'],
])
@pytest.mark.parametrize('existing', [False, True])
def test_worker_commands_name_controller_alternative(command, existing, tmp_path, monkeypatch, capsys):
    store = tmp_path / 'absent' / 'fleet.db'
    if existing:
        store.parent.mkdir()
        store.write_bytes(b'inaccessible controller database')
    monkeypatch.setenv('FLEET_STORE', str(store))
    monkeypatch.setenv('FLEET_JOB_ID', 'worker-job')
    with pytest.raises(SystemExit) as error:
        cli.main(command)
    assert error.value.code == 2
    assert 'controller host' in capsys.readouterr().err
    if existing:
        assert store.read_bytes() == b'inaccessible controller database'
    else:
        assert not store.parent.exists()
