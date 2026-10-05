from contextlib import contextmanager
from dataclasses import replace
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

    def list(self, *, project=None, owner=None):
        return [item for item in self.items.values() if (project is None or item.project == project)
                and (owner is None or item.owner == owner)]

    def snooze_ends(self):
        return [item.snooze_until for item in self.items.values() if item.snooze_until is not None]

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


@pytest.mark.parametrize("resolution", ["removed", "user"])
@pytest.mark.parametrize("initial_start,restreamed_start", [(100, 100), (100, 100.0), (100.0, 100)])
def test_resolved_failure_survives_restream(attention, resolution, initial_start, restreamed_start):
    facade, repo, now = attention
    job = dict(id="j1", project="demo", project_id=None, status="failed", updated_at=500,
               steps=[dict(index=0, status="failed", title="test", started_at=initial_start,
                           message=None, answered_by=None)])
    host = dict(name="worker", ok=True, jobs=[job], sessions=[])
    facade.observe(host)
    first, = facade.list()
    # Older stores wrote the raw float spelling; exercise compatibility with those keys.
    if isinstance(initial_start, float):
        first = replace(first, source_reference="job:worker:j1:failed:0@100.0")
        repo.items[first.id] = first
    if resolution == "removed":
        # The pre-retention stream reconciled missing jobs without present_jobs.
        facade.reconcile("stream:worker", set(), actor="host-stream")
    else:
        facade.resolve(first.id, actor="user", details="Handled already")
    resolved = facade.get(first.id)
    history = list(repo.history)
    facade.mandate = lambda project: pytest.fail("resolved occurrence must not route to triage")
    job["steps"][0]["started_at"] = restreamed_start
    now[0] += timedelta(seconds=1)
    facade.observe(host)
    assert facade.list(state="open") == []
    again, = facade.list(state="resolved")
    assert again.id == first.id
    assert again.resolved_at == resolved.resolved_at
    assert again.resolution_details == resolved.resolution_details
    assert repo.history[:len(history)] == history
    assert job["status"] == "failed"


@pytest.mark.parametrize("change", [dict(started_at=100.000001), dict(index=1)])
def test_new_failure_occurrence_still_raises(attention, change):
    facade, _, _ = attention
    step = dict(index=0, status="failed", title="test", started_at=100.0,
                message=None, answered_by=None)
    host = dict(name="worker", ok=True, sessions=[], jobs=[dict(
        id="j1", project="demo", project_id=None, status="failed", updated_at=500, steps=[step])])
    facade.observe(host)
    first, = facade.list()
    facade.resolve(first.id, actor="user", details="Handled")
    step.update(change)
    facade.observe(host)
    new, = facade.list(state="open")
    assert new.id != first.id
    assert facade.list(state="resolved")[0].id == first.id
