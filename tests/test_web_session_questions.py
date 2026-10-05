"""A question an interactive session asks in its terminal becomes an attention item the deck can show but not answer."""
import json
import sys
from urllib.request import urlopen

import pytest


from fleet.container import configured_container
from fleet_web.server import apply_message
from test_web_attention import HOSTS, Deck
from test_web_refusals import post

QUESTIONS = [{"header": "Probe run", "multi_select": False,
              "question": "The agent-friendliness probe needs a live site before it can score anything. How should I run it?",
              "options": [{"label": "Staging", "description": "Run against staging now"},
                          {"label": "Skip", "description": "Leave the probe for later"}]}]


def asked(occurrence="q1", kind="input_requested", project="restoke", cwd="/srv/restoke", questions=QUESTIONS):
    return {"type": "input_observation", "schema_version": 1, "runtime": "claude", "owner_type": "session",
            "job_id": None, "session_id": "s1", "step_index": None, "project": project, "kind": kind,
            "reason": "question", "source_event": "PreToolUse" if kind == "input_requested" else "PostToolUse",
            "source_event_id": occurrence, "observed_at": 200.0, "context_reference": "/retained/hook.json",
            "cwd": cwd, "request": {"tool": "AskUserQuestion", "description": "", "rules": [],
                                    "detail": questions[0]["question"], "questions": questions}}


@pytest.fixture
def deck(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {"python": sys.executable}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    deck = Deck()
    apply_message(deck.state, HOSTS[0], {"type": "hello"})
    yield deck
    deck.close()


def decision(deck, item_id):
    with urlopen(deck.url + "/api/decision?id=" + item_id, timeout=5) as response:
        return json.load(response)


def test_a_session_question_is_an_item_until_the_terminal_answers_it(deck):
    project = configured_container().initialized_workspace().edit_registry(lambda registry: registry.create('Restoke V2'))
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project.id, 'home', 'restoke'))
    deck.state.refresh_registry()   # as every pushed document does
    for _ in range(3):
        apply_message(deck.state, HOSTS[0], asked())
    [item] = deck.state.attention.list()
    assert item.headline == "Probe run: The agent-friendliness probe needs a live site before it can…"
    assert len(item.headline.split()) == 12
    assert (item.kind, item.state, item.project) == ("decision", "open", project.id)
    [listed] = deck.state.document()["attention"]
    assert (listed["summary"], listed["questions"], listed["refusals"]) == (item.headline, 1, 0)
    detail = decision(deck, item.id)["session_question"]
    assert detail == {"host": "home", "session": "s1", "cwd": "/srv/restoke", "project": "Restoke V2",
                      "label": "restoke", "state": "open", "resolution": None, "questions": QUESTIONS}
    status, body = post(deck, "/api/decision/answer", {"id": item.id, "answer": "Staging"})
    assert status == 400 and "answered in its terminal" in body["error"]
    apply_message(deck.state, HOSTS[0], asked(kind="input_cleared"))
    closed = deck.state.attention.get(item.id)
    assert (closed.state, closed.resolution_details) == ("resolved", "answered in session")


def test_an_unregistered_directory_shows_the_cwd_and_an_older_fleetd_still_ingests(deck):
    apply_message(deck.state, HOSTS[0], asked(project="scratch", cwd="/tmp/scratch"))
    [item] = deck.state.attention.list()
    detail = decision(deck, item.id)["session_question"]
    assert (detail["project"], detail["label"], detail["cwd"]) == (None, "scratch", "/tmp/scratch")
    older = asked("q2")
    del older["cwd"]
    older["request"] = {key: value for key, value in older["request"].items() if key != "questions"}
    apply_message(deck.state, HOSTS[0], older)
    second = next(each for each in deck.state.attention.list() if each.id != item.id)
    assert second.headline == "Claude asks to use AskUserQuestion" and second.questions == ()


@pytest.mark.parametrize('order', ['stream-first', 'hook-first'])
def test_batch12_stream_and_hook_question_share_one_open_item(deck, order):
    session = {"id": "s1", "project": "restoke", "agent": "claude", "status": "working", "started_at": 100.0,
               "activity": {"kind": "tool", "name": "AskUserQuestion", "summary": "How should I run it?", "ts": 199.0}}
    messages = [{"type": "session", "session": session}, asked()]
    for message in messages if order == 'stream-first' else reversed(messages):
        apply_message(deck.state, HOSTS[0], message)
    [item] = deck.state.attention.list(state='open')
    assert item.source == 'runtime-input:home' and item.questions
    assert sorted(item['state'] for item in deck.state.document()['attention']) == (
        ['open', 'resolved'] if order == 'stream-first' else ['open'])
    if order == 'stream-first':
        [folded] = deck.state.attention.list(state='resolved')
        assert 'superseded by session question' in folded.resolution_details
    apply_message(deck.state, HOSTS[0], asked(kind='input_cleared'))
    apply_message(deck.state, HOSTS[0], {"type": "session", "session": session})
    assert deck.state.attention.list(state='open') == []
    # A later question on the same tool still falls back to stream attention.
    session['activity'] = {**session['activity'], 'ts': 300.0, 'summary': 'A different question?'}
    apply_message(deck.state, HOSTS[0], {"type": "session", "session": session})
    [later] = deck.state.attention.list(state='open')
    assert later.source == 'stream:home'


def test_legacy_stream_session_question_is_terminal_only(deck):
    from fleet.modules.attention.domain import StreamContext

    item = deck.state.attention.raise_item(project='restoke', kind='decision', owner='user',
        source='stream:home', source_reference='session:home:legacy:question',
        headline='Keep the double fetch behind a flag?', context_reference='session:home:legacy',
        stream_context=StreamContext('home', 'session', 'legacy', 'restoke', None,
                                     'question', 'Keep the double fetch behind a flag?', 200), actor='host-stream')
    detail = decision(deck, item.id)
    assert detail['session_question']['session'] == 'legacy'
    assert detail['session_question']['questions'] == []
    sequence = deck.state.store.latest_sequence()
    status, body = post(deck, '/api/decision/answer', {'id': item.id, 'answer': 'Keep it'})
    assert status == 400 and 'terminal' in body['error']
    assert deck.state.attention.get(item.id).state == 'open'
    assert deck.state.store.latest_sequence() == sequence
    assert configured_container(deck.state.store).decisions().list() == []
