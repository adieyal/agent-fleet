import json
from datetime import datetime, timedelta, timezone

import pytest
from fleet.container import configured_container


def raise_item(facade, reference="q1"):
    return facade.raise_item(project="p1", kind="alert", owner="user", source="test",
                             source_reference=reference, headline="Review output", context_reference="doc:1",
                             work_item="w1", run="r1", actor="tester")


@pytest.mark.parametrize("state", ["acknowledged", "snoozed", "resolved"])
def test_items_states_and_history_survive_reopening(tmp_path, state):
    store = configured_container(path=tmp_path / 'store.db').store()
    facade = configured_container(store).initialized_attention(workspace_path=tmp_path / 'missing.json')
    item = raise_item(facade)
    facade.acknowledge(item.id, actor="reader")
    if state == "snoozed":
        facade.snooze(item.id, until=datetime(2099, 1, 1, tzinfo=timezone.utc), actor="reader")
    elif state == "resolved":
        facade.resolve(item.id, details="Handled", actor="reader")
    facade = configured_container(configured_container(path=store.path).store()).initialized_attention(workspace_path=tmp_path / 'missing.json')
    assert facade.get(item.id).state == state
    assert facade.get(item.id).work_item == "w1"
    assert facade.get(item.id).run == "r1"
    before = store.history_after(0)
    facade.list()
    facade.get(item.id)
    assert store.history_after(0) == before
    assert [row["actor"] for row in before[:2]] == ["tester", "reader"]
    assert [(row["from"], row["to"]) for row in before[:2]] == [("", "open"), ("open", "acknowledged")]
    if state != "acknowledged":
        assert (before[-1]["from"], before[-1]["to"], before[-1]["actor"]) == ("acknowledged", state, "reader")


def test_workspace_actions_import_once_and_keep_file(tmp_path):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    path = tmp_path / "workspace.json"
    original = json.dumps({"attention": {
        "old-ack": {"state": "acknowledged", "at": now.timestamp()},
        "old-snooze": {"state": "snoozed", "at": now.timestamp(),
                       "until": (now + timedelta(hours=1)).timestamp()}}})
    path.write_text(original)
    store = configured_container(path=tmp_path / 'store.db', clock=lambda : now).store()
    facade = configured_container(store).initialized_attention(workspace_path=path)
    assert path.read_text() == original
    assert path.with_suffix(".json.bak").read_text() == original
    ack = raise_item(facade, "old-ack")
    snooze = raise_item(facade, "old-snooze")
    assert ack.state == "acknowledged"
    assert snooze.state == "snoozed"
    facade.resolve(ack.id, details="Handled", actor="user")
    history = store.history_after(0)
    facade = configured_container(store).initialized_attention(workspace_path=path)
    assert facade.get(ack.id).state == "resolved"
    assert store.history_after(0) == history
    assert path.read_text() == original


def test_sqlite_dedupe_and_expired_reads_do_not_write(tmp_path):
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    store = configured_container(path=tmp_path / 'store.db', clock=lambda : now[0]).store()
    facade = configured_container(store).initialized_attention(workspace_path=tmp_path / 'missing.json')
    for _ in range(10):
        item = raise_item(facade)
    assert len(facade.list()) == 1
    facade.snooze(item.id, until=now[0] + timedelta(seconds=1), actor="user")
    history = store.history_after(0)
    now[0] += timedelta(seconds=1)
    assert facade.list(state="open")[0].id == item.id
    assert facade.get(item.id).state == "open"
    assert store.history_after(0) == history


