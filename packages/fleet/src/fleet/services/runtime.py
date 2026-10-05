"""Local, full-snapshot runtime read contract; no command acceptance."""
import json
import os
from pathlib import Path
import time
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

from fleet.errors import FleetError


def endpoint_path(store):
    return Path(str(Path(store).resolve()) + '.runtime.json')


def runtime_status(store):
    try:
        endpoint = json.loads(endpoint_path(store).read_text())
        with urlopen(f"http://127.0.0.1:{endpoint['port']}/health", timeout=2) as response:
            result = json.load(response)
        if result['generation'] != endpoint['generation']:
            raise ValueError('runtime generation mismatch')
        return result
    except (OSError, ValueError, KeyError) as error:
        raise FleetError(f'runtime unavailable: {error}; start fleet serve') from error


class RuntimeServer:
    def __init__(self, state, runtime, port=0):
        self.state, self.runtime = state, runtime
        self.generation = str(uuid.uuid4())
        self.started = time.monotonic()
        self.build_lock = threading.Lock()
        self.cached_snapshot = None
        self.cached_body = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/health':
                    self.reply(owner.health())
                elif self.path == '/snapshot':
                    self.reply(owner.snapshot_bytes()[1])
                elif self.path == '/subscribe':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    try:
                        while not runtime.stop.is_set():
                            value, body = owner.snapshot_bytes()
                            self.wfile.write(b'event: snapshot\ndata: ' + body + b'\n\n')
                            self.wfile.flush()
                            state.wait_for_change(value['sequence'], 1, value['pipeline_sequence'])
                    except (OSError, ConnectionError):
                        pass
                else:
                    self.send_error(404)

            def reply(self, value):
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (OSError, ConnectionError):
                    pass

        self.http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.http.daemon_threads = True
        self.path = endpoint_path(state.container.settings()['store_path'])
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'port': self.http.server_port, 'generation': self.generation}))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def health(self):
        # Host entries are replaced atomically by ingestion. Health must never
        # acquire the projection/state lock, including during host catch-up.
        hosts = {name: {'ok': entry['ok'], 'error': entry['error'],
                        'down_since': entry.get('down_since')}
                 for name, entry in self.state.by_host.items()}
        return {'generation': self.generation, 'pid': os.getpid(),
                'uptime_seconds': time.monotonic() - self.started,
                **self.runtime.health(), 'hosts': hosts}

    def _snapshot(self):
        # Serialize builders, not health or ingestion. Every reader shares the
        # same document for a (state, pipeline) generation, including SSE polls.
        with self.build_lock:
            with self.state.changed:
                key = (self.state.version, self.state.pipeline_seq)
                cached = self.cached_snapshot
                if cached is None or key != (cached['sequence'], cached['pipeline_sequence']):
                    view = self.state.snapshot_view()
                else:
                    view = None
            if view is not None:
                document = view.document()
                self.state.accept_snapshot_view(view)
                cached = {'contract_version': 1, 'generation': self.generation,
                          'sequence': key[0], 'pipeline_sequence': key[1],
                          'document': document,
                          'pipelines': view.pipeline_updates(-1)}
                self.cached_body = json.dumps(cached).encode()[:-1]
                self.cached_snapshot = cached
            return cached, self.cached_body

    def snapshot(self):
        cached, _ = self._snapshot()
        # Worker health may change without a document generation changing.
        return {**cached, 'observed_at': time.time(), 'health': self.health()}

    def snapshot_bytes(self):
        cached, body = self._snapshot()
        health = self.health()
        observed_at = time.time()
        return ({**cached, 'observed_at': observed_at, 'health': health},
                body + b', "observed_at": ' + json.dumps(observed_at).encode()
                + b', "health": ' + json.dumps(health).encode() + b'}')

    def close(self):
        self.http.server_close()
        self.path.unlink(missing_ok=True)
