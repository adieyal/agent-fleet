"""Local, full-snapshot runtime read contract; no command acceptance."""
import hashlib
import json
import os
from pathlib import Path
import time
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from urllib.parse import parse_qs, urlsplit
from concurrent.futures import Future, TimeoutError

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
        self.cached_job_details = {}
        self.builder = None
        self.pending = None
        self.pending_lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/health':
                    self.reply(owner.health())
                elif self.path == '/snapshot':
                    self.reply(owner.snapshot_bytes()[1])
                elif self.path.startswith('/job-detail?'):
                    query = parse_qs(urlsplit(self.path).query)
                    try:
                        self.reply(owner.job_detail(query['host'][0], query['job'][0]))
                    except KeyError:
                        self.send_error(404)
                elif self.path.startswith('/job-documents?'):
                    query = parse_qs(urlsplit(self.path).query)
                    try:
                        self.reply(owner.job_documents(query['host'][0], query['job'][0]))
                    except KeyError:
                        self.send_error(404)
                elif self.path == '/subscribe':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    try:
                        while not runtime.stop.is_set():
                            future = owner.subscription_snapshot()
                            try:
                                value, body = future.result(timeout=1)
                            except TimeoutError:
                                self.wfile.write(b': rebuilding snapshot\n\n')
                                self.wfile.flush()
                                continue
                            cursor = (value['sequence'], value['pipeline_sequence'],
                                      json.dumps({key: value['health'][key] for key in ('healthy', 'workers', 'hosts')}, sort_keys=True))
                            if cursor != getattr(self, 'cursor', None):
                                self.wfile.write(b'event: snapshot\ndata: ' + body + b'\n\n')
                                self.cursor = cursor
                            else:
                                self.wfile.write(b': heartbeat\n\n')
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

    def subscription_snapshot(self):
        # One shared in-flight builder; slow clients never queue more generations.
        with self.pending_lock:
            if self.pending is None or self.pending.done():
                future = self.pending = Future()
                def build():
                    try:
                        future.set_result(self.snapshot_bytes())
                    except Exception as error:
                        future.set_exception(error)
                self.builder = threading.Thread(target=build, name='runtime-snapshot', daemon=True)
                self.builder.start()
            return self.pending

    def job_detail(self, host, job):
        self._snapshot()
        return self.cached_job_details[host, job]

    def job_documents(self, host, job):
        with self.state.changed:
            return [dict(doc) for doc in self.state.by_host[host]['jobs'][job].get('documents', [])]

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
                full_document = view.document()
                details = {(host['name'], job['id']): job for host in full_document.get('hosts', [])
                           for job in host.get('jobs', [])}
                document = compact_document(full_document)
                self.cached_job_details = details
                self.state.accept_snapshot_view(view)
                cached = {'contract_version': 1, 'generation': self.generation,
                          'sequence': key[0], 'pipeline_sequence': key[1],
                          'document': document,
                          'pipelines': view.pipeline_updates(-1)}
                self.cached_body = json.dumps(cached, separators=(',', ':')).encode()[:-1]
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
        if self.builder is not None:
            self.builder.join(timeout=1)
        self.http.server_close()
        self.path.unlink(missing_ok=True)


def compact_document(document):
    """Keep the latest document card per job; full listings are read on demand."""
    hosts = []
    for host in document.get('hosts', []):
        jobs = []
        for job in host.get('jobs', []):
            documents = job.get('documents', [])
            preview = sorted(documents, key=lambda doc: doc.get('mtime') or 0)[-1:]
            summary = {**job, 'documents': [{key: value for key, value in doc.items()
                                              if key in ('id', 'name', 'kind', 'mtime', 'size', 'step', 'media')}
                                             for doc in preview],
                         'details_revision': hashlib.blake2b(json.dumps(job, sort_keys=True).encode(), digest_size=16).hexdigest(),
                         'details_truncated': True,
                         'decisions_count': len(job.get('decisions_since_dispatch', [])),
                         'decisions_since_dispatch': [],
                         'steps': [compact_step(step) for step in job.get('steps', [])],
                         'documents_count': len(documents), 'documents_truncated': len(documents) > 1}
            # Empty optional collections and false preview flags have the same
            # meaning when absent; the deck already uses optional reads.
            for field in ('documents', 'documents_count', 'documents_truncated',
                          'decisions_count', 'decisions_since_dispatch'):
                if not summary.get(field):
                    summary.pop(field, None)
            jobs.append(summary)
        hosts.append({**host, 'jobs': jobs})
    attention = []
    for item in document.get('attention', []):
        context = item.get('context_reference') or ''
        summary = {**{key: value for key, value in item.items() if value is not None},
                   'context_reference': context[:80], 'context_truncated': len(context) > 80}
        for field in ('blocked', 'stale', 'refusals', 'questions', 'context_truncated'):
            if not summary.get(field):
                summary.pop(field, None)
        attention.append(summary)
    return {**document, 'hosts': hosts, 'attention': attention}


def compact_step(step):
    result = {key: value for key, value in step.items()
              if key in ('index', 'title', 'status', 'started_at', 'finished_at', 'work_item', 'result')}
    for field, limit in (('title', 64), ('result', 400)):
        if isinstance(result.get(field), str):
            result[field] = result[field][:limit]
    return result
