# ruff: noqa: F811
import json
from urllib.request import urlopen

import pytest

from fleet.services.reply_stream import partial_reply
from tests.integration.test_page_responder import responder
from tests.integration.test_triage_commands import triage  # noqa: F401
from tests.runtime_support import eventually
from tests.test_web_subscription import runtime_deck  # noqa: F401


@pytest.mark.parametrize(('source', 'expected'), [
    ('{', ''), ('{"reply":"Hello', 'Hello'),
    ('{"reason":"tools", "escalate":false,"reply":"Hi\\nthere', 'Hi\nthere'),
    ('{"reply":"A\\', 'A'), ('{"reply":"A\\u00', 'A'),
    ('{"reply":"A\\u00e9', 'Aé'), ('{"reply":"A\\ud83d', 'A'),
    ('{"reply":"A\\ud83d\\ude00', 'A😀'),
    ('{"reason":"reply text", "reply":"<img src=x>"}', '<img src=x>'),
])
def test_partial_structured_reply(source, expected):
    assert partial_reply(source) == expected


def test_delta_throttle_is_unstored_and_cleared_after_reply(triage, monkeypatch):
    services = triage[0]
    _, attention, engine, _, service, fake = responder(triage)
    emitted = []
    service.on_typing = lambda item, value: emitted.append((item, value))
    clock = iter([1., 1., 1.01, 1.02, 1.11, 1.11])
    monkeypatch.setattr('fleet.services.reply_stream.time.monotonic', lambda: next(clock))
    original = fake.turn
    def turn(*args, on_delta, **kwargs):
        before = services.store.latest_sequence()
        for chunk in ['{"reply":"First', ' second', ' third', ' fourth']:
            on_delta(chunk)
        assert services.store.latest_sequence() == before
        return original(*args, **kwargs)
    fake.turn = turn
    engine.schedule()
    service.process_next()
    assert [value['text'] for _, value in emitted if value] == ['First', 'First second third fourth']
    assert emitted[-1] == (attention.id, None)
    assert len(services.attention.get(attention.id).replies) == 1


def read_event(response, wanted):
    event = None
    while True:
        line = response.readline()
        assert line, 'SSE closed unexpectedly'
        if line.startswith(b'event: '):
            event = line[7:].strip().decode()
        if line.startswith(b'data: ') and event == wanted:
            return json.loads(line[6:])


def test_small_events_cross_runtime_and_web_without_snapshot_generation(runtime_deck):
    deck = runtime_deck
    deck.start_runtime()
    deck.start_web()
    eventually(lambda: deck.subscriber.runtime_status()['healthy'])
    with urlopen(deck.url + '/api/stream', timeout=5) as response:
        read_event(response, 'state')
        read_event(response, 'responder')
        version = deck.subscriber.version
        sequence = deck.container.store().latest_sequence()
        cached = deck.endpoint.cached_body
        value = dict(run='r', project='p', owner_at='now', text='Partial <b>text</b>', reply_ids=[])
        deck.runtime_state.set_typing('item', value)
        update = read_event(response, 'responder')
        assert update['items'] == {'item': value}
        assert len(json.dumps(update)) < 400
        assert deck.subscriber.version == version
        assert deck.container.store().latest_sequence() == sequence
        assert deck.endpoint.cached_body == cached
        # A newly connected web reader receives current transient state too.
        with urlopen(deck.url + '/api/stream', timeout=5) as reconnect:
            read_event(reconnect, 'state')
            assert read_event(reconnect, 'responder')['items'] == {'item': value}
        deck.runtime_state.set_typing('item', None)
        assert read_event(response, 'responder')['items'] == {}
    deck.runtime_state.set_typing('item', value)
    eventually(lambda: deck.subscriber.typing)
    deck.stop_runtime()
    eventually(lambda: not deck.subscriber.typing)


def test_typing_precedes_blocked_snapshot_and_is_retained_on_first_snapshot(runtime_deck, monkeypatch):
    import threading
    deck = runtime_deck
    deck.start_runtime()
    entered, release = threading.Event(), threading.Event()
    original = deck.endpoint.snapshot_bytes
    def blocked():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(deck.endpoint, 'snapshot_bytes', blocked)
    value = dict(run='r', project='p', owner_at='now', text='Early text', reply_ids=[])
    deck.runtime_state.set_typing('item', value)
    try:
        with urlopen(f'http://127.0.0.1:{deck.endpoint.http.server_port}/subscribe', timeout=2) as response:
            assert read_event(response, 'responder')['items'] == {'item': value}
            assert entered.wait(1)
            deck.runtime_state.set_typing('item', dict(value, text='Updated while rebuilding'))
            assert read_event(response, 'responder')['items']['item']['text'] == 'Updated while rebuilding'
            release.set()
            read_event(response, 'snapshot')
    finally:
        release.set()
    deck.start_web()
    eventually(lambda: deck.subscriber.runtime_status()['healthy'])
    assert deck.subscriber.typing['item']['text'] == 'Updated while rebuilding'


def test_paused_model_flushes_latest_text_and_close_prevents_late_updates():
    import threading
    from fleet.services.reply_stream import ReplyStream
    trailing = threading.Event()
    emitted = []
    def publish(text):
        emitted.append(text)
        if text == 'First latest':
            trailing.set()
    stream = ReplyStream(publish, interval=.05)
    stream.delta('{"reply":"First')
    stream.delta(' latest')
    assert trailing.wait(1), 'latest delta was lost while model paused'
    stream.delta(' discarded')
    stream.close()
    stream.delta(' after close')
    assert emitted == ['First', 'First latest']
