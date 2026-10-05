"""Install and launch regressions use only temporary worker state."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from fleet_worker import fleetd
from fleet.services.hosts import DETECT_SCRIPT


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv('FLEET_HOME', str(tmp_path))
    monkeypatch.setattr(fleetd, 'FLEET_HOME', tmp_path)
    monkeypatch.setattr(fleetd, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr(fleetd, 'JOBS_DIRECTORY', tmp_path / 'jobs')
    monkeypatch.setattr(fleetd, 'emit', lambda value: None)
    return tmp_path


def binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return str(path)


@pytest.mark.parametrize('guess', [None, '/different/codex'])
def test_install_preserves_executable_config(worker, guess):
    existing = binary(worker / 'node/bin/codex')
    fleetd.CONFIG_PATH.write_text(json.dumps({'codex': existing}))
    fleetd.command_configure(SimpleNamespace(json=json.dumps({'codex': guess, 'path': '/bin'})))
    config = fleetd.load_config()
    assert config['codex'] == existing
    assert config['path'].split(':')[0] == str(worker / 'node/bin')


def test_install_omits_missing_runtime(worker):
    fleetd.command_configure(SimpleNamespace(json='{"codex": null}'))
    assert 'codex' not in fleetd.load_config()


def test_explicit_override_and_invalid_override(worker):
    old = binary(worker / 'old/codex')
    new = binary(worker / 'new/codex')
    fleetd.CONFIG_PATH.write_text(json.dumps({'codex': old}))
    fleetd.command_configure(SimpleNamespace(json=json.dumps({'runtime_overrides': {'codex': new}})))
    assert fleetd.load_config()['codex'] == new
    with pytest.raises(SystemExit):
        fleetd.command_configure(SimpleNamespace(json='{"runtime_overrides": {"codex": "/missing"}}'))
    assert fleetd.load_config()['codex'] == new


def test_detection_finds_nvm_and_local_bin_deterministically(tmp_path):
    first = binary(tmp_path / '.nvm/versions/node/v18/bin/codex')
    binary(tmp_path / '.nvm/versions/node/v20/bin/codex')
    claude = binary(tmp_path / '.local/bin/claude')
    result = subprocess.run(['/bin/sh', '-c', DETECT_SCRIPT], env={'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin'},
                            capture_output=True, text=True, check=True)
    from fleet.services.hosts import merge_detected
    assert merge_detected(result.stdout)['codex'] == first
    assert merge_detected(result.stdout)['claude'] == claude


@pytest.mark.parametrize('value', [None, '/missing/codex'])
def test_unavailable_runtime_returns_failure(worker, value):
    fleetd.CONFIG_PATH.write_text(json.dumps({'codex': value}))
    outcome = fleetd._run_step_attempt({'id': 'job', 'agent': 'codex', 'permission': 'read-only',
                                       'cwd': str(worker), 'project': 'p', 'description': 'Test'}, {'index': 0, 'title': 'Run', 'prompt': 'hi'})
    assert outcome['ok'] is False
    assert 'codex runtime binary' in outcome['reason']


@pytest.mark.parametrize('location', ['interactive', 'npm'])
def test_detection_finds_shell_and_npm_paths(tmp_path, location):
    agent = binary(tmp_path / 'custom/bin/codex')
    tools = tmp_path / 'tools'
    tools.mkdir()
    for name in ('grep',):
        (tools / name).symlink_to('/usr/bin/' + name)
    tool = tools / ('zsh' if location == 'interactive' else 'npm')
    tool.write_text('#!/bin/sh\n' + (f"printf 'PATH={tmp_path}/custom/bin\\ncodex={agent}\\n'\n"
                                   if location == 'interactive' else f"echo '{tmp_path}/custom'\n"))
    tool.chmod(0o755)
    result = subprocess.run(['/bin/sh', '-c', DETECT_SCRIPT], env={'HOME': str(tmp_path), 'PATH': str(tools)},
                            capture_output=True, text=True, check=True)
    from fleet.services.hosts import merge_detected
    assert merge_detected(result.stdout)['codex'] == agent


def test_install_replaces_stale_config_with_valid_guess(worker):
    candidate = binary(worker / 'node/codex')
    fleetd.CONFIG_PATH.write_text('{"codex": "/stale/codex"}')
    fleetd.command_configure(SimpleNamespace(json=json.dumps({'codex': candidate})))
    assert fleetd.load_config()['codex'] == candidate


def test_nonexecutable_guess_is_not_recorded(worker):
    candidate = worker / 'codex'
    candidate.write_text('not executable')
    fleetd.command_configure(SimpleNamespace(json=json.dumps({'codex': str(candidate)})))
    assert 'codex' not in fleetd.load_config()


def test_fake_host_receives_explicit_paths(monkeypatch):
    from fleet import transport
    from fleet.services.hosts import HostSetup
    responses = iter(['', 'PATH=/bin\ncodex=/guess/codex\n', ''])
    requests = []
    host = transport.Host('fake', 'fake.invalid')
    adapter = SimpleNamespace(host_by_name=lambda name: host,
                              REMOTE_FLEETD_PATH=transport.REMOTE_FLEETD_PATH,
                              LOCAL_FLEETD_SOURCE=transport.LOCAL_FLEETD_SOURCE,
                              rsync=lambda *args: None,
                              run_shell=lambda *args, **kwargs: SimpleNamespace(stdout=next(responses)),
                              call=lambda host, args: requests.append(json.loads(args[1])) or {})
    HostSetup(adapter).install('fake', codex='/chosen/codex', claude='~/.local/bin/claude')
    assert requests[0]['runtime_overrides'] == {'codex': '/chosen/codex', 'claude': '~/.local/bin/claude'}


@pytest.mark.parametrize('agent', ['claude', 'codex'])
@pytest.mark.parametrize('value', [None, '/missing/runtime', '__absent__'])
def test_runner_persists_failed_step_and_reason(worker, monkeypatch, agent, value):
    fleetd.CONFIG_PATH.write_text(json.dumps({} if value == '__absent__' else {agent: value}))
    monkeypatch.setenv('PATH', '')
    monkeypatch.setattr(fleetd.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(fleetd, 'begin_step_git', lambda cwd: {'reason': 'test workspace'})
    monkeypatch.setattr(fleetd, 'end_step_git', lambda cwd, record: record)
    job = {'id': 'job', 'agent': agent, 'project': 'p', 'description': 'Test',
           'cwd': str(worker), 'permission': 'read-only', 'runner_pid': None,
           'steps': [fleetd.make_step(0, 'Run', None)]}
    directory = fleetd.JOBS_DIRECTORY / 'job'
    directory.mkdir(parents=True)
    (directory / 'job.json').write_text(json.dumps(job))
    fleetd.run_job('job')
    stored = json.loads((directory / 'job.json').read_text())
    assert stored['steps'][0]['status'] == 'failed'
    assert f'{agent} runtime binary' in stored['steps'][0]['reason']
    assert stored['runner_pid'] is None
    assert f'{agent} runtime binary' in (directory / 'result-0.md').read_text()


def test_cli_flags_and_missing_runtime_guidance(capsys):
    from fleet import transport
    from fleet_cli import cli
    parser = cli.build_parser()
    arguments = parser.parse_args(['install', 'fake', '--codex', '/chosen/codex', '--claude', '/chosen/claude'])
    calls = []
    service = SimpleNamespace(install=lambda name, **paths: calls.append((name, paths)) or
                              (transport.Host('fake', 'fake.invalid'),
                               {'host': 'fake', 'config': {'claude': '/chosen/claude'}, 'tmux': True}, ''))
    cli.command_install(arguments, container=SimpleNamespace(hosts=lambda: service))
    assert calls == [('fake', {'codex': '/chosen/codex', 'claude': '/chosen/claude'})]
    output = capsys.readouterr().out
    assert 'fleet install fake --codex PATH' in output
    assert '/chosen/claude' in output
