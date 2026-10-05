"""Runtime read subscriber with controller-local command facades and retained reads."""
import json
import threading
import time
from urllib.request import urlopen

from fleet.errors import FleetError
from fleet.services.live import FleetState, snapshot
from fleet.services.runtime import endpoint_path


class SubscribedState(FleetState):
    is_runtime_subscriber = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._snapshot = None
        self._lagging = False
        self._cursor = None
        self._available = False
        self._error = 'fleet serve is not connected'
        self._disconnected_at = time.time()
        self._had_disconnect = False
        self._reconnected = False
        self._stop_subscription = threading.Event()
        self._subscriber = threading.Thread(target=self._follow_runtime, name='runtime-subscriber', daemon=True)

    @property
    def subscription_closed(self):
        return self._stop_subscription.is_set()

    def start(self):
        self._subscriber.start()

    def close(self):
        self._stop_subscription.set()
        self.bump()
        self._subscriber.join(timeout=3)

    def follow_history(self, stop):
        raise FleetError('subscriber cannot own history or scheduling; start fleet serve')

    def update(self, *args, **kwargs):
        raise FleetError('subscriber cannot ingest host observations; start fleet serve')

    def move_on_host(self, host_name, identity, label):
        host = next(host for host in self.hosts if host.name == host_name)
        self.transport.call(host, ['mv', identity, label], timeout=30)
        # The sole runtime follows the resulting observation; no optimistic local ingestion.

    def job_detail(self, host, job):
        if host not in self.host_names():
            raise KeyError(host)
        from urllib.parse import urlencode
        endpoint = json.loads(endpoint_path(self.container.settings()['store_path']).read_text())
        query = urlencode({'host': host, 'job': job})
        with urlopen(f"http://127.0.0.1:{endpoint['port']}/job-detail?{query}", timeout=15) as response:
            return json.load(response)

    def job_documents(self, host, job):
        if host not in self.host_names():
            raise KeyError(host)
        from urllib.parse import urlencode
        endpoint = json.loads(endpoint_path(self.container.settings()['store_path']).read_text())
        query = urlencode({'host': host, 'job': job})
        with urlopen(f"http://127.0.0.1:{endpoint['port']}/job-documents?{query}", timeout=15) as response:
            return json.load(response)

    def _follow_runtime(self):
        while not self._stop_subscription.is_set():
            try:
                path = endpoint_path(self.container.settings()['store_path'])
                endpoint = json.loads(path.read_text())
                # A per-read timeout: a busy runtime rebuilding a large snapshot can go quiet for seconds; a stopped
                # one closes the connection at once.
                with urlopen(f"http://127.0.0.1:{endpoint['port']}/subscribe", timeout=15) as response:
                    while not self._stop_subscription.is_set():
                        line = response.readline()
                        if not line:
                            raise OSError('runtime subscription ended')
                        if line.startswith(b': '):
                            with self.changed:
                                lagging = line.startswith(b': rebuilding')
                                if lagging != self._lagging:
                                    self._lagging = lagging
                                    self.bump()
                        if line.startswith(b'data: '):
                            self._accept(json.loads(line[6:]), endpoint['generation'])
            except (OSError, ValueError, KeyError, TypeError, FleetError) as error:
                self._unavailable(str(error))
                self._stop_subscription.wait(0.25)

    def _accept(self, value, generation):
        if value['contract_version'] != 1 or value['generation'] != generation:
            raise ValueError('runtime contract or generation mismatch')
        if not isinstance(value['document']['hosts'], list) or not isinstance(value['pipelines'], list):
            raise ValueError('invalid runtime snapshot')
        if not all(type(value[key]) is int and value[key] >= 0 for key in ('sequence', 'pipeline_sequence')):
            raise ValueError('invalid runtime counters')
        health = value['health']
        cursor = (generation, value['sequence'], value['pipeline_sequence'],
                  health['healthy'], json.dumps(health['workers'], sort_keys=True))
        by_host = {host['name']: {**snapshot(host), **{
            kind: {item['id']: snapshot(item) for item in host.get(kind, [])}
            for kind in ('jobs', 'sessions')}} for host in value['document']['hosts']}
        with self.changed:
            if not health['healthy'] and (self._snapshot is None or self._snapshot['health']['healthy']):
                self._disconnected_at = time.time()
            changed = not self._available or cursor != self._cursor
            if not self._available:
                self._reconnected = self._had_disconnect
            self._snapshot, self._cursor = value, cursor
            self._available, self._error = True, None
            self._lagging = False
            self.by_host = by_host
            self.refresh_registry()
            if changed:
                self.bump()

    def _unavailable(self, error):
        with self.changed:
            changed = self._available or self._error != error
            if self._available:
                self._disconnected_at = time.time()
            self._available, self._error = False, error
            self._had_disconnect = True
            if changed:
                self.bump()

    def runtime_status(self):
        with self.changed:
            health = self._snapshot['health'] if self._snapshot else None
            return {'available': self._available, 'healthy': bool(self._available and health['healthy']),
                    'connection': 'lagging' if self._lagging else 'reconnected' if self._reconnected else 'connected',
                    'generation': self._snapshot['generation'] if self._snapshot else None,
                    'sequence': self._snapshot['sequence'] if self._snapshot else None,
                    'pipeline_sequence': self._snapshot['pipeline_sequence'] if self._snapshot else None,
                    'last_snapshot_at': self._snapshot['observed_at'] if self._snapshot else None,
                    'error': self._error, 'start_command': 'fleet serve', 'health': snapshot(health)}

    def document(self):
        with self.changed:
            document = snapshot(self._snapshot['document']) if self._snapshot else {
                'hosts': [], 'projects': [], 'attention': [], 'pipelines': [], 'project_labels': {}}
            status = self.runtime_status()
            if not status['healthy']:
                reason = 'runtime unavailable' if not status['available'] else 'runtime worker failed'
                for host in document['hosts']:
                    for kind in ('jobs', 'sessions'):
                        for item in host.get(kind, []):
                            item.update(stale=True, stale_reason=reason, stale_since=self._disconnected_at)
            return {**document, 'runtime': status}

    def live_jobs(self):
        document = self.document()
        return {(host['name'], job['id']): job for host in document['hosts'] for job in host.get('jobs', [])}

    def stored_jobs(self, project_id, hosts):
        jobs = super().stored_jobs(project_id, hosts)
        if not self.runtime_status()['healthy']:
            for job in jobs:
                job['availability'] = 'runtime unavailable; host status unknown'
        return jobs

    def pipeline_updates(self, after):
        # Web receives full snapshots, including pipeline-only changes.
        return []
