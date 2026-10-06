import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fleet.errors import FleetError
from fleet.infrastructure.codex.app_server import CodexAppServer
from fleet.infrastructure.codex.provisioning import provision
from fleet.services.live import start_live
from fleet.services.responder import ResponderWorker

from tests.responder_support import fake_codex
from tests.test_serve_runtime import state_at

LIVE_BINARY = shutil.which('codex')
LIVE_AUTH = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
SCHEMA = {'type': 'object', 'properties': {'reply': {'type': 'string'},
          'escalate': {'type': 'boolean'}, 'reason': {'type': 'string'}},
          'required': ['reply', 'escalate', 'reason'], 'additionalProperties': False}


def adapter(tmp_path, mode='normal', timeout=1):
    binary, auth = fake_codex(tmp_path / 'fake', mode)
    return CodexAppServer(tmp_path / 'fleet', binary=str(binary), auth=auth, request_timeout=timeout)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_provision_lean_private_profile_and_environment(tmp_path, monkeypatch):
    binary, auth = fake_codex(tmp_path / 'fake')
    monkeypatch.setenv('FLEET_JOB_ID', 'parent-job')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    environment = provision(tmp_path / 'fleet', binary=str(binary), auth=auth)
    assert environment.codex_home.parent == tmp_path / 'fleet' / 'responder'
    assert (environment.codex_home / 'auth.json').is_symlink()
    assert (environment.codex_home / 'auth.json').resolve() == auth
    assert list(environment.home.iterdir()) == []
    config = (environment.codex_home / 'config.toml').read_text()
    assert '__INSTRUCTIONS_PATH__' not in config and '/home/adi/models' not in config
    assert 'enabled = false' in config and 'include_environment_context = false' in config
    assert 'model_instructions_file = ' + json.dumps(str(environment.codex_home / 'instructions.md')) in config
    assert environment.home.stat().st_mode & 0o777 == 0o700
    assert 'OPENAI_API_KEY' not in environment.environ() and 'FLEET_JOB_ID' not in environment.environ()
    assert provision(tmp_path / 'fleet', binary=str(binary), auth=auth) == environment


def test_provision_missing_binary_auth_and_conflicting_path(tmp_path):
    binary, auth = fake_codex(tmp_path / 'fake')
    with pytest.raises(FleetError, match='binary missing'):
        provision(tmp_path / 'fleet', binary=str(tmp_path / 'absent'))
    with pytest.raises(FleetError, match='auth missing'):
        provision(tmp_path / 'fleet', binary=str(binary), auth=tmp_path / 'absent')
    environment = provision(tmp_path / 'fleet', binary=str(binary), auth=auth)
    other = tmp_path / 'other-auth'
    other.write_text('{}')
    with pytest.raises(FleetError, match='another source'):
        provision(tmp_path / 'fleet', binary=str(binary), auth=other)
    (environment.home / '.agents').mkdir()
    with pytest.raises(FleetError, match='instruction/config'):
        provision(tmp_path / 'fleet', binary=str(binary), auth=auth)


def test_provision_refuses_symlinked_profile_without_touching_target(tmp_path):
    binary, auth = fake_codex(tmp_path / 'fake')
    profile = tmp_path / 'fleet' / 'responder' / 'codex'
    profile.mkdir(parents=True)
    original = tmp_path / 'user-config'
    original.write_text('original user config')
    (profile / 'config.toml').symlink_to(original)
    with pytest.raises(FleetError, match='must not be a symlink'):
        provision(tmp_path / 'fleet', binary=str(binary), auth=auth)
    assert original.read_text() == 'original user config'


