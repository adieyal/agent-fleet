import gc
import subprocess
import threading
import time
import weakref

from fleet.container import Container, bound_services
from fleet.services.live import start_live
from fleet.services.runtime import RuntimeServer
from tests.test_serve_runtime import runtime_state, state_at


def test_completed_transaction_scopes_are_collectible(tmp_path):
    container = Container()
    container.settings.override(dict(container.settings(), store_path=tmp_path / 'scope.db',
                                     home=tmp_path / 'home', management_home=tmp_path / 'management'))
    refs = []
    for _ in range(12):
        with container.store().unit_of_work() as unit:
            bound_services(container, unit)
            refs.append(weakref.ref(unit))
    del unit
    gc.collect()
    assert not container.store.overrides
    assert all(ref() is None for ref in refs)


def test_transient_worker_timeout_recovers(tmp_path):
    attempts = []
    recovered = threading.Event()
    def history(stop):
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            raise subprocess.TimeoutExpired(['git', 'show'], 10)
        state._runtime_worker_success("history-scheduler")
        recovered.set()
        stop.wait()
    state = state_at(tmp_path / 'worker.db', history)
    runtime = start_live(state)
    try:
        assert recovered.wait(3)
        worker = runtime.health()['workers']['history-scheduler']
        assert runtime.health()['healthy']
        assert worker['restarts'] == 1
        assert 'TimeoutExpired' in worker['last_error']
        assert worker['recovered_at'] is not None
        assert attempts[1] - attempts[0] >= 0.2
    finally:
        runtime.close()


def test_live_document_preview_is_bounded_and_detail_remains_available(tmp_path):
    state = runtime_state(tmp_path)
    docs = [dict(id=str(i), name='doc', kind='file', path='/bulk/' + 'x' * 300,
                 mtime=i, size=20) for i in range(1000)]
    job = dict(id='job', documents=docs)
    state.by_host['host']['jobs'] = {'job': job}
    state.document = lambda: {'hosts': [dict(name='host', jobs=[job], sessions=[])]}
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    try:
        value, body = server.snapshot_bytes()
        summary = value['document']['hosts'][0]['jobs'][0]
        assert len(body) < 10000
        assert len(summary['documents']) <= 4
        assert summary['documents_count'] == 1000
        assert summary['documents_truncated']
        assert 'path' not in summary['documents'][0]
        assert server.job_documents('host', 'job') == docs
        assert len(job['documents']) == 1000
    finally:
        runtime.close()
        server.close()


def test_repeated_worker_failures_back_off_and_close_cancels_retry(tmp_path):
    attempts = []
    def history(stop):
        attempts.append(time.monotonic())
        raise RuntimeError('persistent failure')
    runtime = start_live(state_at(tmp_path / 'backoff.db', history))
    try:
        deadline = time.monotonic() + 3
        while len(attempts) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(attempts) == 3
        assert attempts[1] - attempts[0] >= .2
        assert attempts[2] - attempts[1] >= .45
        assert not runtime.health()['healthy']
    finally:
        before = time.monotonic()
        runtime.close()
        assert time.monotonic() - before < .5
        assert not any(t.is_alive() for t in runtime.threads)


def test_live_details_preserve_audit_data_on_demand(tmp_path):
    state = runtime_state(tmp_path)
    job = dict(id='job', documents=[], decisions_since_dispatch=[dict(question='Q', answer='A' * 5000)],
               steps=[dict(index=0, title='Title ' * 100, status='done', result='Result ' * 1000,
                           git={'commits': ['commit']}, started_at=1, finished_at=2)])
    state.by_host['host']['jobs'] = {'job': job}
    state.document = lambda: {'hosts': [dict(name='host', jobs=[job], sessions=[])]}
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    try:
        live = server.snapshot()['document']['hosts'][0]['jobs'][0]
        assert live['details_truncated']
        assert live['decisions_count'] == 1
        assert not live.get('decisions_since_dispatch')
        assert len(live['steps'][0]['title']) == 64
        assert 'git' not in live['steps'][0]
        assert server.job_detail('host', 'job') == job
        revision = live['details_revision']
        job['decisions_since_dispatch'][0]['answer'] = 'changed'
        state.version += 1
        assert server.snapshot()['document']['hosts'][0]['jobs'][0]['details_revision'] != revision
        assert server.job_detail('host', 'job')['decisions_since_dispatch'][0]['answer'] == 'changed'
    finally:
        runtime.close()
        server.close()


def test_long_attention_context_has_an_explicit_preview():
    from fleet.services.runtime import compact_document
    context = 'Scope evidence. ' * 500
    result = compact_document({'hosts': [], 'attention': [dict(id='item', context_reference=context)]})
    item = result['attention'][0]
    assert item['context_truncated']
    assert item['context_reference'] == context[:80]


def test_runtime_close_does_not_wait_indefinitely_for_snapshot_builder(tmp_path):
    state = runtime_state(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def document():
        entered.set()
        release.wait(5)
        return {'hosts': []}
    state.document = document
    runtime = start_live(state)
    server = RuntimeServer(state, runtime)
    future = server.subscription_snapshot()
    assert entered.wait(1)
    try:
        runtime.close()
        before = time.monotonic()
        server.close()
        assert time.monotonic() - before < 1.5
        assert server.builder.daemon
    finally:
        release.set()
        assert future.result(timeout=2)[0]['document']['hosts'] == []


def test_step_preview_preserves_missing_values():
    from fleet.services.runtime import compact_step
    assert compact_step({'title': None, 'result': None}) == {'title': None, 'result': None}
