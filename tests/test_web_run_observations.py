import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import urlopen

from fleet import composition
from fleet.transport import Host
from fleet.web import server


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
    process = SimpleNamespace(stdout=object(), stderr=object(), poll=lambda: None, kill=lambda: None)
    selector = SimpleNamespace(register=lambda *args: None, close=lambda: None,
                               select=lambda *, timeout: waits.append(timeout) or [])
    monkeypatch.setattr(server.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(server.transport, "ensure_master", lambda host: None)
    monkeypatch.setattr(server.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or process)

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
        assert document["hosts"][0]["ok"] is False
        assert document["hosts"][0]["error"] == "no heartbeat for 20s"
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
