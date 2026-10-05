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
        state.snapshot_view = lambda: state
        state.accept_snapshot_view = lambda view: None
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


def runtime_state(tmp_path):
    state = state_at(tmp_path / 'cache.db')
    state.changed = threading.Condition()
    state.by_host = {'host': {'ok': True, 'error': None}}
    state.version, state.pipeline_seq = 1, 0
    state.pipeline_updates = lambda after: []
    state.snapshot_view = lambda: state
    state.accept_snapshot_view = lambda view: None
    state.wait_for_change = lambda *args: time.sleep(0.01)
    return state


def test_snapshot_shared_once_per_sequence_and_pipeline_generation(tmp_path):
    state = runtime_state(tmp_path)
    builds = []
    def document():
        builds.append(state.version)
        time.sleep(0.02)
        return {'hosts': []}
    state.document = document
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    try:
        callers = [threading.Thread(target=server.snapshot) for _ in range(6)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(2)
            assert not caller.is_alive()
        assert builds == [1]
        with state.changed:
            state.version = 2
        assert server.snapshot()['sequence'] == 2
        assert builds == [1, 2]
        with state.changed:
            state.pipeline_seq = 1
        assert server.snapshot()['pipeline_sequence'] == 1
        assert builds == [1, 2, 2]
    finally:
        runtime.close()
        server.close()


def test_http_health_answers_while_snapshot_build_is_held(tmp_path):
    state = runtime_state(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def document():
        entered.set()
        assert release.wait(5)
        return {'hosts': []}
    state.document = document
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    http = threading.Thread(target=server.http.serve_forever)
    builder = threading.Thread(target=server.snapshot)
    http.start()
    builder.start()
    try:
        assert entered.wait(1)
        before = time.monotonic()
        health = runtime_status(tmp_path / 'cache.db')
        assert time.monotonic() - before < 1
        assert health['healthy'] and health['hosts']['host']['ok']
    finally:
        release.set()
        builder.join(2)
        runtime.close()
        server.http.shutdown()
        http.join()
        server.close()


def test_live_view_releases_ingestion_and_keeps_generation_inputs(tmp_path, monkeypatch):
    from fleet.services.live import FleetState
    state = FleetState.__new__(FleetState)
    state.__dict__.update(runtime_state(tmp_path).__dict__)
    del state.snapshot_view
    del state.accept_snapshot_view
    state.registry, state.capacity, state.work_links = object(), 6, None
    state.by_host['host']['jobs'] = {'job': {'status': 'running'}}
    state.pipeline_runs = {('host', 'build'): {'seq': 0}}
    state.pipeline_updates = lambda after: []
    entered, release = threading.Event(), threading.Event()
    builds = []
    def document(view):
        with view.changed:
            entered.set()
            assert release.wait(3)
            builds.append(view.by_host['host']['jobs']['job']['status'])
            return {'hosts': [], 'job_status': builds[-1]}
    monkeypatch.setattr(FleetState, 'document', document)
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    results = []
    builder = threading.Thread(target=lambda: results.append(server.snapshot()))
    builder.start()
    try:
        assert entered.wait(1)
        # The real projection view holds its own lock. Live ingestion can still
        # advance both counters and mutate nested job observations.
        assert state.changed.acquire(timeout=0.2)
        try:
            state.by_host['host']['jobs']['job']['status'] = 'succeeded'
            state.version += 1
            state.pipeline_seq += 1
            state.pipeline_runs[('host', 'build')]['seq'] = 1
        finally:
            state.changed.release()
        release.set()
        builder.join(2)
        assert results[0]['sequence'] == 1
        assert results[0]['document']['job_status'] == 'running'
        latest = server.snapshot()
        assert (latest['sequence'], latest['pipeline_sequence']) == (2, 1)
        assert latest['document']['job_status'] == 'succeeded'
        assert builds == ['running', 'succeeded']
    finally:
        release.set()
        builder.join(2)
        runtime.close()
        server.close()


def test_health_does_not_wait_for_host_catch_up_lock(tmp_path):
    state = runtime_state(tmp_path)
    state.document = lambda: {'hosts': []}
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    entered, release = threading.Event(), threading.Event()
    def catch_up():
        with state.changed:
            entered.set()
            release.wait(3)
    catcher = threading.Thread(target=catch_up)
    http = threading.Thread(target=server.http.serve_forever)
    catcher.start()
    http.start()
    try:
        assert entered.wait(1)
        before = time.monotonic()
        assert runtime_status(tmp_path / 'cache.db')['healthy']
        assert time.monotonic() - before < 1
    finally:
        release.set()
        catcher.join(2)
        runtime.close()
        server.http.shutdown()
        http.join()
        server.close()


def test_wire_snapshot_encoded_once_and_health_remains_current(tmp_path, monkeypatch):
    import fleet.services.runtime as contract
    state = runtime_state(tmp_path)
    state.document = lambda: {'hosts': []}
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    encodings = []
    original = contract.json.dumps
    def dumps(value, *args, **kwargs):
        if isinstance(value, dict) and 'document' in value:
            encodings.append(value['sequence'])
        return original(value, *args, **kwargs)
    monkeypatch.setattr(contract.json, 'dumps', dumps)
    try:
        for _ in range(4):
            value, body = server.snapshot_bytes()
            assert json.loads(body) == value
        assert encodings == [1]
        runtime.errors['history-scheduler'] = 'failed after publication'
        value, body = server.snapshot_bytes()
        assert not json.loads(body)['health']['healthy']
        assert not value['health']['healthy']
        assert encodings == [1]
        state.version += 1
        assert server.snapshot_bytes()[0]['sequence'] == 2
        assert encodings == [1, 2]
    finally:
        runtime.close()
        server.close()


def test_view_keeps_refreshed_registry_for_subsequent_ingestion(tmp_path, monkeypatch):
    from fleet.services.live import FleetState
    state = FleetState.__new__(FleetState)
    state.__dict__.update(runtime_state(tmp_path).__dict__)
    del state.snapshot_view
    del state.accept_snapshot_view
    state.pipeline_runs = {}
    state.registry, state.capacity, state.work_links = object(), 6, None
    current_registry = object()
    current_links = ('revision', {}, {})
    def document(view):
        view.registry = current_registry
        view.capacity = 8
        view.work_links = current_links
        return {'hosts': []}
    monkeypatch.setattr(FleetState, 'document', document)
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    try:
        server.snapshot()
        assert state.registry is current_registry
        assert state.capacity == 8
        assert state.work_links is current_links
    finally:
        runtime.close()
        server.close()