@pytest.mark.parametrize('state', ['open', 'acknowledged', 'snoozed', 'resolved'])
def test_replies_persist_in_order_with_history_and_preserve_lifecycle(tmp_path, state):
    from dataclasses import replace
    now = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    store = configured_container(path=tmp_path / 'replies.db', clock=lambda: now[0]).store()
    facade = configured_container(store).attention()
    item = raise_item(facade)
    if state == 'acknowledged':
        item = facade.acknowledge(item.id, actor='user')
    elif state == 'snoozed':
        item = facade.snooze(item.id, until=now[0] + timedelta(days=1), actor='user')
    elif state == 'resolved':
        item = facade.resolve(item.id, details='Checked', actor='user')
    sequence = store.latest_sequence()
    for index, actor in enumerate(['user', 'agent', 'user']):
        now[0] += timedelta(seconds=1)
        updated = facade.reply(item.id, f'Message {index}', actor=actor)
        assert replace(updated, replies=()) == item
    reopened = configured_container(path=store.path).attention()
    messages = reopened.get(item.id).replies
    assert [message.body for message in messages] == ['Message 0', 'Message 1', 'Message 2']
    assert [message.actor for message in messages] == ['user', 'agent', 'user']
    assert [message.time for message in messages] == sorted(message.time for message in messages)
    assert len({message.id for message in messages}) == 3
    history = store.history_after(sequence)
    assert [row['subject'] for row in history] == [f'attention:{item.id}:reply:{message.id}' for message in messages]
    assert [json.loads(row['to'])['body'] for row in history] == [message.body for message in messages]
    assert configured_container(store).decisions().list() == []
    facade.resolve(item.id, details='Done', actor='user')
    assert facade.get(item.id).state == 'resolved'
    with pytest.raises(ValueError, match='resolved'):
        facade.reopen(item.id, actor='user')
    assert facade.get(item.id).replies == messages


@pytest.mark.parametrize('body,actor,error', [('', 'user', 'body is required'), ('hello', '', 'actor is required'),
                                           ('x' * 8193, 'user', 'exceeds 8 KiB')])
def test_invalid_reply_does_not_write(tmp_path, body, actor, error):
    container = configured_container(path=tmp_path / 'replies.db')
    item = raise_item(container.attention())
    sequence = container.store().latest_sequence()
    with pytest.raises(ValueError, match=error):
        container.attention().reply(item.id, body, actor=actor)
    assert container.store().latest_sequence() == sequence


def test_schema_18_upgrade_preserves_annotation_history_and_adds_empty_replies(tmp_path):
    import sqlite3
    from contextlib import closing
    from dataclasses import asdict
    from fleet.infrastructure.sqlite.migrations import MIGRATIONS
    from fleet.modules.attention import PageAnnotation

    path = tmp_path / 'old.db'
    annotation = PageAnnotation('comment', 'fleet://projects/p1/pages/evidence', 'revision', 'Check evidence',
                               {'type': 'FragmentSelector', 'value': 'evidence'}, 'user', 'user',
                               'User must review', 'Check evidence')
    with closing(sqlite3.connect(path)) as connection:
        for statements in MIGRATIONS[:18]:
            for statement in statements:
                connection.execute(statement)
        connection.execute("INSERT INTO attention_item (id, project, kind, owner, source, source_reference, "
            "headline, context_reference, state, last_seen, page_annotation) "
            "VALUES ('old', 'p1', 'decision', 'user', 'page', 'comment', 'Check evidence', ?, 'open', ?, ?)",
            (annotation.page, '2026-10-05T09:00:00+00:00', json.dumps(asdict(annotation))))
        connection.execute('INSERT INTO state_history (subject, "from", "to", actor, time) '
                           "VALUES ('attention:old', '', 'open', 'user', '2026-10-05T09:00:00+00:00')")
        connection.execute('PRAGMA user_version = 18')
        connection.commit()
    container = configured_container(path=path)
    assert container.store().schema_version() == 19
    item = container.attention().get('old')
    assert item.page_annotation == annotation
    assert item.replies == () and item.state == 'open'
    assert len(container.store().history_after(0)) == 1
    container.attention().reply('old', 'Verified after upgrade', actor='agent')
    assert container.attention().get('old').page_annotation == annotation
    assert len(container.store().history_after(0)) == 2
    container.attention().resolve('old', details='Reviewed', actor='user')
    assert container.attention().reopen('old', actor='user').state == 'open'
    assert container.attention().get('old').replies[0].body == 'Verified after upgrade'
