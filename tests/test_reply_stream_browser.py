# ruff: noqa: F811
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from dependency_injector import providers
from playwright.sync_api import expect

from fleet_web.server import make_handler
from tests.integration.test_page_responder import responder
from tests.integration.test_triage_commands import triage  # noqa: F401
from tests.runtime_support import eventually
from tests.test_web_subscription_browser import shoot


@pytest.fixture
def streaming_page(triage, monkeypatch):
    container, attention, _, _, _, fake = responder(triage)
    release = threading.Event()
    original = fake.turn
    fake.response['reply'] = 'Checking <script> safely. All active suppliers match.'
    def turn(*args, on_delta, **kwargs):
        on_delta('{"reply":"Checking <script> safely.')
        assert release.wait(15), 'test did not release scripted responder'
        return original(*args, **kwargs)
    fake.turn = turn
    fake.start = lambda: None
    fake.close = lambda: release.set()
    fake.health = lambda: {'ready': True, 'alive': True, 'pid': None, 'error': None}
    container.responder_server.override(providers.Object(fake))
    monkeypatch.setattr(container.transport(), 'host_by_name', lambda name: SimpleNamespace(name=name, is_local=True))
    state = container.live_state(hosts=[])
    runtime = container.start_live(state=state)
    endpoint = container.runtime_server(state=state, runtime=runtime)
    runtime_thread = threading.Thread(target=endpoint.http.serve_forever, kwargs={'poll_interval': .01})
    runtime_thread.start()
    subscriber = container.subscribed_state(hosts=[])
    subscriber.start()
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(subscriber))
    server.daemon_threads = True
    web_thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
    web_thread.start()
    eventually(lambda: subscriber.typing)
    try:
        yield container, attention, release, f'http://127.0.0.1:{server.server_port}', fake
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        web_thread.join(3)
        subscriber.close()
        runtime.stop.set()
        endpoint.http.shutdown()
        runtime_thread.join(3)
        endpoint.close()
        runtime.close()


def test_partial_is_replaced_once_and_draft_survives(page, streaming_page, request):
    container, attention, release, url, fake = streaming_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("const interval = window.setInterval; window.setInterval = (fn, ms, ...args) => ms === 2000 ? 0 : interval(fn, ms, ...args)")
    page.goto(f'{url}/pages/{attention.project}/supplier-migration')
    card = page.locator(f'#thread-{attention.id}')
    expect(card.locator('[data-agent-status]')).to_have_text('Agent typing…')
    expect(card.locator('[data-agent-typing] p')).to_have_text('Checking <script> safely.')
    assert card.locator('[data-agent-typing] script').count() == 0
    card.locator('textarea').fill('Keep this follow-up draft')
    shoot(request, page, 'responder-typing')
    release.set()
    expect(card.locator('[data-agent-typing]')).to_have_count(0)
    expect(card.locator('[data-message-type="reply"]')).to_have_count(1)
    expect(card.locator('[data-message-type="reply"] p')).to_have_text(fake.response['reply'])
    expect(card.locator('textarea')).to_have_value('Keep this follow-up draft')
    expect(card.locator('[data-agent-status]')).to_have_text('Agent replied · thread remains open')
    shoot(request, page, 'responder-recorded')
    assert errors == []
    assert container.attention().get(attention.id).state == 'open'


def test_takeback_removes_partial_without_recording_reply(page, streaming_page):
    container, attention, release, url, _ = streaming_page
    page.goto(f'{url}/pages/{attention.project}/supplier-migration')
    card = page.locator(f'#thread-{attention.id}')
    expect(card.locator('[data-agent-typing]')).to_have_count(1)
    container.attention().take(attention.id, actor='user', reason='I will answer')
    expect(card.locator('[data-agent-typing]')).to_have_count(0)
    release.set()
    eventually(lambda: not any(run.kind == 'responder' and run.status == 'running' for run in container.execution().runs()))
    assert container.attention().get(attention.id).replies == ()
