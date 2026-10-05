from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.pages_fixture import seed_page
from tests.runtime_support import RuntimeDeck


@pytest.fixture
def runtime_deck(monkeypatch):
    deck = RuntimeDeck(monkeypatch)
    try:
        yield deck
    finally:
        deck.close()


def shoot(request, page, name):
    destination = request.config.getoption('--shots')
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / (name + '.png')), full_page=True)


@pytest.mark.browser
def test_unavailable_restart_reconnect_and_live_pages(page, runtime_deck, request):
    deck = runtime_deck
    ids = seed_page(deck.container)
    deck.start_web()
    page.set_viewport_size({'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(deck.url)
    banner = page.locator('#runtime-status')
    expect(banner).to_contain_text('Runtime unavailable — start fleet serve')
    expect(page.locator('#live')).to_contain_text('runtime unavailable')
    shoot(request, page, 'runtime-unavailable')
    records = page.context.new_page()
    # Prove page updates come through the subscription, not the reconciliation timer.
    records.add_init_script("""const interval = window.setInterval;
      window.setInterval = (fn, ms, ...args) => ms === 2000 ? 0 : interval(fn, ms, ...args);""")
    records.on('pageerror', lambda error: errors.append(str(error)))
    records.goto(f'{deck.url}/pages/{ids["project"]}/supplier-migration')
    expect(records.locator('#page-connection')).to_contain_text('Runtime unavailable — start fleet serve')
    deck.start_runtime()
    expect(banner).to_contain_text('Runtime reconnected — live updates resumed')
    expect(records.locator('#page-connection')).to_contain_text('Runtime reconnected')
    deck.container.work().set(ids['work'], actor='test', next_step='Live page changed through runtime history')
    expect(records.locator('#supplier-work')).to_contain_text('Live page changed through runtime history')
    deck.job()
    expect(page.locator('#tags .tag')).to_have_count(1)
    generation = deck.subscriber.runtime_status()['generation']
    deck.stop_runtime()
    expect(banner).to_contain_text('Runtime unavailable — start fleet serve')
    expect(records.locator('#page-connection')).to_contain_text('Runtime unavailable')
    # Stale work is kept visible until a new authoritative snapshot replaces it.
    expect(page.locator('#tags .tag')).to_have_count(1)
    shoot(request, page, 'runtime-lost-stale')
    deck.start_runtime()
    expect(banner).to_contain_text('Runtime reconnected — live updates resumed')
    assert deck.subscriber.runtime_status()['generation'] != generation
    deck.job()
    expect(page.locator('#tags .tag')).to_have_count(1)
    deck.container.work().set(ids['work'], actor='test', next_step='Live page changed after runtime restart')
    expect(records.locator('#supplier-work')).to_contain_text('Live page changed after runtime restart')
    expect(records.locator('#page-connection')).to_contain_text('Runtime reconnected')
    shoot(request, page, 'runtime-reconnected')
    shoot(request, records, 'live-page-reconnected')
    deck.events.put({'type': 'error', 'error': 'host network offline'})
    expect(page.locator('#legendBody')).to_contain_text('host network offline')
    expect(page.locator('#live')).not_to_contain_text('runtime unavailable')
    expect(banner).not_to_contain_text('Runtime worker failed')
    deck.events.put(RuntimeError('follower broke'))
    expect(banner).to_contain_text('Runtime worker failed — host:worker: RuntimeError: follower broke')
    expect(page.locator('#live')).to_contain_text('runtime worker failed')
    expect(records.locator('#page-connection')).to_contain_text('Runtime worker failed')
    records.close()
    assert errors == []


def test_complete_document_listing_and_lagging_indicator(page, runtime_deck, request, monkeypatch):
    import threading
    import time
    from tests.runtime_support import eventually
    deck = runtime_deck
    deck.container.initialized_workspace().edit_registry(lambda registry: registry.create('p'))
    deck.start_runtime()
    deck.start_web()
    docs = [dict(id=f'outbox/{i}.md', name=f'{i}.md', kind='outbox', mtime=i, size=10,
                 path=f'/offline/{i}.md') for i in range(40)]
    deck.events.put({'type': 'job', 'job': dict(id='docs', project='p', description='Forty documents',
        status='running', agent='codex', created_at=time.time(), updated_at=time.time(), steps=[], documents=docs)})
    eventually(lambda: deck.read()['hosts'] and deck.read()['hosts'][0]['jobs'], timeout=15)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(deck.url)
    page.locator('#tags .tag', has_text='docs').dispatch_event('click')
    page.locator('#panelTabs [data-tab="documents"]').click()
    expect(page.locator('#panelBody [data-doc]')).to_have_count(40)
    shoot(request, page, 'complete-document-list')
    entered, release = threading.Event(), threading.Event()
    original = deck.endpoint._snapshot
    def slow():
        entered.set()
        assert release.wait(10)
        return original()
    monkeypatch.setattr(deck.endpoint, '_snapshot', slow)
    deck.runtime_state.bump()
    try:
        assert entered.wait(3)
        expect(page.locator('#live')).to_contain_text('runtime lagging')
        shoot(request, page, 'runtime-lagging')
        assert deck.read()['runtime']['available']
    finally:
        release.set()
    expect(page.locator('#live')).not_to_contain_text('runtime lagging')
    deck.stop_runtime()
    expect(page.locator('#panelHead [data-stale]')).to_be_visible()
    expect(page.locator('#panelBody [data-doc]')).to_have_count(40)
    shoot(request, page, 'retained-details-stale')
    assert errors == []


def test_full_attention_context_is_loaded_on_demand(page, runtime_deck, request):
    from tests.runtime_support import eventually
    deck = runtime_deck
    project = deck.container.initialized_workspace().edit_registry(lambda registry: registry.create('p')).id
    context = 'Detailed evidence. ' * 100 + 'COMPLETE CONTEXT END'
    item = deck.container.attention().raise_item(project=project, kind='decision', owner='user',
        source='fixture', source_reference='long-context', headline='Read the complete evidence?',
        context_reference=context, actor='fixture', owner_reason='The user must choose the scope.')
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.read()['runtime']['healthy'], timeout=15)
    live = next(i for i in deck.read()['attention'] if i['id'] == item.id)
    assert live['context_truncated'] and len(live['context_reference']) == 80
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(deck.url)
    page.locator('#needYou').click()
    page.locator(f'[data-context="{item.id}"]').first.click()
    expect(page.locator('#rdBody .decision-context')).to_have_text(context)
    shoot(request, page, 'complete-attention-context')
    assert errors == []
