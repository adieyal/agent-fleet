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
