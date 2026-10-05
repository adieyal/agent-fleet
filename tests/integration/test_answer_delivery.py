from fleet import transport
from fleet.composition import open_attention, open_decisions, open_execution, open_store, open_work
from fleet.web.server import FleetState, apply_message
from fleet.modules.attention import StreamContext
from fleet.infrastructure.sqlite.execution import ExecutionRepository
import pytest
from concurrent.futures import ThreadPoolExecutor


def question(monkeypatch):
    monkeypatch.setattr(transport, "host_by_name", lambda name: transport.Host(name, None))
    store = open_store()
    work = open_work(store).add(project="p", title="Ship", goal="Ship", actor="adi")
    run = open_execution(store).link("carbon", "job1", work.id, actor="adi", runtime="claude")
    item = open_attention(store).raise_item(project="p", kind="decision", owner="user",
        source="runtime-input:carbon", source_reference="q1", headline="Proceed?",
        context_reference="request:1", actor="fleetd", run=run.id, work_item=work.id)
    return store, run, item


def test_lost_reply_retries_same_key_once_and_repeated_heartbeat_does_not_write(monkeypatch):
    store, run, item = question(monkeypatch)
    applied = {}
    calls = []

    def send(host, arguments, **kwargs):
        key = arguments[arguments.index("--key") + 1]
        calls.append(key)
        if key not in applied:
            applied[key] = kwargs["stdin_text"]
            raise transport.FleetError("reply lost")
        return {"schema_version": 1, "key": key, "status": "applied"}

    monkeypatch.setattr(transport, "call", send)
    decision = open_decisions(store).answer(item.id, "Proceed", actor="adi")
    assert open_attention(store).get(item.id).state == "resolved"
    state = FleetState([transport.Host("carbon", None)], store=store)
    apply_message(state, state.hosts[0], {"type": "hello"})
    apply_message(state, state.hosts[0], {"type": "heartbeat"})
    delivery, = open_execution(store).deliveries()
    assert delivery.decision == decision.id
    assert delivery.status == "applied"
    assert calls == [delivery.key, delivery.key]
    assert len(applied) == 1
    sequence, version = store.latest_sequence(), state.version
    apply_message(state, state.hosts[0], {"type": "heartbeat"})
    assert (store.latest_sequence(), state.version) == (sequence, version)


def test_offline_decision_survives_restart_and_delivers_on_reconnect(monkeypatch):
    store, run, item = question(monkeypatch)
    def offline(*args, **kwargs):
        raise transport.FleetError("offline")
    monkeypatch.setattr(transport, "call", offline)
    decision = open_decisions(store).answer(item.id, "Proceed", actor="adi")
    assert open_decisions(open_store()).get(decision.id) == decision
    assert open_execution(store).deliveries()[0].status == "pending"
    calls = []
    def online(host, arguments, **kwargs):
        calls.append(arguments)
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied"}
    monkeypatch.setattr(transport, "call", online)
    state = FleetState([transport.Host("carbon", None)], store=open_store())
    apply_message(state, state.hosts[0], {"type": "hello"})
    apply_message(state, state.hosts[0], {"type": "heartbeat"})
    assert len(calls) == 1
    assert open_execution(store).deliveries()[0].status == "applied"


