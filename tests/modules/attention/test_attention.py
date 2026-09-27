from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from fleet.modules.attention import AttentionFacade


class MemoryRepository:
    def __init__(self):
        self.items = {}
        self.history = []

    @contextmanager
    def transaction(self):
        yield self

    def get(self, item_id):
        return self.items[item_id]

    def find(self, source, source_reference):
        return next((item for item in self.items.values()
                     if (item.source, item.source_reference) == (source, source_reference)), None)

    def list(self):
        return list(self.items.values())

    def save(self, item, previous, actor):
        self.items[item.id] = item
        self.history.append((previous, item.state, actor))

    def imported_action(self, reference):
        return None


@pytest.fixture
def attention():
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    repo = MemoryRepository()
    return AttentionFacade(repo, lambda: now[0]), repo, now


def raise_item(facade, **changes):
    fields = dict(project="p1", kind="decision", owner="user", source="manual",
                  source_reference="question-1", headline="Choose a direction", context_reference="doc:1", actor="user")
    fields.update(changes)
    return facade.raise_item(**fields)


def test_repeated_signal_updates_one_item(attention):
    facade, repo, now = attention
    first = raise_item(facade)
    facade.acknowledge(first.id, actor="reviewer")
    for index in range(9):
        now[0] += timedelta(seconds=1)
        item = raise_item(facade, headline=f"Question revision {index}")
    assert item.id == first.id
    assert len(facade.list()) == 1
    assert item.headline == "Question revision 8"
    assert item.last_seen == now[0]
    assert item.state == "acknowledged"


def test_headline_limit_is_explicit(attention):
    facade, repo, _ = attention
    with pytest.raises(ValueError, match="12 words"):
        raise_item(facade, headline="word " * 13)
    assert not repo.items and not repo.history
    assert raise_item(facade, headline="word " * 12)


def test_expired_snooze_is_open_without_read_writes(attention):
    facade, repo, now = attention
    item = raise_item(facade)
    until = now[0] + timedelta(hours=1)
    facade.snooze(item.id, until=until, actor="user")
    assert not facade.list(state="open")
    before = (dict(repo.items), list(repo.history))
    now[0] = until
    assert facade.get(item.id).state == "open"
    assert [item.id for item in facade.list(state="open")] == [item.id]
    assert (repo.items, repo.history) == before


def test_states_record_actor_and_resolution(attention):
    facade, repo, now = attention
    item = raise_item(facade)
    facade.acknowledge(item.id, actor="ack-user")
    facade.snooze(item.id, until=now[0] + timedelta(hours=1), actor="snooze-user")
    result = facade.resolve(item.id, details="Answered in session", actor="source")
    assert result.resolution_details == "Answered in session"
    assert repo.history == [(None, "open", "user"), ("open", "acknowledged", "ack-user"),
                            ("acknowledged", "snoozed", "snooze-user"), ("snoozed", "resolved", "source")]


def test_invalid_commands_leave_item_unchanged(attention):
    facade, repo, now = attention
    item = raise_item(facade)
    with pytest.raises(ValueError, match="future"):
        facade.snooze(item.id, until=now[0], actor="user")
    with pytest.raises(ValueError, match="actor"):
        facade.acknowledge(item.id, actor="")
    assert facade.get(item.id) == item
    assert len(repo.history) == 1


@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode"])
def test_waiting_observations_are_durable_occurrences(attention, tool):
    facade, repo, now = attention
    session = {"id": "s1", "project": "demo", "project_id": None, "agent": "claude",
               "activity": {"kind": "tool", "name": tool, "summary": "Choose", "ts": 100}}
    host = {"name": "worker", "ok": True, "jobs": [], "sessions": [session]}
    facade.observe(host)
    first, = facade.list()
    assert first.kind == "decision"
    assert first.source_reference == f"session:worker:s1:{tool}@100"
    facade.acknowledge(first.id, actor="user")
    now[0] += timedelta(seconds=1)
    facade.observe(host)
    assert len(facade.list()) == 1
    assert facade.get(first.id).state == "acknowledged"
    seen = facade.get(first.id).last_seen
    history = list(repo.history)
    now[0] += timedelta(seconds=1)
    facade.observe({**host, "ok": False, "sessions": []})
    assert facade.get(first.id).last_seen == seen
    assert repo.history == history
    session["activity"] = {"kind": "text", "summary": "Continuing", "ts": 200}
    facade.observe(host)
    assert facade.get(first.id).state == "resolved"
    assert facade.get(first.id).resolution_details
    assert facade.get(first.id).resolved_at == now[0]
    session["activity"] = {"kind": "tool", "name": tool, "summary": "Next choice", "ts": 300}
    facade.observe(host)
    assert len(facade.list()) == 2
    assert len(facade.list(state="open")) == 1
