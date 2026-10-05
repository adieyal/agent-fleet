from datetime import datetime, timedelta, timezone

import pytest

from fleet.container import configured_container
from fleet_cli import cli
from fleet import transport
from fleet.transport import Host, HostReport
from fleet.services.live import FleetState, apply_message


def session(identity="session", status="working", updated=200):
    return {"id": identity, "status": status, "agent": "codex", "started_at": 100,
            "updated_at": updated, "project": "unregistered", "cwd": "/work", "title": "Plan"}


def test_sessions_stop_reopen_and_keep_identity_without_claims():
    store = configured_container().store()
    execution = configured_container(store).execution()
    first = execution.observe_session("carbon", session())
    assert first.kind == "session" and first.status == "running"
    assert execution.repository.get_action(first.action).source == "session"
    assert execution.repository.claims() == []
    sequence = store.latest_sequence()
    execution.observe_session("carbon", session(status="idle", updated=201))
    assert store.latest_sequence() == sequence
    stopped = execution.stop_session("carbon", "session")
    assert stopped.end.timestamp() == 201 and stopped.reason == "quiet for 20 min"
    reopened = execution.observe_session("carbon", session(updated=1500))
    assert reopened.id == first.id and reopened.status == "running" and reopened.end is None
    assert execution.observe_session("home", session()).id != first.id
    restarted = configured_container(configured_container(path=store.path).store()).execution()
    assert restarted.repository.find("carbon", "session") == reopened


def test_linking_a_session_attaches_existing_action_and_survives_moves():
    store = configured_container().store()
    work = configured_container(store).work()
    item = work.add(project="p", title="Task", goal="Ship", actor="user")
    execution = configured_container(store).execution()
    run = execution.observe_session("carbon", session(), "p")
    linked = execution.link("carbon", "session", item.id, actor="user")
    assert linked.id == run.id and execution.repository.get_action(run.action).work_item == item.id
    execution.observe_session("carbon", {**session(), "project": "moved"}, None)
    assert execution.repository.get_action(run.action).project == "p"
    assert execution.repository.claims() == [] and work.get(item.id) == item


def test_offline_restart_retains_work_until_first_heartbeat(monkeypatch):
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    store = configured_container(clock=lambda : now[0]).store()
    host = Host("carbon", None)
    state = FleetState([host], container=configured_container(store=store))
    apply_message(state, host, {"type": "hello"})
    apply_message(state, host, {"type": "session", "session": session()})
    apply_message(state, host, {"type": "job", "job": {"id": "job", "status": "running", "steps": [], "created_at": 100}})
    apply_message(state, host, {"type": "heartbeat"})
    sequence = store.latest_sequence()
    now[0] += timedelta(minutes=5)
    apply_message(state, host, {"type": "heartbeat"})
    assert store.latest_sequence() == sequence
    apply_message(state, host, {"type": "error", "error": "ssh unavailable"})
    original = state.document()["hosts"][0]
    assert original["jobs"][0]["stale"] and original["sessions"][0]["stale"]
    restarted = FleetState([host], container=configured_container(store=configured_container(path=store.path, clock=lambda : now[0]).store()))
    offline = restarted.document()["hosts"][0]
    assert offline["down_since"] == original["down_since"]
    assert {item["id"] for item in offline["jobs"]} == {"job"}
    assert {item["id"] for item in offline["sessions"]} == {"session"}
    since = []
    monkeypatch.setattr(transport, "catch_up_sessions", lambda host, lower: since.append(lower) or [session("missed", "stopped")])
    apply_message(restarted, host, {"type": "hello"})
    assert restarted.document()["hosts"][0]["sessions"][0]["stale"]
    assert since == ["2026-10-01T00:05:00+00:00"]
    assert restarted.execution.repository.find("carbon", "missed").status == "stopped"
    apply_message(restarted, host, {"type": "heartbeat"})
    online = restarted.document()["hosts"][0]
    assert online["jobs"] == online["sessions"] == [] and "down_since" not in online
    assert restarted.execution.repository.find("carbon", "session").status == "stopped"
    transitions = [entry for entry in store.history_after(0) if entry["subject"] == "execution:host:carbon"]
    assert len(transitions) == 3


def test_notify_reports_each_outage_once_and_recovery(monkeypatch, capsys, *, cli_container, override_cli_method):
    host = Host("carbon", None)
    reports = iter([[HostReport(host, [], "ssh unavailable")], [HostReport(host, [], "ssh unavailable")],
                    [HostReport(host, [], None)]])
    override_cli_method('jobs', 'selected_hosts', lambda args: [host])
    monkeypatch.setattr(transport, "gather", lambda *args: next(reports))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    with pytest.raises(StopIteration):
        cli.command_notify(cli.argparse.Namespace(interval=1), container=cli_container)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("HOST DOWN carbon since ") and lines[0].endswith(": ssh unavailable")
    assert lines[1] == "HOST UP carbon"
