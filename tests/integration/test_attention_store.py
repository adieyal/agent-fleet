import json
from datetime import datetime, timedelta, timezone

import pytest

from fleet.composition import open_attention, open_store


def raise_item(facade, reference="q1"):
    return facade.raise_item(project="p1", kind="alert", owner="user", source="test",
                             source_reference=reference, headline="Review output", context_reference="doc:1",
                             work_item="w1", run="r1", actor="tester")


@pytest.mark.parametrize("state", ["acknowledged", "snoozed", "resolved"])
def test_items_states_and_history_survive_reopening(tmp_path, state):
    store = open_store(tmp_path / "store.db")
    facade = open_attention(store, workspace_path=tmp_path / "missing.json")
    item = raise_item(facade)
    facade.acknowledge(item.id, actor="reader")
    if state == "snoozed":
        facade.snooze(item.id, until=datetime(2099, 1, 1, tzinfo=timezone.utc), actor="reader")
    elif state == "resolved":
        facade.resolve(item.id, details="Handled", actor="reader")
    facade = open_attention(open_store(store.path), workspace_path=tmp_path / "missing.json")
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
    store = open_store(tmp_path / "store.db", clock=lambda: now)
    facade = open_attention(store, workspace_path=path)
    assert path.read_text() == original
    assert path.with_suffix(".json.bak").read_text() == original
    ack = raise_item(facade, "old-ack")
    snooze = raise_item(facade, "old-snooze")
    assert ack.state == "acknowledged"
    assert snooze.state == "snoozed"
    facade.resolve(ack.id, details="Handled", actor="user")
    history = store.history_after(0)
    facade = open_attention(store, workspace_path=path)
    assert facade.get(ack.id).state == "resolved"
    assert store.history_after(0) == history
    assert path.read_text() == original


def test_sqlite_dedupe_and_expired_reads_do_not_write(tmp_path):
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    store = open_store(tmp_path / "store.db", clock=lambda: now[0])
    facade = open_attention(store, workspace_path=tmp_path / "missing.json")
    for _ in range(10):
        item = raise_item(facade)
    assert len(facade.list()) == 1
    facade.snooze(item.id, until=now[0] + timedelta(seconds=1), actor="user")
    history = store.history_after(0)
    now[0] += timedelta(seconds=1)
    assert facade.list(state="open")[0].id == item.id
    assert facade.get(item.id).state == "open"
    assert store.history_after(0) == history
