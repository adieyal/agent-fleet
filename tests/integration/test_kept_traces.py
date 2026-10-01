import argparse
import json
from pathlib import Path

import pytest

from fleet import cli, composition, transport
from fleet.remote import fleetd
from fleet.transport import FleetError, Host
from fleet.web.server import FleetState, apply_message


KEEP_TRACE = transport.keep_run_trace


def job(status="done"):
    return {"id": "job", "status": status, "steps": [], "created_at": 100, "updated_at": 200,
            "trace": {"path": "/worker/events.jsonl", "availability": "available",
                      "raw": [{"path": "/worker/raw-0.jsonl", "availability": "available"}]}}


def test_terminal_trace_is_complete_idempotent_and_survives_rm(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(transport, "keep_run_trace", KEEP_TRACE)
    content = ''.join(json.dumps({"kind": "tool", "summary": str(index)}) + '\n' for index in range(100))
    calls = []
    def worker(host, arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "read-trace":
            return {"content": content, "reason": None}
        if arguments[0] == "show":
            return job()
        assert arguments == ["rm", "job"]
        return {"removed": "job"}
    monkeypatch.setattr(transport, "call", worker)
    host = Host("carbon", None)
    store = composition.open_store()
    state = FleetState([host], store=store)
    apply_message(state, host, {"type": "hello"})
    apply_message(state, host, {"type": "job", "job": job()})
    assert state.keeper.settle(5)
    run, = state.execution.runs()
    trace = state.execution.trace(run.id)
    assert trace["events"]["content"] == content and trace["events"]["bytes"] == len(content.encode())
    assert Path(trace["events"]["path"]).is_relative_to(tmp_path)
    sequence = store.latest_sequence()
    apply_message(state, host, {"type": "job", "job": job()})
    assert state.keeper.settle(5)
    assert store.latest_sequence() == sequence and calls == [["read-trace", "job"]]
    monkeypatch.setattr(cli, "resolve", lambda reference: (host, "job"))
    cli.command_remove(argparse.Namespace(job="carbon:job"))
    kept = composition.open_execution(composition.open_store(store.path)).trace(run.id)
    assert kept["events"]["content"] == content
    assert kept["source"]["availability"] == "removed by fleet rm"
    assert kept["source"]["raw"][0]["availability"] == "removed by fleet rm"
    cli.main(["run", "show", run.id, "--json"])
    capsys.readouterr()
    cli.main(["store", "usage", "--json"])
    usage = json.loads(capsys.readouterr().out)
    assert usage["traces"]["files"] == 1 and usage["traces"]["bytes"] == len(content.encode())
    assert usage["tables"]["execution_run"] == 1


def test_missing_trace_retry_and_running_trace_is_not_fetched(monkeypatch):
    execution = composition.open_execution()
    host = Host("carbon", None)
    calls = []
    def fail(host, arguments, **kwargs):
        calls.append(arguments)
        raise FleetError("offline")
    monkeypatch.setattr(transport, "call", fail)
    KEEP_TRACE(execution, host, job("running"))
    assert calls == []
    run, = execution.runs()
    KEEP_TRACE(execution, host, job())
    assert execution.trace(run.id)["events"]["reason"] == "trace copy failed: offline"
    monkeypatch.setattr(transport, "call", lambda *args, **kwargs: {"content": "", "reason": None})
    KEEP_TRACE(execution, host, job())
    assert execution.trace(run.id)["events"]["availability"] == "kept"
    assert execution.trace(run.id)["events"]["content"] == ""  # an empty file is a kept empty trace
    path = Path(execution.trace(run.id)["events"]["path"])
    path.unlink()
    assert execution.trace(run.id)["events"]["reason"] == "retained trace file is missing or corrupt"


def test_worker_removal_provenance_updates_streamed_run_without_deleting_history(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "jobs" / "job"
    directory.mkdir(parents=True)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", directory.parent)
    monkeypatch.setattr(fleetd, "derive_status", lambda value: "done")
    (directory / "job.json").write_text('{"id": "job"}')
    (directory / "raw-0.jsonl").write_text("raw")
    fleetd.command_remove(argparse.Namespace(job="job"))
    removal = json.loads(capsys.readouterr().out)
    assert not directory.exists() and removal["reason"] == "removed by fleet rm"
    manifest, = fleetd.REMOVALS_DIRECTORY.glob("*.json")
    assert json.loads(manifest.read_text())["removed_at"] == removal["removed_at"]
    observed = {}
    message, = fleetd.removal_messages(observed)
    assert message["id"] == "job" and message["reason"] == "removed by fleet rm"
    assert list(fleetd.removal_messages(observed)) == []
    assert list(fleetd.removal_messages({})) == [message]  # a fresh stream catches up removals while offline
    store = composition.open_store()
    state = FleetState([Host("carbon", None)], store=store)
    run = state.execution.record_observed("carbon", job())
    state.execution.record_trace(run.id, job()["trace"], content="kept\n")
    apply_message(state, state.hosts[0], message)
    trace = state.execution.trace(run.id)
    assert trace["events"]["content"] == "kept\n" and trace["source"]["removed_at"]
    assert trace["source"]["availability"] == "removed by fleet rm"
    assert state.execution.get_run(run.id).id == run.id
    sequence = store.latest_sequence()
    apply_message(state, state.hosts[0], message)
    assert store.latest_sequence() == sequence


def test_worker_read_trace_reads_every_event_and_refuses_path_escape(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    directory = tmp_path / "jobs" / "job"
    directory.mkdir(parents=True)
    (directory / "job.json").write_text("{}")
    content = ''.join(json.dumps({"kind": "tool", "summary": str(index)}) + '\n' for index in range(100))
    (directory / "events.jsonl").write_text(content)
    fleetd.command_read_trace(argparse.Namespace(job="job"))
    assert json.loads(capsys.readouterr().out)["content"] == content
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "job.json").write_text("{}")
    (outside / "events.jsonl").write_text("private")
    with pytest.raises(SystemExit):
        fleetd.command_read_trace(argparse.Namespace(job="../outside"))
    (directory / "events.jsonl").unlink()
    (directory / "events.jsonl").symlink_to(outside / "events.jsonl")
    with pytest.raises(SystemExit):
        fleetd.command_read_trace(argparse.Namespace(job="job"))
    capsys.readouterr()
    (directory / "events.jsonl").unlink()
    fleetd.command_read_trace(argparse.Namespace(job="job"))
    assert json.loads(capsys.readouterr().out)["reason"] == "worker events.jsonl is missing"


def test_changed_terminal_trace_replaces_snapshot_and_keeps_old_copy(monkeypatch):
    execution = composition.open_execution()
    host = Host("carbon", None)
    current = ["one\n"]
    monkeypatch.setattr(transport, "call", lambda *args, **kwargs: {"content": current[0], "reason": None})
    KEEP_TRACE(execution, host, job())
    run, = execution.runs()
    old = execution.trace(run.id)["events"]
    current[0] = "one\ntwo\n"
    changed = job()
    changed["trace"]["size"] = len(current[0])
    KEEP_TRACE(execution, host, changed)
    latest = execution.trace(run.id)["events"]
    assert latest["content"] == current[0] and latest["sha256"] != old["sha256"]
    assert Path(old["path"]).read_text() == "one\n"


def test_new_copy_failure_keeps_old_content_and_pending_reports_preserve_removal(monkeypatch):
    store = composition.open_store()
    execution = composition.open_execution(store)
    host = Host("carbon", None)
    monkeypatch.setattr(transport, "call", lambda *args, **kwargs: {"content": "old\n", "reason": None})
    KEEP_TRACE(execution, host, job())
    run, = execution.runs()
    changed = job()
    changed["trace"]["size"] = 8
    def fail(*args, **kwargs):
        raise FleetError("offline")
    monkeypatch.setattr(transport, "call", fail)
    KEEP_TRACE(execution, host, changed)
    retained = execution.trace(run.id)
    assert retained["events"]["content"] == "old\n" and retained["copy_error"] == "trace copy failed: offline"
    execution.removed("carbon", "job", at=300)
    sequence = store.latest_sequence()
    KEEP_TRACE(execution, host, changed)
    assert execution.trace(run.id)["source"]["removed_at"] == "1970-01-01T00:05:00+00:00"
    assert store.latest_sequence() == sequence