def test_delivery_transport_does_not_hold_stream_state_lock(monkeypatch):
    store, run, item = question(monkeypatch)
    monkeypatch.setattr(transport, "call", lambda *args, **kwargs:
        {"schema_version": 1, "key": "wrong", "status": "applied"})
    open_decisions(store).answer(item.id, "Proceed", actor="adi")
    state = FleetState([transport.Host("carbon", None)], store=store)
    calls = []

    def acquire_lock():
        acquired = state.changed.acquire(blocking=False)
        if acquired:
            state.changed.release()
        return acquired

    def send(host, arguments, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(acquire_lock).result(timeout=2)
        calls.append(arguments)
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied"}

    monkeypatch.setattr(transport, "call", send)
    apply_message(state, state.hosts[0], {"type": "hello"})
    assert len(calls) == 1
    assert open_execution(store).deliveries()[0].status == "applied"


def test_busy_delivery_stays_pending_without_failures_or_history_then_applies(monkeypatch):
    store, run, item = question(monkeypatch)
    outcome = "busy"
    calls = []

    def send(host, arguments, **kwargs):
        key = arguments[arguments.index("--key") + 1]
        calls.append(key)
        return {"schema_version": 1, "key": key, "status": outcome}

    monkeypatch.setattr(transport, "call", send)
    open_decisions(store).answer(item.id, "Proceed", actor="adi")
    execution = open_execution(store)
    delivery, = execution.deliveries()
    assert (delivery.status, delivery.failures, delivery.error) == ("pending", 0, None)
    state = FleetState([transport.Host("carbon", None)], store=store)
    sequence = store.latest_sequence()
    for index in range(4):
        state.update("carbon", lambda entry: entry.update(ok=True, revision=index), ingest=False)
    version = state.version
    state.update("carbon", lambda entry: entry.update(ok=True, revision=3), ingest=False)
    assert state.version == version
    change, = store.history_after(sequence)
    assert change["subject"] == "execution:host:carbon"
    assert execution.deliveries() == [delivery]
    assert not [item for item in open_attention(store).list() if item.kind == "alert"]
    assert calls == [delivery.key] * 5
    outcome = "applied"
    state.update("carbon", lambda entry: entry.update(revision=4), ingest=False)
    assert execution.deliveries()[0].status == "applied"


def test_lasting_failure_raises_one_alert_without_repeated_history(monkeypatch):
    store, run, item = question(monkeypatch)
    def fail(*args, **kwargs):
        raise transport.FleetError("unavailable")
    monkeypatch.setattr(transport, "call", fail)
    open_decisions(store).answer(item.id, "Proceed", actor="adi")
    execution = open_execution(store)
    for _ in range(4):
        execution.retry_deliveries("carbon")
    alerts = [item for item in open_attention(store).list() if item.kind == "alert"]
    assert len(alerts) == 1
    assert alerts[0].run == run.id
    sequence = store.latest_sequence()
    execution.retry_deliveries("carbon")
    assert store.latest_sequence() == sequence


def test_unchanged_offline_delivery_heartbeat_does_not_write(monkeypatch):
    store, run, item = question(monkeypatch)
    def fail(*args, **kwargs):
        raise transport.FleetError("offline")
    monkeypatch.setattr(transport, "call", fail)
    open_decisions(store).answer(item.id, "Proceed", actor="adi")
    state = FleetState([transport.Host("carbon", None)], store=store)
    apply_message(state, state.hosts[0], {"type": "hello"})
    sequence = store.latest_sequence()
    apply_message(state, state.hosts[0], {"type": "heartbeat"})
    change, = store.history_after(sequence)
    assert change["subject"] == "execution:host:carbon"
    sequence, version = store.latest_sequence(), state.version
    apply_message(state, state.hosts[0], {"type": "heartbeat"})
    assert (store.latest_sequence(), state.version) == (sequence, version)


def test_runtime_hook_question_finds_linked_run_and_rolls_back_delivery_intent(monkeypatch):
    store, run, item = question(monkeypatch)
    attention = open_attention(store)
    hook = attention.raise_item(project="p", kind="decision", owner="user", subject="job:carbon:job1",
        source="runtime-input:carbon", source_reference="hook", headline="Permission needed",
        context_reference="request:2", actor="fleetd",
        stream_context=StreamContext("carbon", "job", "job1", "p", "p", "hook", "Permission", 1))
    sequence = store.latest_sequence()
    original = ExecutionRepository.save_delivery
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("rollback")
    with monkeypatch.context() as patch:
        patch.setattr(ExecutionRepository, "save_delivery", fail)
        with pytest.raises(RuntimeError, match="rollback"):
            open_decisions(store).answer(hook.id, "Proceed", actor="adi")
    assert store.latest_sequence() == sequence
    assert attention.get(hook.id).state == "open"
    assert open_decisions(store).list() == []
    assert open_execution(store).deliveries() == []
    monkeypatch.setattr(transport, "call", lambda host, args, **kw:
        {"schema_version": 1, "key": args[args.index("--key") + 1], "status": "applied"})
    open_decisions(store).answer(hook.id, "Proceed", actor="adi")
    assert open_execution(store).deliveries()[0].run == run.id
