"""Independent runtime and web listeners over temporary paths and a scripted host."""
import json
import queue
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from dependency_injector import providers
from fleet.container import Container
from fleet.infrastructure.codex.app_server import CodexAppServer
from fleet.transport import Host
from fleet_web.server import make_handler

from tests.responder_support import fake_codex


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError('condition did not become true')


class RuntimeDeck:
    def __init__(self, monkeypatch):
        self.container = Container()
        binary, auth = fake_codex(Path(self.container.settings()['home']) / 'fake-codex')
        self.container.responder_server.override(providers.Factory(
            CodexAppServer, fleet_home=self.container.settings()['home'], binary=str(binary), auth=auth))
        self.host = Host('worker', None)
        self.events = queue.Queue()
        self.follow_count = 0
        self.runtime = self.endpoint = self.runtime_thread = None
        self.web = self.web_thread = self.subscriber = None

        def follow(host, receive, *, stop, **kwargs):
            self.follow_count += 1
            receive({'type': 'hello'})
            receive({'type': 'heartbeat'})
            while not stop.is_set():
                try:
                    event = self.events.get(timeout=0.025)
                except queue.Empty:
                    continue
                if isinstance(event, Exception):
                    raise event
                receive(event)
            return 'stream cancelled'

        monkeypatch.setattr(self.container.transport(), 'follow_stream', follow)

    def start_runtime(self):
        self.runtime_state = self.container.live_state(hosts=[self.host])
        self.runtime = self.container.start_live(state=self.runtime_state)
        self.endpoint = self.container.runtime_server(state=self.runtime_state, runtime=self.runtime)
        self.runtime_thread = threading.Thread(target=self.endpoint.http.serve_forever,
                                               kwargs={'poll_interval': 0.01})
        self.runtime_thread.start()

    def stop_runtime(self):
        if self.runtime is not None:
            self.runtime.stop.set()
            self.endpoint.http.shutdown()
            self.runtime_thread.join(2)
            self.endpoint.close()
            self.runtime.close()
            self.runtime = None

    def start_web(self):
        self.subscriber = self.container.subscribed_state(hosts=[self.host])
        self.subscriber.start()
        self.web = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.subscriber))
        self.web.daemon_threads = True
        self.web_thread = threading.Thread(target=self.web.serve_forever, kwargs={'poll_interval': 0.01})
        self.web_thread.start()
        self.url = f'http://127.0.0.1:{self.web.server_port}'
        return self.url

    def stop_web(self):
        if self.web is not None:
            self.web.shutdown()
            self.web_thread.join(2)
            self.web.server_close()
            self.subscriber.close()
            self.web = None

    def read(self, path='/api/state'):
        with urlopen(self.url + path, timeout=3) as response:
            return json.load(response)

    def job(self, status='running'):
        self.events.put({'type': 'job', 'job': {
            'id': 'observed-job', 'project': 'p', 'description': 'Observed across web restart',
            'status': status, 'agent': 'codex', 'created_at': time.time(),
            'updated_at': time.time(), 'steps': [], 'documents': []}})

    def close(self):
        self.stop_web()
        self.stop_runtime()
