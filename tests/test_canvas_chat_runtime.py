"""Canvas questions cross from web subscriber to the serve-owned responder."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet.services.runtime import endpoint_path
from tests.runtime_support import RuntimeDeck, eventually


@pytest.fixture
def chat_deck(monkeypatch):
    monkeypatch.delenv('FLEET_JOB_ID', raising=False)
    deck = RuntimeDeck(monkeypatch)
    project = deck.container.initialized_workspace().edit_registry(lambda registry: registry.create('chat-test')).id
    canvas = deck.container.canvas()
    canvas.init(project, actor='user')
    canvas.operation(project, 'item.create', {'title': 'Inbox task'}, actor='user')
    deck.project = project
    try:
        yield deck
    finally:
        deck.close()


def send(deck, text, op_id='question-1'):
    request = Request(deck.url + '/api/canvas/op', data=json.dumps({
        'space': deck.project, 'op': 'message.send', 'args': {'text': text}, 'op_id': op_id}).encode(),
        headers={'Content-Type': 'application/json', 'Origin': deck.url})
    with urlopen(request, timeout=8) as response:
        return json.load(response)


def model(deck):
    return deck.read('/api/canvas?space=' + deck.project)


def test_question_uses_owner_and_replay_does_not_launch_again(chat_deck):
    deck = chat_deck
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.runtime_state.responder.health().get('ready'))
    before = model(deck)
    sent = send(deck, 'are the inbox items prioritised?')
    assert sent['result']['agent_request'] is True
    reply = eventually(lambda: next((message for message in model(deck)['messages']['orch']
                                     if message.get('status') == 'completed'), None))
    assert reply['text'] == 'Inbox task is in Later.'
    assert reply['who'] == 'orchestrator · codex'
    assert reply['proposals'] == []
    assert send(deck, 'are the inbox items prioritised?')['replayed'] is True
    after = model(deck)
    assert len(after['messages']['orch']) == 2
    assert after['items'] == before['items']
    assert deck.container.execution().runs() == []
    assert after['proposals'] == before['proposals']
    # Only the owner has a running app-server, never the web subscriber.
    assert deck.subscriber.responder.server is None
    command = send(deck, "what's costing the most?", op_id='cost-1')
    assert command['result']['proposals']
    assert not command['result'].get('agent_request')


def test_runtime_unavailable_is_a_conversation_failure(chat_deck):
    deck = chat_deck
    deck.start_web()
    send(deck, 'are the inbox items prioritised?')
    reply = model(deck)['messages']['orch'][-1]
    assert reply['status'] == 'failed'
    assert 'fleet serve is unavailable' in reply['text']
    assert reply['proposals'] == []


def test_pending_question_recovers_and_partial_reply_is_not_replayed(chat_deck):
    deck = chat_deck
    canvas = deck.container.canvas()
    pending = canvas.operation(deck.project, 'message.send', {'text': 'are the inbox items prioritised?'},
                               actor='user')['result']['reply']
    partial = canvas.operation(deck.project, 'message.send', {'text': 'interrupted question'},
                               actor='user')['result']['reply']
    def interrupt(engine):
        engine.need('message', partial, 'message').update(status='streaming', text='partial')
    canvas.kernel_step(deck.project, interrupt)
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: any(message['id'] == pending and message.get('status') == 'completed'
                           for message in model(deck)['messages']['orch']))
    reply = next(message for message in model(deck)['messages']['orch'] if message['id'] == partial)
    assert reply['status'] == 'failed'
    assert 'restarted' in reply['text']


def test_bridge_rejects_arbitrary_operations_origins_and_stale_generation(chat_deck):
    deck = chat_deck
    deck.start_runtime()
    endpoint = json.loads(endpoint_path(deck.container.settings()['store_path']).read_text())
    url = f"http://127.0.0.1:{endpoint['port']}/canvas-question"
    cases = [({'generation': endpoint['generation'], 'space': deck.project, 'message': 'missing'}, {}),
             ({'generation': 'stale', 'space': deck.project, 'message': 'missing'}, {}),
             ({'op': 'item.create', 'args': {'title': 'unapproved'}}, {}),
             ({}, {'Origin': 'http://evil.example'})]
    for body, headers in cases:
        with pytest.raises(HTTPError) as refused:
            urlopen(Request(url, data=json.dumps(body).encode(), headers=headers), timeout=5)
        assert refused.value.code == (403 if headers else 400)
    assert len(deck.container.canvas().state(deck.project, person='user')['items']) == 1
