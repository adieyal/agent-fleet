import json
from urllib.request import Request, urlopen

import pytest

from tests.pages_fixture import seed_page
from tests.runtime_support import RuntimeDeck, eventually


@pytest.fixture
def runtime_deck(monkeypatch):
    deck = RuntimeDeck(monkeypatch)
    try:
        yield deck
    finally:
        deck.close()


def test_web_restart_leaves_one_follower_ingesting(runtime_deck):
    deck = runtime_deck
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'])
    first_generation = deck.read()['runtime']['generation']
    deck.job()
    eventually(lambda: deck.container.execution().runs())
    deck.stop_web()
    assert deck.runtime.health()['healthy']
    deck.job('done')
    eventually(lambda: any(run.status == 'succeeded' for run in deck.container.execution().runs()))
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'])
    value = deck.read()
    assert value['runtime']['generation'] == first_generation
    assert value['hosts'][0]['jobs'][0]['status'] == 'done'
    assert deck.follow_count == 1


def test_unavailable_then_generation_restart_reconnects_without_counter_comparison(runtime_deck):
    deck = runtime_deck
    deck.start_web()
    initial = deck.read()
    assert initial['runtime']['available'] is False
    assert initial['hosts'] == []
    assert initial['runtime']['start_command'] == 'fleet serve'
    deck.start_runtime()
    eventually(lambda: deck.read()['runtime']['healthy'])
    deck.job()
    eventually(lambda: deck.read()['hosts'] and deck.read()['hosts'][0]['jobs'])
    for _ in range(50):
        deck.runtime_state.bump()
    eventually(lambda: deck.read()['runtime']['sequence'] >= 50)
    old = deck.read()
    local_version = deck.subscriber.version
    deck.stop_runtime()
    eventually(lambda: not deck.read()['runtime']['available'])
    stale = deck.read()
    assert stale['hosts'][0]['jobs'][0]['stale']
    assert stale['hosts'][0]['jobs'][0]['stale_reason'] == 'runtime unavailable'
    deck.start_runtime()
    eventually(lambda: deck.read()['runtime']['healthy'])
    value = deck.read()
    assert value['runtime']['generation'] != old['runtime']['generation']
    assert value['runtime']['sequence'] < old['runtime']['sequence']
    assert value['runtime']['connection'] == 'reconnected'
    assert deck.subscriber.version > local_version


def test_offline_host_and_failed_runtime_worker_are_distinct(runtime_deck):
    deck = runtime_deck
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'])
    deck.events.put({'type': 'error', 'error': 'host network offline'})
    eventually(lambda: not deck.read()['hosts'][0]['ok'])
    offline = deck.read()
    assert offline['runtime']['healthy']
    assert offline['hosts'][0]['error'] == 'host network offline'
    deck.events.put(RuntimeError('follower broke'))
    eventually(lambda: not deck.read()['runtime']['healthy'])
    failed = deck.read()['runtime']
    assert failed['available']
    assert 'follower broke' in failed['health']['workers']['host:worker']['error']


def test_external_page_change_and_pipeline_only_change_wake_subscriber(runtime_deck):
    deck = runtime_deck
    ids = seed_page(deck.container)
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'])
    before = deck.subscriber.version
    # Shared-store change, not an optimistic web-state bump.
    deck.container.work().set(ids['work'], actor='test', next_step='Changed outside web')
    eventually(lambda: deck.subscriber.version > before)
    page = deck.read(f'/api/pages/{ids["project"]}/supplier-migration')
    assert 'Changed outside web' in json.dumps(page['directive_html'])
    assert page['runtime']['healthy']
    before = deck.subscriber.version
    deck.events.put({'type': 'pipeline', 'pipeline': 'build', 'run': {'status': 'running'}, 'baseline': None})
    eventually(lambda: deck.subscriber.version > before)
    assert deck.read()['pipelines'][0]['run']['status'] == 'running'


def test_subscriber_rejects_contract_mismatch_and_cannot_ingest(runtime_deck):
    from fleet.api import FleetError
    deck = runtime_deck
    deck.start_runtime()
    subscriber = deck.container.subscribed_state(hosts=[deck.host])
    value = deck.endpoint.snapshot()
    value['contract_version'] = 999
    with pytest.raises(ValueError, match='contract or generation mismatch'):
        subscriber._accept(value, value['generation'])
    with pytest.raises(FleetError, match='cannot ingest'):
        subscriber.update('worker', lambda _: None)
    with pytest.raises(FleetError, match='subscriber cannot own runtime'):
        deck.container.start_live(state=subscriber)
    with pytest.raises(FleetError, match='cannot own history'):
        subscriber.follow_history(None)


def test_local_web_command_wakes_authoritative_runtime(runtime_deck):
    deck = runtime_deck
    ids = seed_page(deck.container)
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'])
    before = deck.runtime_state.version
    request = Request(deck.url + '/api/focus',
                      data=json.dumps({'focus': 'background', 'projects': [ids['project']], 'labels': []}).encode(),
                      headers={'Content-Type': 'application/json', 'Origin': deck.url}, method='POST')
    with urlopen(request, timeout=3) as response:
        assert response.status == 200
    eventually(lambda: deck.runtime_state.version > before)
    eventually(lambda: deck.read()['building']['focus'][ids['project']] == 'background')
    assert deck.follow_count == 1


def test_slow_rebuild_keeps_subscription_connected_and_reports_lag(runtime_deck, monkeypatch):
    import threading
    import time
    deck = runtime_deck
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'])
    entered, release = threading.Event(), threading.Event()
    original = deck.endpoint._snapshot
    def slow():
        entered.set()
        assert release.wait(8)
        return original()
    monkeypatch.setattr(deck.endpoint, '_snapshot', slow)
    deck.runtime_state.bump()
    try:
        assert entered.wait(2)
        eventually(lambda: deck.read()['runtime']['connection'] == 'lagging')
        time.sleep(2.5)  # Exceeds the old socket read timeout.
        status = deck.read()['runtime']
        assert status['available'] and status['healthy']
        assert status['connection'] == 'lagging'
    finally:
        release.set()
    eventually(lambda: deck.read()['runtime']['connection'] != 'lagging')
    assert deck.read()['runtime']['healthy']


def test_document_listing_on_demand_preserves_all_documents(runtime_deck):
    from urllib.parse import urlencode
    deck = runtime_deck
    deck.start_runtime()
    deck.start_web()
    docs = [dict(id=f'outbox/{i}.md', name=f'{i}.md', kind='outbox', mtime=i,
                 size=10, path=f'/offline/{i}.md') for i in range(40)]
    deck.events.put({'type': 'job', 'job': dict(id='docs', project='p', description='Documents',
        status='done', agent='codex', created_at=0, updated_at=0, steps=[], documents=docs)})
    eventually(lambda: deck.read()['hosts'] and deck.read()['hosts'][0]['jobs'])
    job = deck.read()['hosts'][0]['jobs'][0]
    assert len(job['documents']) == 1
    assert job['documents_count'] == 40
    assert job['documents_truncated']
    assert deck.read('/api/job-documents?' + urlencode({'host': 'worker', 'job': 'docs'})) == docs
    assert 'path' not in job['documents'][0]
