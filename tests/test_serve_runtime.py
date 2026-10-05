import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from fleet.api import FleetError
from fleet.services.live import start_live
from fleet.services.runtime import RuntimeServer, runtime_status


def state_at(path, history=None):
    return SimpleNamespace(container=SimpleNamespace(settings=lambda: {'store_path': path}),
                           hosts=[], follow_history=history or (lambda stop: stop.wait()))


def test_canonical_lock_refuses_other_state_and_other_process(tmp_path):
    path = tmp_path / 'fleet.db'
    runtime = start_live(state_at(path))
    try:
        with pytest.raises(FleetError, match='runtime already owned'):
            start_live(state_at(tmp_path / 'sub' / '..' / 'fleet.db'))
        code = '''import fcntl, sys
with open(sys.argv[1], 'a+') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(7)
'''
        result = subprocess.run([sys.executable, '-c', code, str(path) + '.runtime.lock'])
        assert result.returncode == 7
    finally:
        runtime.close()
    successor = start_live(state_at(path))
    successor.close()


def test_failed_worker_visible_and_close_bounded(tmp_path):
    def broken(stop):
        raise RuntimeError('scheduler broke')
    runtime = start_live(state_at(tmp_path / 'failed.db', broken))
    runtime.threads[0].join(1)
    assert not runtime.health()['healthy']
    assert 'scheduler broke' in runtime.health()['workers']['history-scheduler']['error']
    runtime.close()
    release = threading.Event()
    runtime = start_live(state_at(tmp_path / 'blocked.db', lambda stop: release.wait()))
    try:
        before = time.monotonic()
        runtime.close(timeout=0.05)
        assert time.monotonic() - before < 0.5
        with pytest.raises(FleetError, match='runtime already owned'):
            start_live(state_at(tmp_path / 'blocked.db'))
    finally:
        release.set()
        runtime.close()


def test_snapshot_subscription_offline_health_and_generation(tmp_path):
    def make():
        state = state_at(tmp_path / 'fleet.db')
        state.changed = threading.Condition()
        state.by_host = {'offline': {'ok': False, 'error': 'unreachable'}}
        state.version, state.pipeline_seq = 37, 4
        state.document = lambda: {'hosts': ['offline']}
        state.pipeline_updates = lambda after: [{'name': 'build', 'seq': 4}]
        state.wait_for_change = lambda *args: time.sleep(0.01)
        runtime = start_live(state)
        server = RuntimeServer(state, runtime)
        thread = threading.Thread(target=server.http.serve_forever)
        thread.start()
        return runtime, server, thread
    runtime, server, thread = make()
    try:
        health = runtime_status(tmp_path / 'fleet.db')
        assert health['healthy'] and not health['hosts']['offline']['ok']
        url = f'http://127.0.0.1:{server.http.server_port}'
        with urlopen(url + '/snapshot', timeout=2) as response:
            value = json.load(response)
        assert value['contract_version'] == 1 and value['sequence'] == 37
        assert value['pipelines'] == [{'name': 'build', 'seq': 4}]
        with urlopen(url + '/subscribe', timeout=2) as response:
            assert response.readline() == b'event: snapshot\n'
            data = json.loads(response.readline().decode().removeprefix('data: '))
            assert data['generation'] == value['generation']
            with server.state.changed:
                server.state.version = 38
                server.state.pipeline_seq = 5
            deadline = time.monotonic() + 2
            while data['sequence'] != 38:
                assert time.monotonic() < deadline
                line = response.readline().decode()
                if line.startswith('data: '):
                    data = json.loads(line.removeprefix('data: '))
            assert data['pipeline_sequence'] == 5
    finally:
        runtime.close()
        server.http.shutdown()
        thread.join()
        server.close()
    with pytest.raises(FleetError, match='runtime unavailable'):
        runtime_status(tmp_path / 'fleet.db')
    runtime, server, thread = make()
    try:
        assert server.snapshot()['generation'] != value['generation']
    finally:
        runtime.close()
        server.http.shutdown()
        thread.join()
        server.close()


def test_cli_sigterm_status_and_duplicate_owner(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'hosts': {'offline': {'ssh': None, 'python': '/bin/false'}}}))
    env = dict(os.environ, FLEET_CONFIG=str(config), FLEET_STORE=str(tmp_path / 'fleet.db'),
               FLEET_HOME=str(tmp_path / 'home'), FLEET_MANAGEMENT=str(tmp_path / 'management'))
    command = [sys.executable, '-m', 'fleet_cli.cli', 'serve']
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                health = runtime_status(tmp_path / 'fleet.db')
                break
            except FleetError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f'serve failed: {process.communicate(timeout=1)}')
                time.sleep(0.05)
        assert health['pid'] == process.pid
        status = subprocess.run(command + ['status'], env=env, capture_output=True, timeout=5)
        assert status.returncode == 0
        assert json.loads(status.stdout)['healthy']
        duplicate = subprocess.run(command, env=env, capture_output=True, timeout=5)
        assert duplicate.returncode == 2 and b'runtime already owned' in duplicate.stderr
        before = time.monotonic()
        process.terminate()
        process.communicate(timeout=8)
        assert process.returncode == 0 and time.monotonic() - before < 8
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_unexpected_worker_return_visible(tmp_path):
    runtime = start_live(state_at(tmp_path / 'fleet.db', lambda stop: None))
    runtime.threads[0].join(1)
    assert runtime.health()['workers']['history-scheduler']['error'] == 'worker exited unexpectedly'
    assert not runtime.health()['healthy']
    runtime.close()


def test_start_failure_releases_lock(tmp_path, monkeypatch):
    original = threading.Thread.start
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', lambda self: (_ for _ in ()).throw(RuntimeError('start failed')))
        with pytest.raises(RuntimeError, match='start failed'):
            start_live(state_at(tmp_path / 'fleet.db'))
    assert threading.Thread.start is original
    runtime = start_live(state_at(tmp_path / 'fleet.db'))
    runtime.close()


def test_status_does_not_create_store(tmp_path, capsys):
    from fleet_cli.cli import main
    from fleet.container import Container
    from dependency_injector import providers
    container = Container()
    container.settings.override(dict(container.settings(), store_path=tmp_path / 'absent.db'))
    container.runtime_status.override(providers.Callable(lambda: {'pid': 123, 'healthy': True}))
    container.store.override(providers.Callable(lambda: pytest.fail('status opened store')))
    main(['serve', 'status'], container=container)
    assert json.loads(capsys.readouterr().out)['pid'] == 123
    assert not (tmp_path / 'absent.db').exists()
