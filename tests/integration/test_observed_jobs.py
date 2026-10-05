"""Jobs seen outside dispatch retain one identity through catch-up and later linking."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Barrier

import pytest

from fleet.container import configured_container
from fleet import transport
from fleet.ingestion import observe_runs

from fleet.services.live import FleetState, apply_message

real_catch_up_jobs = transport.catch_up_jobs
real_fetch_raw = FleetState.fetch_raw


def job(**changes):
    return {"id": "remote-job", "run_id": "foreign-run", "project": "label", "agent": "codex",
            "description": "Record jobs", "cwd": "/repo", "status": "done", "updated_at": 20,
            "workspace": {"branch": "feat/history", "head": "abc"},
            "steps": [{"index": 0, "status": "done", "started_at": 10, "finished_at": 20}],
            "documents": [], **changes}


def test_ingester_records_unlinked_job_once_with_workspace_and_foreign_id():
    store = configured_container().store()
    execution = configured_container(store).execution()
    library = configured_container(store).library()
    host = {"name": "carbon", "ok": True, "jobs": {"remote-job": job()}}
    observe_runs(execution, library, host)
    sequence = store.latest_sequence()
    observe_runs(execution, library, host)
    assert store.latest_sequence() == sequence
    run, = execution.runs()
    action, = execution.actions()
    assert (run.id, run.status, run.runtime, run.label, run.title, run.cwd, run.workspace) == (
        "foreign-run", "succeeded", "codex", "label", "Record jobs", "/repo",
        {"branch": "feat/history", "head": "abc"})
    assert (action.source, action.work_item, action.project) == ("observed", None, None)
    assert execution.claims() == []
    assert configured_container(configured_container(path=store.path).store()).execution().get_run(run.id) == run


def test_concurrent_observers_create_one_action_and_run():
    store = configured_container().store()
    ready = Barrier(2)

    def record(_):
        execution = configured_container(store).execution()
        ready.wait()
        return execution.record_observed("carbon", job()).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(record, range(2))) == ["foreign-run", "foreign-run"]
    execution = configured_container(store).execution()
    assert len(execution.actions()) == len(execution.runs()) == 1
    assert execution.claims() == []


def test_later_reports_refresh_workspace_without_replacing_identity():
    execution = configured_container().execution()
    first = execution.record_observed("carbon", job())
    updated = execution.record_observed("carbon", job(workspace={"head": "new"}, description="Updated title"))
    assert (updated.id, updated.action) == (first.id, first.action)
    assert updated.workspace == {"head": "new"} and updated.title == "Updated title"
    assert len(execution.actions()) == 1


def test_attach_preserves_run_and_audits_link_then_refuses_relink(project_id):
    store = configured_container().store()
    execution = configured_container(store).execution()
    work = configured_container(store).work()
    task = work.add(project=project_id, title="Task", goal="Ship", actor="user")
    other = work.add(project=project_id, title="Other", goal="Ship", actor="user")
    run = execution.record_observed("carbon", job())
    before = store.latest_sequence()
    assert execution.link("carbon", "remote-job", task.id, actor="codex") == run
    action = execution.get_action(run.action)
    assert (action.work_item, action.project, action.source) == (task.id, project_id, "observed")
    history = store.history_after(before)
    assert len(history) == 1 and history[0]["actor"] == "codex"
    assert history[0]["subject"] == f"execution:action:{action.id}"
    assert execution.link("carbon", "remote-job", task.id, actor="codex") == run
    with pytest.raises(ValueError, match="already linked"):
        execution.link("carbon", "remote-job", other.id, actor="codex")
    assert len(execution.runs()) == 1 and execution.claims() == []


def test_mismatched_projects_are_named_and_link_is_unchanged(project_id):
    store = configured_container().store()
    workspace = configured_container(store).initialized_workspace()
    other = workspace.edit_registry(lambda registry: registry.create("Other project"))
    task = configured_container(store).work().add(project=other.id, title='Task', goal='Ship', actor='user')
    execution = configured_container(store).execution()
    run = execution.record_observed("carbon", job(), project_id)
    before = store.latest_sequence()
    with pytest.raises(ValueError) as caught:
        execution.link("carbon", "remote-job", task.id, actor="codex")
    assert f"p ({project_id})" in str(caught.value)
    assert f"Other project ({other.id})" in str(caught.value)
    assert store.latest_sequence() == before
    assert execution.get_action(run.action).work_item is None


def test_project_link_assigns_only_matching_label_and_moves_retained_documents(project_id, tmp_path, capsys):
    from fleet_cli import cli
    config = os.environ["FLEET_CONFIG"]
    with open(config, "w") as handle:
        json.dump({"hosts": {"carbon": {"ssh": None}}}, handle)

    store = configured_container().store()
    execution = configured_container(store).execution()
    run = execution.record_observed("carbon", job())
    different = execution.record_observed("home", job(id="other", run_id="other-run"))
    documents = configured_container().documents()
    scope = documents.label_scope("carbon", "label")
    report = {"id": "report", "kind": "report", "name": "Report", "path": "/report.md", "mtime": 1, "size": 5}
    keeper = configured_container().document_keeper(documents, lambda *args: {'content': 'Proof'})
    keeper.copy("carbon", scope, job(documents=[report]))
    assert (documents.root / "_labels/carbon/label/jobs/carbon-remote-job/report.md").read_text() == "Proof"
    cli.main(["project", "link", project_id, "carbon:label"])
    assert "1 earlier runs assigned; 1 retained jobs moved" in capsys.readouterr().out
    assert execution.get_action(run.action).project == project_id
    assert execution.get_action(different.action).project is None
    assert documents.text(project_id, "carbon-remote-job", "report") == "Proof"
    # A copy queued under the old label also writes to the project's retained job.
    keeper.copy("carbon", scope, job(documents=[{**report, "mtime": 2}]))
    assert documents.jobs(project_id)[0]["project_id"] == project_id
    assert documents.projects() == [project_id]
    assert documents.assign_label("carbon", "label", project_id) == 0
    assert execution.assign_label("carbon", "label", project_id, actor="codex") == 0


def test_hello_catches_up_old_finish_and_registered_project(monkeypatch, project_id):
    store = configured_container().store()
    workspace = configured_container(store).initialized_workspace()
    workspace.edit_registry(lambda registry: registry.link(project_id, "carbon", "label"))
    host = transport.Host("carbon", None)
    state = FleetState([host], container=configured_container(store=store))
    calls = []

    def call(worker, arguments, **kwargs):
        calls.append((worker.name, arguments))
        return {"jobs": [job()]}

    monkeypatch.setattr(transport, "call", call)
    monkeypatch.setattr(transport, "catch_up_jobs", real_catch_up_jobs)
    apply_message(state, host, {"type": "hello"})
    run, = state.execution.runs()
    assert run.status == "succeeded" and run.end.timestamp() == 20
    assert state.execution.get_action(run.action).project == project_id
    assert calls == [("carbon", ["ls", "--all", "--events", "0"])]
    apply_message(state, host, {"type": "hello"})
    assert len(state.execution.runs()) == 1


def test_real_worker_catch_up_keeps_a_finish_older_than_stream_horizon(monkeypatch, tmp_path):
    from fleet_worker import fleetd

    monkeypatch.setenv("FLEET_FLEETD_PATH", fleetd.__file__)
    monkeypatch.delenv("FLEET_REMOTE_HOME", raising=False)
    monkeypatch.setattr(transport, "catch_up_jobs", real_catch_up_jobs)
    directory = Path(os.environ["FLEET_HOME"]) / "jobs" / "remote-job"
    directory.mkdir(parents=True)
    definition = job(created_at=1, permission="default", cancelled=False, runner_pid=None)
    definition["steps"][0].update(title="Finished long ago", prompt="Record jobs", result="Done")
    (directory / "job.json").write_text(json.dumps(definition))
    host = transport.Host("carbon", None)
    state = FleetState([host], container=configured_container())
    state.keeper.fetch = lambda host, job, document: real_fetch_raw(state, host, job, document)
    apply_message(state, host, {"type": "hello"})
    run, = state.execution.runs()
    assert (run.id, run.status, run.end.timestamp()) == ("foreign-run", "succeeded", 20)
    assert state.keeper.settle(5)


def test_document_move_merges_a_concurrent_project_copy_without_losing_files(project_id):
    documents = configured_container().documents()
    scope = documents.label_scope("carbon", "label")
    report = {"id": "report", "kind": "report", "name": "Report", "path": "/report.md", "mtime": 1, "size": 3}
    older = configured_container().document_keeper(documents, lambda *args: {'content': 'Old'})
    newer = configured_container().document_keeper(documents, lambda *args: {'content': 'New'})
    older.copy("carbon", scope, job(documents=[report]))
    newer.copy("carbon", project_id, job(documents=[{**report, "mtime": 2}]))
    assert documents.assign_label("carbon", "label", project_id) == 1
    assert documents.text(project_id, "carbon-remote-job", "report") == "New"
    directory = documents.job_directory(project_id, "carbon", "remote-job")
    assert any(path.read_text() == "Old" for path in directory.glob("*.md"))
    assert len(documents.jobs(project_id)) == 1


def test_stream_keeps_unregistered_label_documents(monkeypatch):
    state = FleetState([transport.Host('carbon', None)], container=configured_container())
    report = {"id": "report", "kind": "report", "name": "Report", "path": "/report.md", "mtime": 1, "size": 5}
    state.keeper.fetch = lambda *args: {"content": "Proof"}
    apply_message(state, state.hosts[0], {"type": "hello"})
    apply_message(state, state.hosts[0], {"type": "job", "job": job(documents=[report])})
    assert state.keeper.settle(5)
    scope = state.documents.label_scope("carbon", "label")
    assert state.documents.text(scope, "carbon-remote-job", "report") == "Proof"
    assert state.documents.jobs(scope)[0]["project_id"] is None


def test_catch_up_failure_is_visible_and_stream_still_records_job(monkeypatch, capsys):
    state = FleetState([transport.Host('carbon', None)], container=configured_container())

    def failed(host):
        raise transport.FleetError("offline during catch-up")

    monkeypatch.setattr(transport, "catch_up_jobs", failed)
    apply_message(state, state.hosts[0], {"type": "hello"})
    assert "catch-up on carbon failed: offline during catch-up" in capsys.readouterr().err
    apply_message(state, state.hosts[0], {"type": "job", "job": job()})
    assert len(state.execution.runs()) == 1
