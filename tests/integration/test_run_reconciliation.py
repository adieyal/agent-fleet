import json
from pathlib import Path

import pytest


from fleet.container import configured_container
from fleet.transport import Host
from fleet.services.live import FleetState, apply_message


@pytest.mark.parametrize("outcome", ["done", "failed", "blocked", "cancelled"])
def test_recorded_stream_reconciles_without_changing_work(outcome):
    store = configured_container().store()
    work = configured_container(store).work()
    item = work.add(project="p", title="Task", goal="Ship", actor="user")
    work.add_criterion(item.id, text="Accept", verification="accepted", actor="user")
    before = (work.get(item.id), work.progress(item.id), work.criteria(item.id))
    execution = configured_container(store).execution()
    run = execution.link("worker", "job", item.id, actor="user")
    other = execution.link("other", "job", item.id, actor="user")
    state = FleetState([Host('worker', None)], container=configured_container(store=store))
    host = state.hosts[0]
    messages = [json.loads(line) for line in
                (Path(__file__).parents[1] / "fixtures/run_reconciliation.jsonl").read_text().splitlines()]
    for message in messages[:2]:
        apply_message(state, host, message)
    assert execution.runs()[0].status == "running"
    sequence = store.latest_sequence()
    apply_message(state, host, {"type": "heartbeat", "time": 110})
    change, = store.history_after(sequence)
    assert change["subject"] == "execution:host:worker"
    sequence, version = store.latest_sequence(), state.version
    apply_message(state, host, messages[1])
    apply_message(state, host, {"type": "heartbeat", "time": 110})
    assert (store.latest_sequence(), state.version) == (sequence, version)
    # follow_host reports this after run_stream's freshness deadline.
    state.update(host.name, lambda entry: entry.update(ok=False, error="no heartbeat for 20s", jobs={}))
    assert execution.runs()[0].status == "unknown outcome"
    sequence, version = store.latest_sequence(), state.version
    state.update(host.name, lambda entry: entry.update(ok=False, error="no heartbeat for 20s", jobs={}))
    assert (store.latest_sequence(), state.version) == (sequence, version)
    apply_message(state, host, messages[0])
    final = messages[2]
    final["job"]["status"] = outcome
    apply_message(state, host, final)
    reconciled = execution.runs()[0]
    assert reconciled.id == run.id
    assert reconciled.status == {"done": "succeeded", "failed": "failed", "blocked": "failed", "cancelled": "stopped"}[outcome]
    assert reconciled.reason == ("blocked" if outcome == "blocked" else None)
    assert reconciled.runtime == "codex"
    assert reconciled.start.timestamp() == 100
    assert reconciled.end.timestamp() == 120
    assert execution.runs()[1] == other
    assert (work.get(item.id), work.progress(item.id), work.criteria(item.id)) == before
    entries = configured_container(store).library().list()
    assert {entry.kind for entry in entries} == {"report", "trace"}
    assert all(entry.run == run.id and entry.work_item == item.id and entry.project == "p" for entry in entries)
    trace = next(entry for entry in entries if entry.kind == "trace")
    assert trace.availability == "available"
    final["job"]["trace"]["availability"] = "unavailable"
    apply_message(state, host, final)
    entries = configured_container(configured_container().store()).library().list()
    pruned = next(entry for entry in entries if entry.kind == "trace")
    assert pruned.id == trace.id and pruned.availability == "unavailable"
    sequence, version = store.latest_sequence(), state.version
    apply_message(state, host, final)
    if outcome in ("failed", "blocked"):
        attention, = configured_container(store).initialized_attention().list()
        change, = store.history_after(sequence)
        assert change["subject"] == f"attention:{attention.id}"
        assert state.version == version + 1
    else:
        assert (store.latest_sequence(), state.version) == (sequence, version)
    state.update(host.name, lambda entry: entry.update(ok=False, error="offline", jobs={}))
    assert execution.runs()[0] == reconciled
