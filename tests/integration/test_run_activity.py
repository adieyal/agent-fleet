import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


from fleet.container import configured_container
from fleet.transport import Host
from fleet.web.server import FleetState, apply_message


def test_stream_records_actions_without_duplicate_history_or_push():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    store = configured_container(clock=lambda : now).store()
    work = configured_container(store).work()
    item = work.add(project="p", title="Task", goal="Ship", actor="user")
    execution = configured_container(store).execution()
    run = execution.link("worker", "job", item.id, actor="user")
    state = FleetState([Host('worker', None)], container=configured_container(store=store))
    transport_calls = []

    def retry_deliveries(host):
        assert not state.changed._is_owned()
        transport_calls.append(host)

    state.execution.retry_deliveries = retry_deliveries
    messages = [json.loads(line) for line in
                (Path(__file__).parents[1] / "fixtures/run_reconciliation.jsonl").read_text().splitlines()]
    apply_message(state, state.hosts[0], messages[0])
    for index, (name, summary, action) in enumerate([
        ("Read", "a.py", "read"), ("Edit", "a.py", "edit"),
        ("Bash", "cd src && uv run pytest -q", "test"),
        ("Bash", "sleep 10", "wait"),
    ]):
        event = dict(kind="tool", name=name, summary=summary, ts=100 + index)
        messages[1]["job"].update(activity=event, events=[event])
        apply_message(state, state.hosts[0], messages[1])
        recorded = execution.get_run(run.id)
        assert recorded.current_action == action
        assert recorded.action_observed_at == datetime.fromtimestamp(100 + index, timezone.utc)
        sequence, version = store.latest_sequence(), state.version
        apply_message(state, state.hosts[0], messages[1])
        assert (store.latest_sequence(), state.version) == (sequence, version)
        assert state.document()["hosts"][0]["jobs"][0]["activity"]["activity_class"] == action
    assert transport_calls
    now += timedelta(seconds=30)
    sequence, version = store.latest_sequence(), state.version
    apply_message(state, state.hosts[0], {'type': 'heartbeat'})
    assert execution.run_activity(recorded)['action_freshness'] == 'current'
    change, = store.history_after(sequence)
    assert change['subject'] == 'execution:host:worker'
    sequence, version = store.latest_sequence(), state.version
    apply_message(state, state.hosts[0], {'type': 'heartbeat'})
    assert (store.latest_sequence(), state.version) == (sequence, version)
    assert configured_container(configured_container().store()).execution().get_run(run.id) == recorded
