import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import urlopen

from fleet import composition
from fleet.transport import Host
from fleet.web import server


def test_unscoped_dispatch_observations_keep_deck_available():
    store = composition.open_store()
    execution = composition.open_execution(store)
    run = execution.dispatch(None, project="p", host="worker", runtime="codex", payload={"cwd": "/repo"},
                             actor="user", reason="manual", idempotency_key="request").run
    host = Host("worker", None)
    state = server.FleetState([host], store=store)
    state.keeper.fetch = lambda *args: {"content": "Report"}
    message = {"type": "job", "job": {
        "id": run.remote_job_id, "project": "p", "description": "Task", "status": "done", "agent": "codex",
        "created_at": 1, "updated_at": 2, "steps": [],
        "documents": [{"id": "report", "kind": "report", "name": "Report", "path": "/repo/report.md"}]}}
    server.apply_message(state, host, {"type": "hello"})
    server.apply_message(state, host, message)
    assert state.keeper.settle(5)
    sequence = store.latest_sequence()
    server.apply_message(state, host, message)
    assert not [row for row in store.history_after(sequence) if row["subject"].startswith("execution:")]
    assert not execution.claims()[0].active
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(state))
    thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{http.server_port}/api/state", timeout=5) as response:
            document = json.load(response)
        assert document["hosts"][0]["jobs"][0]["id"] == run.remote_job_id
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def test_silence_deadline_marks_linked_run_unknown(monkeypatch):
    store = composition.open_store()
    work = composition.open_work(store).add(project="p", title="Task", goal="Ship", actor="user")
    execution = composition.open_execution(store)
    execution.link("worker", "job", work.id, actor="user")
    host = Host("worker", None)
    state = server.FleetState([host], store=store)
    server.apply_message(state, host, {"type": "hello"})
    server.apply_message(state, host, {"type": "job", "job": {
        "id": "job", "project": "p", "description": "Task", "status": "running", "agent": "codex",
        "created_at": 1, "updated_at": 2, "steps": [], "documents": []}})
    waits, commands = [], []
    process = SimpleNamespace(stdout=iter(()), stderr=iter(()), poll=lambda: None, kill=lambda: None)

    class Silent:   # the stream sends nothing before the deadline
        def put(self, line):
            pass

        def get(self, *, timeout):
            waits.append(timeout)
            raise server.transport.queue.Empty

    monkeypatch.setattr(server.transport.queue, "Queue", Silent)
    monkeypatch.setattr(server.transport, "ensure_master", lambda host: None)
    monkeypatch.setattr(server.transport.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or process)

    class Finished(Exception):
        pass

    def finish(seconds):
        raise Finished

    monkeypatch.setattr(server.time, "sleep", finish)
    try:
        server.follow_host(state, host)
    except Finished:
        pass
    assert waits == [server.STREAM_SILENCE_LIMIT]
    assert "--since-hours" not in commands[0]
    assert execution.runs()[0].status == "unknown outcome"
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(state))
    thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{http.server_port}/api/state", timeout=5) as response:
            document = json.load(response)
        assert document["hosts"][0]["jobs"][0]["id"] == "job"
        assert document["hosts"][0]["jobs"][0]["stale"] is True
        assert document["hosts"][0]["jobs"][0]["stale_reason"] == "no heartbeat for 20s"
        assert document["hosts"][0]["ok"] is False
        assert document["hosts"][0]["error"] == "no heartbeat for 20s"
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_batch12_reconnect_keeps_stale_work_until_complete_snapshot():
    host = Host('worker', None)
    state = server.FleetState([host], store=composition.open_store())
    server.apply_message(state, host, {'type': 'hello'})
    job = {'id': 'one', 'project': 'p', 'description': 'Work', 'agent': 'codex', 'status': 'running',
           'created_at': 1, 'updated_at': 2, 'steps': []}
    for id in ['one', 'two']:
        server.apply_message(state, host, {'type': 'job', 'job': {**job, 'id': id}})
    session = {'id': 's', 'project': 'p', 'agent': 'claude', 'status': 'idle', 'started_at': 1, 'activity': None}
    server.apply_message(state, host, {'type': 'session', 'session': session})
    server.apply_message(state, host, {'type': 'error', 'error': 'offline'})
    entry = state.document()['hosts'][0]
    assert len(entry['jobs']) == 2 and all(j['stale'] for j in entry['jobs'])
    assert entry['sessions'][0]['stale']
    server.apply_message(state, host, {'type': 'hello'})
    entry = state.document()['hosts'][0]
    assert len(entry['jobs']) == 2 and all(j['stale'] for j in entry['jobs'])
    server.apply_message(state, host, {'type': 'job', 'job': job})
    entry = state.document()['hosts'][0]
    assert {j['id']: j['stale'] for j in entry['jobs']} == {'one': False, 'two': True}
    server.apply_message(state, host, {'type': 'heartbeat'})
    entry = state.document()['hosts'][0]
    assert [j['id'] for j in entry['jobs']] == ['one'] and entry['jobs'][0]['stale'] is False
    assert entry['sessions'] == []