def test_adapter_initialization_callbacks_usage_and_reused_thread(tmp_path):
    server = adapter(tmp_path)
    try:
        server.start()
        health = server.health()
        assert health['ready'] and health['alive'] and health['pid']
        assert health['codex_version'] == 'codex-cli fake-0.160.1'
        assert health['startup_s'] > 0
        tid = server.start_thread()
        deltas, completed = [], []
        first = server.turn(tid, 'ping', output_schema=SCHEMA, on_delta=deltas.append, on_complete=completed.append)
        second = server.turn(tid, 'ping again', output_schema=SCHEMA)
        assert ''.join(deltas) == first.text
        assert json.loads(first.text)['reply'] == 'pong'
        assert completed == [first] and first.status == 'completed'
        assert first.usage['inputTokens'] == 2200
        assert 0 < first.start_s <= first.total_s and first.first_delta_s <= first.total_s
        assert second.thread_id == first.thread_id and second.turn_id != first.turn_id
        assert 'text' not in server.health()['last_turn']
        assert not server.pending and not server.events
    finally:
        server.close()
    assert not server.health()['alive']
    assert not any(reader.is_alive() for reader in server.readers)
    server.close()


@pytest.mark.parametrize('mode, expected', [('init-death', 'stdout closed'),
    ('init-hang', 'timed out'), ('malformed', 'protocol failed')])
def test_initialization_failure_is_loud_and_reaps_process(tmp_path, mode, expected):
    server = adapter(tmp_path, mode, .15)
    with pytest.raises(FleetError, match=expected):
        server.start()
    assert server.health()['error'] and not server.health()['alive']
    assert not server.pending


@pytest.mark.parametrize('mode, expected', [('turn-death', 'stdout closed'),
    ('hang', 'timed out'), ('start-hang', 'timed out'), ('rpc-error', 'bad turn'),
    ('server-request', 'unexpected app-server request'), ('failed', 'scripted model failure')])
def test_turn_errors_never_leave_waiters_or_unobserved_turns(tmp_path, mode, expected):
    server = adapter(tmp_path, mode, .3)
    try:
        server.start()
        before = time.monotonic()
        with pytest.raises(FleetError, match=expected):
            server.turn(server.warm_thread, 'ping', output_schema=SCHEMA, timeout=.2)
        assert time.monotonic() - before < 2
        assert not server.pending and not server.events
        if mode in ('hang', 'start-hang'):
            assert not server.health()['alive']
    finally:
        server.close()


def test_interrupt_and_close_unblock_inflight_turn(tmp_path):
    server = adapter(tmp_path, 'hang')
    server.start()
    errors = []
    def turn():
        try:
            server.turn(server.warm_thread, 'ping', output_schema=SCHEMA, timeout=10)
        except FleetError as error:
            errors.append(str(error))
    thread = threading.Thread(target=turn)
    thread.start()
    try:
        wait_for(lambda: bool(server.events) and server.counter >= 3)
        server.interrupt(server.warm_thread, 'turn-1')
        thread.join(1)
        assert not thread.is_alive() and 'interrupted' in errors[0]
        errors.clear()
        thread = threading.Thread(target=turn)
        thread.start()
        wait_for(lambda: bool(server.events))
        server.close()
        thread.join(1)
        assert not thread.is_alive() and errors
    finally:
        server.close()
        thread.join(1)


def test_callback_failure_retires_server_and_releases_turn_lock(tmp_path):
    server = adapter(tmp_path)
    def broken(delta):
        raise RuntimeError('callback failed')
    try:
        server.start()
        with pytest.raises(RuntimeError, match='callback failed'):
            server.turn(server.warm_thread, 'ping', output_schema=SCHEMA, on_delta=broken)
        assert not server.health()['alive']
        assert not server.turn_lock.locked() and not server.events
    finally:
        server.close()


def test_close_terminates_wrapper_descendants_and_pipe_readers(tmp_path):
    server = adapter(tmp_path, 'child')
    server.start()
    child_pid = int((server.environment.codex_home / 'child.pid').read_text())
    assert os.getpgid(child_pid) == server.process.pid
    before = time.monotonic()
    server.close()
    assert time.monotonic() - before < 2
    assert not any(reader.is_alive() for reader in server.readers)
    # An adopted child can briefly remain a zombie before init reaps it.
    stat = Path(f'/proc/{child_pid}/stat')
    try:
        state = stat.read_text().split()[2]
    except FileNotFoundError:
        state = None  # Reaped between close and the read: the required dead-child outcome.
    assert state is None or state == 'Z'


