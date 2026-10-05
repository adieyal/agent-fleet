"""Local, full-snapshot runtime read contract; no command acceptance."""
import json
import os
from pathlib import Path
import time
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
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/health':
                    self.reply(owner.health())
                elif self.path == '/snapshot':
                    self.reply(owner.snapshot())
                elif self.path == '/subscribe':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    try:
                        while not runtime.stop.is_set():
                            value = owner.snapshot()
                            self.wfile.write(('event: snapshot\ndata: ' + json.dumps(value) + '\n\n').encode())
                            self.wfile.flush()
                            state.wait_for_change(value['sequence'], 1, value['pipeline_sequence'])
                    except (OSError, ConnectionError):
                        pass
                else:
                    self.send_error(404)

            def reply(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.http.daemon_threads = True
        self.path = endpoint_path(state.container.settings()['store_path'])
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'port': self.http.server_port, 'generation': self.generation}))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def health(self):
        with self.state.changed:
            hosts = {name: {'ok': entry['ok'], 'error': entry['error'],
                            'down_since': entry.get('down_since')}
                     for name, entry in self.state.by_host.items()}
        return {'generation': self.generation, 'pid': os.getpid(),
                'uptime_seconds': time.monotonic() - self.started,
                **self.runtime.health(), 'hosts': hosts}

    def snapshot(self):
        with self.state.changed:
            document = self.state.document()
            pipelines = self.state.pipeline_updates(-1)
            return {'contract_version': 1, 'generation': self.generation,
                    'sequence': self.state.version, 'pipeline_sequence': self.state.pipeline_seq,
                    'observed_at': time.time(), 'health': self.health(),
                    'document': document, 'pipelines': pipelines}

    def close(self):
        self.http.server_close()
        self.path.unlink(missing_ok=True)
