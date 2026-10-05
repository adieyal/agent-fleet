"""Real serve/web process separation, with a local scripted fleetd and temporary paths."""
import json
import os
import socket
import subprocess
import sys
from urllib.request import urlopen

from fleet.container import Container

from tests.responder_support import fake_codex
from tests.runtime_support import eventually

WORKER = '''import json, os, sys, time
from pathlib import Path
state = Path(os.environ['TEST_WORKER_STATUS'])
def job():
    return dict(id='process-job', project='p', description='Process independence',
                status=state.read_text(), agent='codex', created_at=1,
                updated_at=time.time(), steps=[], documents=[])
def emit(value):
    print(json.dumps(value), flush=True)
command = sys.argv[1]
if command == 'version':
    emit({'wire_protocol_version': 1})
elif command == 'ls':
    emit({'jobs': [job()]})
elif command == 'sessions':
    emit({'sessions': []})
elif command == 'stream':
    with open(os.environ['TEST_WORKER_STARTS'], 'a') as log:
        log.write(str(os.getpid()) + '\\n')
    emit({'type': 'hello', 'wire_protocol_version': 1})
    emit({'type': 'heartbeat'})
    previous = None
    while True:
        current = state.read_text()
        if current != previous:
            emit({'type': 'job', 'job': job()})
            previous = current
        emit({'type': 'heartbeat'})
        time.sleep(0.1)
else:
    emit({'error': 'unexpected test worker command ' + command})
'''


def test_real_web_and_runtime_process_restarts(tmp_path):
    binary, auth = fake_codex(tmp_path / 'fake-codex')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'hosts': {'worker': {'ssh': None, 'python': sys.executable}}}))
    script = tmp_path / 'fake_fleetd.py'
    script.write_text(WORKER)
    status = tmp_path / 'host-status'
    status.write_text('running')
    starts = tmp_path / 'stream-starts'
    env = dict(os.environ, FLEET_CONFIG=str(config), FLEET_STORE=str(tmp_path / 'fleet.db'),
               FLEET_HOME=str(tmp_path / 'home'), FLEET_MANAGEMENT=str(tmp_path / 'management'),
               FLEET_REMOTE_HOME=str(tmp_path / 'worker-home'), FLEET_FLEETD_PATH=str(script),
               TEST_WORKER_STATUS=str(status), TEST_WORKER_STARTS=str(starts))
    env.update(CODEX_HOME=str(auth.parent), PATH=str(binary.parent) + os.pathsep + os.environ['PATH'])
    env.pop('FLEET_JOB_ID', None)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    url = f'http://127.0.0.1:{port}/api/state'
    processes, logs = [], []

    def launch(name, arguments):
        log = open(tmp_path / (name + '.log'), 'a')
        logs.append(log)
        process = subprocess.Popen([sys.executable, '-m', 'fleet_cli.cli', *arguments],
                                   env=env, stdout=log, stderr=log)
        processes.append(process)
        return process

    def read():
        try:
            with urlopen(url, timeout=1) as response:
                return json.load(response)
        except OSError:
            return None

    def ready():
        value = read()
        return value if value and value['runtime']['healthy'] and value['hosts'][0]['jobs'] else None

    def stop(process):
        process.terminate()
        process.wait(timeout=8)

    try:
        runtime = launch('runtime', ['serve'])
        web = launch('web', ['web', '--port', str(port)])
        first = eventually(ready, timeout=10)
        assert first['runtime']['health']['pid'] == runtime.pid
        stop(web)
        assert runtime.poll() is None
        next_status = status.with_suffix('.tmp')
        next_status.write_text('done')
        next_status.replace(status)
        reader = Container()
        reader.settings.override(dict(reader.settings(), store_path=tmp_path / 'fleet.db'))
        eventually(lambda: any(run.status == 'succeeded' for run in reader.execution().runs()))
        web = launch('web-restarted', ['web', '--port', str(port)])
        value = eventually(ready, timeout=10)
        assert value['hosts'][0]['jobs'][0]['status'] == 'done'
        assert value['runtime']['generation'] == first['runtime']['generation']
        assert len(starts.read_text().splitlines()) == 1
        stop(runtime)
        eventually(lambda: (value := read()) and not value['runtime']['available'])
        assert web.poll() is None
        runtime = launch('runtime-restarted', ['serve'])
        value = eventually(ready, timeout=10)
        assert value['runtime']['generation'] != first['runtime']['generation']
        assert value['runtime']['connection'] == 'reconnected'
        assert len(starts.read_text().splitlines()) == 2
    finally:
        for process in processes:
            if process.poll() is None:
                stop(process)
        for log in logs:
            log.close()
