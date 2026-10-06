from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from playwright.sync_api import expect

from tests.test_canvas_chat_runtime import chat_deck
from tests.runtime_support import eventually


def shoot(request, page, name):
    destination = request.config.getoption('--shots')
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / (name + '.png')), full_page=True)


@pytest.mark.browser
def test_canvas_partial_reply_identity_and_unavailable(page, chat_deck, monkeypatch, request):
    deck = chat_deck
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.runtime_state.responder.health().get('ready'))
    release = threading.Event()
    class SlowServer:
        def start_thread(self):
            return 'browser-thread'

        def turn(self, thread, prompt, *, output_schema, effort, on_delta):
            on_delta('{"reply":"Inbox task is in Later.')
            assert release.wait(15), 'browser did not observe streamed text'
            return SimpleNamespace(status='completed', text='{"reply":"Inbox task is in Later. No task is in Now."}')
    monkeypatch.setattr(deck.runtime_state.responder, 'client', lambda: SlowServer())
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto(deck.url + '/canvas/' + deck.project)
    page.locator('#cmd').fill('are the inbox items prioritised?')
    page.locator('#cmd').press('Enter')
    try:
        panel = page.get_by_role('log', name='Conversation', exact=True)
        expect(panel).to_contain_text('Inbox task is in Later.', timeout=12000)
        expect(panel).to_contain_text('orchestrator · codex')
        expect(panel).to_contain_text('is replying')
        shoot(request, page, 'canvas-chat-streaming')
    finally:
        release.set()
    expect(panel).to_contain_text('No task is in Now.')
    expect(panel).not_to_contain_text('is replying')
    shoot(request, page, 'canvas-chat-completed')
    deck.stop_runtime()
    page.locator('#cmd').fill('are the inbox items prioritised?')
    page.locator('#cmd').press('Enter')
    expect(panel).to_contain_text('Responder unavailable:')
    expect(panel).to_contain_text('fleet serve is unavailable')
    shoot(request, page, 'canvas-chat-unavailable')
    assert errors == []
    assert deck.container.execution().runs() == []