def test_concurrent_close_waits_for_uncooperative_process_exit(tmp_path):
    server = adapter(tmp_path, 'ignore-term')
    server.start()
    def close_and_check():
        server.close()
        return server.process.poll()
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda _: close_and_check(), range(4)))
    assert all(code is not None for code in codes)
    assert not any(reader.is_alive() for reader in server.readers)


def test_serve_supervision_reports_child_and_restarts_after_death(tmp_path):
    servers = []
    def factory():
        server = adapter(tmp_path)
        servers.append(server)
        return server
    state = state_at(tmp_path / 'fleet.db')
    state.responder = ResponderWorker(factory)
    runtime = start_live(state)
    try:
        wait_for(lambda: runtime.health()['workers']['responder'].get('ready'))
        first = state.responder.client()
        first.turn(first.warm_thread, 'ping', output_schema=SCHEMA)
        health = runtime.health()['workers']['responder']
        assert health['worker_alive'] and health['alive'] and health['pid'] == first.process.pid
        assert health['last_turn']['usage']['inputTokens'] == 2200
        first.process.kill()
        first.process.wait()
        wait_for(lambda: len(servers) == 2 and runtime.health()['workers']['responder'].get('ready'))
        health = runtime.health()['workers']['responder']
        assert runtime.health()['healthy'] and health['restarts'] == 1
        assert health['pid'] != first.process.pid and 'stdout closed' in health['last_error']
        assert health['last_turn']['usage']['inputTokens'] == 2200
    finally:
        runtime.close()
    assert all(server.process.poll() is not None for server in servers)
    assert not any(thread.is_alive() for thread in runtime.threads)


def test_serve_missing_auth_visible_with_backoff(tmp_path):
    binary, _ = fake_codex(tmp_path / 'fake')
    state = state_at(tmp_path / 'fleet.db')
    state.responder = ResponderWorker(lambda: CodexAppServer(tmp_path / 'fleet', binary=str(binary),
                                                           auth=tmp_path / 'absent'))
    runtime = start_live(state)
    try:
        wait_for(lambda: runtime.health()['workers']['responder'].get('restarts', 0) >= 1)
        health = runtime.health()['workers']['responder']
        assert not runtime.health()['healthy']
        assert not health['alive'] and 'auth missing' in health['error']
        with pytest.raises(FleetError, match='auth missing'):
            state.responder.client()
    finally:
        runtime.close()


@pytest.mark.skipif(os.environ.get('FLEET_LIVE_CODEX') != '1', reason='opt-in real Codex')
def test_live_codex_lean_turn(tmp_path):
    assert LIVE_BINARY, 'codex binary missing'
    assert LIVE_AUTH.is_file(), 'codex auth missing'
    server = CodexAppServer(tmp_path / 'fleet', binary=LIVE_BINARY, auth=LIVE_AUTH)
    try:
        server.start()
        result = server.turn(server.warm_thread, 'Document says: the test word is pong.\n'
                             'User: What is the test word? Reply with pong.', output_schema=SCHEMA)
        assert result.status == 'completed'
        assert json.loads(result.text) == {'reply': 'pong', 'escalate': False, 'reason': ''}
        assert result.usage is not None, 'token usage notification missing'
        assert 0 < result.usage['inputTokens'] < 3000
        measurement = {'codex_version': server.version, 'startup_s': server.startup_s,
                       **server.health()['last_turn']}
        print('LIVE_CODEX_MEASUREMENT ' + json.dumps(measurement, sort_keys=True))
        (tmp_path / 'live-codex.json').write_text(json.dumps(measurement, indent=2) + '\n')
    finally:
        server.close()
