"""Bounded, host-free live-update replay against a read-only SQLite backup."""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import unquote, urlsplit


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ob', dir='/tmp/xx') as directory:
        root = Path(directory)
        source = sqlite3.connect(f'file:{Path.home()}/.config/fleet/fleet.db?mode=ro', uri=True)
        target = sqlite3.connect(root / 'fleet.db')
        source.backup(target)
        source.close()
        target.close()
        for key, value in {'FLEET_STORE': root / 'fleet.db', 'FLEET_CONFIG': root / 'config.json',
                           'FLEET_HOME': root / 'home', 'FLEET_MANAGEMENT': root / 'management'}.items():
            os.environ[key] = str(value)
        (root / 'config.json').write_text('{}')
        from fleet.container import configured_container
        from fleet.services.live import FleetState
        from fleet.transport import Host
        container = configured_container()
        state = FleetState([Host('carbon', None)], container=container)
        # Exercise ingestion and reconciliation, but never deliver to hosts or schedule agents.
        state.schedule_triage = lambda: None
        state.execution.retry_deliveries = lambda *args: None
        state.execution.retry_decisions = lambda *args: None
        jobs = {}
        status = {'succeeded': 'done', 'stopped': 'cancelled', 'unknown outcome': 'blocked'}
        entries = state.run_library.list()
        for run in state.execution.runs():
            if run.host != 'carbon' or run.kind == 'session':
                continue
            steps = state.execution.steps(run.id)
            documents = [{'kind': e.kind, 'name': e.title, 'path': unquote(urlsplit(e.canonical_location).path)}
                         for e in entries if e.run == run.id and e.canonical_location.startswith('fleet://carbon/')]
            jobs[run.remote_job_id] = {'id': run.remote_job_id, 'run_id': run.id,
                'project': run.label or '', 'agent': run.runtime, 'description': run.title,
                'cwd': run.cwd, 'workspace': run.workspace, 'workspace_reason': run.workspace_reason,
                'status': status.get(run.status, run.status),
                'updated_at': run.last_observed.timestamp() if run.last_observed else None,
                'steps': [{**s, 'started_at': None if s['start'] is None else datetime.fromisoformat(s['start']).timestamp(),
                           'finished_at': None if s['end'] is None else datetime.fromisoformat(s['end']).timestamp()} for s in steps],
                'documents': documents}
        while len(jobs) < 212:
            identity = f'bench-{len(jobs)}'
            jobs[identity] = {'id': identity, 'run_id': identity, 'project': '', 'agent': 'codex',
                'status': 'done', 'updated_at': 20, 'steps': [{'index': 0, 'status': 'done', 'started_at': 10, 'finished_at': 20}],
                'documents': []}
        state.update('carbon', lambda h: h.update(ok=True, error=None, jobs=jobs), heartbeat=True)
        wall, cpu = time.perf_counter(), time.process_time()
        reports = 5
        interval = float(os.environ.get('BENCH_INTERVAL', '0'))
        changed = os.environ.get('BENCH_CHANGE') == '1'
        identity = next(key for key, job in jobs.items() if job['status'] == 'done')
        for index in range(reports):
            def report(entry):
                if changed:
                    entry['jobs'][identity]['updated_at'] = (jobs[identity]['updated_at'] or 0) + index + 1
            state.update('carbon', report, heartbeat=not changed,
                         subjects={f'job:carbon:{identity}'} if changed else None)
            if interval:
                time.sleep(interval)
        cpu = time.process_time() - cpu
        wall = time.perf_counter() - wall
        print(json.dumps({'jobs': len(jobs), 'reports': reports, 'changed_jobs_per_report': int(changed), 'cpu_seconds_per_report': cpu / reports,
                          'wall_seconds_per_report': wall / reports, 'core_percent_at_default_5_second_heartbeat': cpu / reports / 5 * 100,
                          'paced_core_percent': cpu / wall * 100 if interval else None}))


if __name__ == '__main__':
    main()
