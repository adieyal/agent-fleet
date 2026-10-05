"""A run's latest reading is kept without history, so the audit trail holds only its state changes."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


from fleet.container import configured_container
from fleet.infrastructure.sqlite import store as sqlite_store
from fleet.modules.execution import JobObservation, Run, Usage

START = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
USAGE = {"source": "codex", "reports": [{"input_tokens": 10}]}


def p2_migration() -> int:
    """The position of the migration that moved run observations out of the run record."""
    return next(index for index, statements in enumerate(sqlite_store.MIGRATIONS)
                if any("CREATE TABLE execution_run_observation" in statement for statement in statements))


def dispatched(store):
    workspace = configured_container(store).initialized_workspace()
    project = workspace.move_in(["one"], "demo").project_id
    item = configured_container(store).work().add(project=project, title='Task', goal='Ship', actor='user')
    execution = configured_container(store).execution()
    run = execution.dispatch(item.id, host="one", runtime="codex", payload={"cwd": "/repo", "steps": ["Ship"]},
                             actor="user", reason="manual", idempotency_key="request").run
    return execution, run


def run_history(store, run: Run) -> list[dict]:
    return [entry for entry in store.history_after(0) if entry["subject"] == f"execution:run:{run.id}"]


def test_activity_and_observation_time_leave_no_history():
    store = configured_container().store()
    execution, run = dispatched(store)
    execution.observe(run.host, JobObservation(run.remote_job_id, "running", "codex", START, None, START))
    before = run_history(store, run)
    for minute in range(100):
        at = START + timedelta(minutes=minute)
        observed = execution.observe(run.host, JobObservation(
            run.remote_job_id, "running", "codex", START, None, at, Usage(**{**USAGE, "reports": [{"n": minute}]}),
            "tool" if minute % 2 else "text", at))
    assert run_history(store, run) == before
    assert execution.get_run(run.id) == observed
    assert (observed.current_action, observed.action_observed_at, observed.last_observed) == (
        "tool", START + timedelta(minutes=99), START + timedelta(minutes=99))
    assert observed.usage.reports == [{"n": 99}]


def test_state_changes_keep_their_history_and_a_finished_run_keeps_its_usage():
    store = configured_container().store()
    execution, run = dispatched(store)
    execution.observe(run.host, JobObservation(run.remote_job_id, "running", "codex", START, None, START))
    before = len(run_history(store, run))
    end = START + timedelta(minutes=5)
    finished = execution.observe(run.host, JobObservation(run.remote_job_id, "done", "codex", START, end, end,
                                                          Usage(**USAGE)))
    entries = run_history(store, run)
    assert len(entries) == before + 1
    recorded = json.loads(entries[-1]["to"])
    assert (recorded["status"], recorded["usage"]) == ("succeeded", USAGE)
    assert not {"last_observed", "current_action", "action_observed_at"} & recorded.keys()
    assert execution.get_run(run.id) == finished


def test_unrecorded_writes_outside_observations_are_still_refused():
    store = configured_container().store()
    execution, run = dispatched(store)
    with pytest.raises(ValueError, match="history entry"):
        with store.unit_of_work() as unit:
            unit.record_observation("UPDATE execution_run_observation SET record = '{}' WHERE run = ?", (run.id,))
            unit.connection.execute("UPDATE execution_run SET record = record WHERE id = ?", (run.id,))


def test_new_run_fields_default_for_a_job_and_reject_unknown_kinds():
    run = Run("r", "a", "one", "job", None, "running", None, None, None, None)
    assert (run.kind, run.label, run.title, run.cwd, run.workspace, run.workspace_reason) == (
        "job", None, None, None, None, None)
    with pytest.raises(ValueError, match="kind"):
        Run("r", "a", "one", "job", None, "running", None, None, None, None, kind="pipeline")


def legacy_run(identity: str, status: str, usage: dict | None) -> dict:
    return {"id": identity, "action": "action", "host": "one", "remote_job_id": identity, "runtime": "codex",
            "status": status, "reason": None, "start": START.isoformat(), "end": None,
            "last_observed": START.isoformat(), "usage": usage, "current_action": "tool",
            "action_observed_at": START.isoformat(), "step_work": None}


def test_migration_moves_observations_out_of_existing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "controller.db"
    migrations = sqlite_store.MIGRATIONS
    position = p2_migration()
    monkeypatch.setattr(sqlite_store, "MIGRATIONS", migrations[:position])
    store = configured_container(path=path).store()
    runs = {"live": legacy_run("live", "running", USAGE), "ended": legacy_run("ended", "succeeded", USAGE)}
    with store.unit_of_work() as unit:
        unit.connection.execute("INSERT INTO execution_action (id, record) VALUES ('action', '{}')")
        for identity, record in runs.items():
            unit.connection.execute("INSERT INTO execution_run (id, action, host, remote_job_id, record) "
                                    "VALUES (?, 'action', 'one', ?, ?)", (identity, identity, json.dumps(record)))
        unit.record_change("execution:action:action", "", "{}", "test")
    sequence = store.latest_sequence()

    monkeypatch.setattr(sqlite_store, "MIGRATIONS", migrations)
    store = configured_container(path=path).store()
    migrated = store.history_after(sequence)
    assert sorted(entry["subject"] for entry in migrated) == ["execution:run:ended", "execution:run:live"]
    assert {entry["actor"] for entry in migrated} == {"migration"}
    datetime.fromisoformat(migrated[0]["time"])
    with sqlite3.connect(path) as connection:
        records = dict(connection.execute("SELECT id, record FROM execution_run"))
    assert "usage" not in json.loads(records["live"]) and json.loads(records["ended"])["usage"] == USAGE
    assert not any(key in json.loads(record) for record in records.values()
                   for key in ("last_observed", "current_action", "action_observed_at"))
    execution = configured_container(store).execution()
    for run in execution.runs():
        assert (run.last_observed, run.current_action, run.action_observed_at, run.usage) == (
            START, "tool", START, Usage(**USAGE))
        assert run.kind == "job"
    # The first observation after the migration changes nothing, and writes nothing.
    sequence = store.latest_sequence()
    execution.observe("one", JobObservation("live", "running", "codex", START, None, START, Usage(**USAGE),
                                            "tool", START))
    assert store.latest_sequence() == sequence


def test_store_already_migrated_through_main_17_reopens_without_replaying(tmp_path, monkeypatch):
    """P2 integration uses main's 15/16/17 history, observations and ownership schema."""
    path = tmp_path / "main-17.db"
    migrations = sqlite_store.MIGRATIONS
    monkeypatch.setattr(sqlite_store, "MIGRATIONS", migrations[:17])
    store = configured_container(path=path).store()
    assert store.schema_version() == 17
    execution, run = dispatched(store)
    execution.observe(run.host, JobObservation(run.remote_job_id, "running", "codex", START, None, START))
    before = store.history_after(0)
    monkeypatch.setattr(sqlite_store, "MIGRATIONS", migrations)
    reopened = configured_container(path=path).store()
    assert reopened.schema_version() == 19
    assert reopened.history_after(0) == before
    assert configured_container(reopened).execution().get_run(run.id).last_observed == START
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM execution_run_observation").fetchone()[0] == 1
        columns = {row[1] for row in connection.execute("PRAGMA table_info(attention_item)")}
        assert {"subject", "page_annotation"} <= columns
        assert connection.execute("SELECT count(*) FROM attention_item WHERE page_annotation IS NOT NULL").fetchone()[0] == 0
