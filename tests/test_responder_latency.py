"""Opt-in end-to-end timings with a real model, actual commits and HTTP page reads."""
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from urllib.request import urlopen
from uuid import uuid4

import pytest
from dependency_injector import providers

from fleet.container import configured_container
from fleet.infrastructure.codex.app_server import CodexAppServer
from fleet.infrastructure.sqlite.store import UnitOfWork
from fleet.modules.records import TRIAGE_PATH
from fleet_web.server import make_handler
from tests.runtime_support import eventually
from tests.test_responder import LIVE_AUTH, LIVE_BINARY


@pytest.mark.skipif(os.environ.get('FLEET_LIVE_CODEX') != '1', reason='opt-in 20 real Codex turns')
def test_live_responder_latency_10_cold_10_warm(tmp_path, monkeypatch):
    assert LIVE_BINARY and LIVE_AUTH.is_file(), 'live Codex binary/auth missing'
    container = configured_container()
    services = container.services()
    project = container.initialized_workspace().edit_registry(lambda registry: registry.create('responder-latency')).id
    services.workspace.edit_registry(lambda registry: registry.link(project, 'carbon', 'latency'))
    source = '# Responder latency\n\nThe test word is pong.\n'
    page = services.records.write(project, 'pages/latency.md', source, key='latency-page', actor='latency-fixture')
    mandate = dict(goal='Answer document comments concisely. When asked the test word, reply exactly pong.',
        constraints=['Use only the supplied page context.'], escalation_conditions=[], criteria_it_may_judge=[],
        decision_authority=['reply_attention'], host='carbon', runtime='codex', cwd=str(tmp_path),
        permission='acceptEdits', routing={}, permissions={'allow': [], 'escalate': []},
        limits={'retries_per_step': 2, 'runs_per_day': 100, 'unclaimed_minutes': 30})
    services.records.write_mandate(project, TRIAGE_PATH, json.dumps(mandate), key='latency-mandate', actor='latency-fixture')
    server = CodexAppServer(Path(container.settings()['home']), binary=LIVE_BINARY, auth=LIVE_AUTH)
    container.responder_server.override(providers.Object(server))
    monkeypatch.setattr(container.transport(), 'host_by_name', lambda name: SimpleNamespace(name=name, is_local=True))
    rows, active = [], [None]
    lock = threading.RLock()
    original_record, original_exit = UnitOfWork.record_change, UnitOfWork.__exit__
    def record(unit, subject, previous, current, actor):
        original_record(unit, subject, previous, current, actor)
        if unit.store.path == services.store.path:
            unit._latency_changes = getattr(unit, '_latency_changes', []) + [(subject, current, actor)]
    def committed(unit, error_type, error, traceback):
        original_exit(unit, error_type, error, traceback)
        if error_type is not None:
            return
        now = time.monotonic()
        with lock:
            row = active[0]
            if row is None:
                return
            for subject, current, actor in getattr(unit, '_latency_changes', []):
                if subject.startswith('attention:') and actor == 'latency-user':
                    row.setdefault('comment_stored', now)
                    row.setdefault('item', subject.split(':')[1])
                if subject.startswith('execution:run:'):
                    run = json.loads(current)
                    if run.get('kind') == 'responder' and run['status'] == 'running':
                        row.setdefault('reserved', now)
                        row.setdefault('run', run['id'])
                if (subject.startswith('attention:') and ':reply:' in subject
                        and actor.startswith('triage:')):
                    row['reply_recorded'] = now
                    row['recorded_event'].set()
    monkeypatch.setattr(UnitOfWork, 'record_change', record)
    monkeypatch.setattr(UnitOfWork, '__exit__', committed)
    original_request, original_turn = server._request, server.turn
    def request(method, params, timeout):
        row = active[0]
        if method == 'turn/start':
            row['turn_start'] = time.monotonic()
            row['thread'] = params['threadId']
        result = original_request(method, params, timeout)
        if method == 'turn/start':
            row['accepted'] = time.monotonic()
        return result
    def turn(thread, prompt, **kwargs):
        row = active[0]
        delta, complete = kwargs.pop('on_delta', None), kwargs.pop('on_complete', None)
        def on_delta(text):
            row.setdefault('first_delta', time.monotonic())
            if delta:
                delta(text)
        def on_complete(result):
            row['model_completed'] = time.monotonic()
            row['usage'] = result.usage
            if complete:
                complete(result)
        return original_turn(thread, prompt, on_delta=on_delta, on_complete=on_complete, **kwargs)
    monkeypatch.setattr(server, '_request', request)
    monkeypatch.setattr(server, 'turn', turn)
    state = container.live_state(hosts=[])
    runtime = container.start_live(state=state)
    endpoint = container.runtime_server(state=state, runtime=runtime)
    endpoint_thread = threading.Thread(target=endpoint.http.serve_forever, kwargs={'poll_interval': .01})
    endpoint_thread.start()
    subscriber = container.subscribed_state(hosts=[])
    subscriber.start()
    web = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(subscriber))
    web.daemon_threads = True
    web_thread = threading.Thread(target=web.serve_forever, kwargs={'poll_interval': .01})
    web_thread.start()
    api = f'http://127.0.0.1:{web.server_port}/api/pages/{project}/latency'
    output = Path(os.environ.get('FLEET_RESPONDER_LATENCY_REPORT', tmp_path / 'latency.json'))
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'rows': rows,
              'definition': 'cold=new attention thread; warm=follow-up on that thread; one persistent serve app-server',
              'model_interval': 'turn acceptance observed to completion callback', 'target_non_model_s': .5}
    try:
        eventually(lambda: state.responder.health().get('ready'), timeout=30)
        eventually(lambda: subscriber.runtime_status()['healthy'])
        report.update(codex_version=server.version, startup_s=server.startup_s,
                      store=str(services.store.path), fleet_home=str(container.settings()['home']))
        for index in range(1, 11):
            item = None
            cold_thread = None
            for mode in ('cold', 'warm'):
                row = {'index': index, 'mode': mode, 'recorded_event': threading.Event()}
                with lock:
                    active[0] = row
                row['submitted'] = time.monotonic()
                if mode == 'cold':
                    result = container.page_change(project=project, slug='latency', operation='comment',
                        revision=page['revision'], comment_id=str(uuid4()), headline='What is the test word?',
                        body='What is the test word? Reply with only the word.', reason='Latency measurement requires an agent reply.',
                        actor='latency-user', owner='agent',
                        selector={'type': 'TextQuoteSelector', 'exact': 'The test word is pong.'})
                    item = result['id']
                else:
                    services.attention.reply(item, 'Again: what is the test word? Reply with only the word.', actor='latency-user')
                assert row['recorded_event'].wait(120), ('reply not recorded', index, mode, state.responder.health())
                with urlopen(api, timeout=15) as response:
                    view = json.load(response)
                row['page_visible'] = time.monotonic()
                thread = next(value for value in view['threads'] if value['id'] == item)
                reply = thread['replies'][-1]
                assert reply['actor'].startswith('triage:') and reply['body'].strip().lower() == 'pong', reply
                run = services.execution.get_run(row['run'])
                assert run.kind == 'responder' and run.status == 'succeeded' and run.usage
                if mode == 'cold':
                    cold_thread = row['thread']
                else:
                    assert row['thread'] == cold_thread, 'warm sample failed to reuse attention-item thread'
                assert row['submitted'] <= row['comment_stored'] <= row['turn_start'] <= row['accepted']
                assert row['accepted'] <= row['first_delta'] <= row['model_completed'] <= row['reply_recorded'] <= row['page_visible']
                row.pop('recorded_event')
                row['segments'] = dict(
                    submission=row['comment_stored'] - row['submitted'],
                    reservation=row['reserved'] - row['comment_stored'],
                    dispatch=row['turn_start'] - row['reserved'],
                    acceptance=row['accepted'] - row['turn_start'],
                    first_delta=row['first_delta'] - row['accepted'],
                    generation=row['model_completed'] - row['first_delta'],
                    recording=row['reply_recorded'] - row['model_completed'],
                    visibility=row['page_visible'] - row['reply_recorded'])
                row['model_s'] = row['model_completed'] - row['accepted']
                row['total_s'] = row['page_visible'] - row['comment_stored']
                row['non_model_s'] = row['total_s'] - row['model_s']
                rows.append(row)
                print('RESPONDER_LATENCY ' + json.dumps(row, sort_keys=True), flush=True)
                output.write_text(json.dumps(report, indent=2) + '\n')
                with lock:
                    active[0] = None
                eventually(lambda: item not in state.typing)
            services.attention.take(item, actor='latency-fixture', reason='Measured conversation complete')
        assert len({row['thread'] for row in rows if row['mode'] == 'cold'}) == 10
        assert services.triage_repository.get(project)['used'] == 20
        report['medians'] = {mode: {
            **{name: median(row['segments'][name] for row in rows if row['mode'] == mode)
               for name in rows[0]['segments']},
            **{name: median(row[name] for row in rows if row['mode'] == mode)
               for name in ('model_s', 'total_s', 'non_model_s')}} for mode in ('cold', 'warm')}
        report['target_met'] = {mode: report['medians'][mode]['non_model_s'] < .5 for mode in ('cold', 'warm')}
        report['over_target_samples'] = [dict(index=row['index'], mode=row['mode'], non_model_s=row['non_model_s'])
                                         for row in rows if row['non_model_s'] >= .5]
    finally:
        output.write_text(json.dumps(report, indent=2) + '\n')
        web.shutdown()
        web.server_close()
        web_thread.join(3)
        subscriber.close()
        runtime.stop.set()
        endpoint.http.shutdown()
        endpoint_thread.join(3)
        endpoint.close()
        runtime.close()
