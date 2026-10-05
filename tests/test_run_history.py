import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from fleet import cli, composition
from fleet.errors import FleetError
from fleet.modules.execution import JobObservation
from fleet.projections.run_history import history_runs, run_detail
from fleet.web.fixture import FixtureLibrary
from fleet.web.server import FleetState, make_handler


@pytest.fixture
def history():
    now = datetime.now(timezone.utc)
    store = composition.open_store()
    workspace = composition.open_workspace(store)
    project = workspace.edit_registry(lambda registry: registry.create("History")).id
    other = workspace.edit_registry(lambda registry: registry.create("Other")).id
    work = composition.open_work(store)
    root = work.add(project=project, title="Epic", goal="Ship", kind="epic", actor="user")
    child = work.add(project=project, parent=root.id, title="Child", goal="Ship", actor="user")
    execution = composition.open_execution(store)
    job = execution.record_observed("carbon", {"id": "job", "run_id": "aabb-1", "description": "Failed job"}, project)
    execution.link("carbon", "job", root.id, actor="user")
    job = execution.observe("carbon", JobObservation("job", "failed", "codex", now - timedelta(days=1), now, now))
    session = execution.observe_session("home", {"id": "session", "status": "idle", "started_at": now.timestamp(),
        "updated_at": now.timestamp(), "project": "History", "agent": "claude"}, project)
    execution.link("home", "session", child.id, actor="user")
    unlinked = execution.record_observed("carbon", {"id": "old", "run_id": "aabb-2"}, other)
    execution.observe("carbon", JobObservation("old", "done", "codex", now - timedelta(days=3), now, now))
    missing = execution.record_observed("gpu", {"id": "unknown"}, None)
    return store, workspace, work, execution, root, child, job, session, unlinked, missing, now


def filters(history, **options):
    _, workspace, work, execution, *_, now = history
    return history_runs(execution, work, workspace, now=now, **options)


@pytest.mark.parametrize("options,indices", [
    ({"project": "History"}, (6, 7)), ({"host": "home"}, (7,)),
    ({"status": "failed,succeeded"}, (6, 8)), ({"kind": "session"}, (7,)),
    ({"unlinked": True}, (8, 9)), ({"since": "2d"}, (6, 7)),
    ({"since": "1h"}, (7,)), ({"since": "1m"}, (7,)),
    ({"host": "absent"}, ()),
])
def test_filters(history, options, indices):
    result = filters(history, **options)
    assert {run["id"] for run in result["runs"]} == {history[index].id for index in indices}
    assert result["total"] == len(indices)
    assert bool(result["empty_reason"]) == (not indices)


def test_work_descendants_dates_order_and_limit(history):
    assert filters(history, work_item=history[4].id)["total"] == 1
    assert filters(history, work_item=history[4].id, descendants=True)["total"] == 2
    assert filters(history, work_item=history[5].id)["runs"][0]["id"] == history[7].id
    result = filters(history, limit=1)
    assert result["total"] == 4 and result["runs"][0]["id"] == history[7].id
    until = (history[-1] - timedelta(days=2)).isoformat()
    assert filters(history, until=until)["runs"][0]["id"] == history[8].id
    assert filters(history, since=history[6].start.isoformat(), until=history[6].start.isoformat())["total"] == 1


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": -1}, {"kind": "other"}, {"status": "lost"},
    {"since": "garbage"}, {"until": "garbage"}, {"descendants": True}, {"project": "missing"},
    {"since": "2099-01-01", "until": "2000-01-01"}])
def test_invalid_filters_are_refused(history, options):
    with pytest.raises((ValueError, LookupError, FleetError)):
        filters(history, **options)


def test_cli_json_footer_empty_state_and_run_details(history, capsys):
    cli.main(["history", "runs", "--limit", "1", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == 4 and len(result["runs"]) == 1
    cli.main(["history", "runs", "--limit", "1"])
    assert "1 of 4 runs; --limit to see more" in capsys.readouterr().out
    cli.main(["history", "runs", "--host", "absent"])
    assert "No stored runs match these filters." in capsys.readouterr().out
    cli.main(["run", "show", "aabb-1", "--json"])
    detail = json.loads(capsys.readouterr().out)
    assert detail["run"]["id"] == "aabb-1" and detail["action"]["work_item"] == history[4].id
    assert detail["trace"]["events"]["reason"] == "not recorded for this run"
    with pytest.raises(ValueError, match="matches 2 runs"):
        run_detail("aabb", history[3], history[2], composition.open_library(history[0]))
    with pytest.raises(LookupError):
        run_detail("missing", history[3], history[2], composition.open_library(history[0]))


@pytest.mark.parametrize("arguments,options", [(["--project", "History"], {"project": "History"}),
    (["--host", "home"], {"host": "home"}), (["--status", "failed,succeeded"], {"status": "failed,succeeded"}),
    (["--kind", "session"], {"kind": "session"}), (["--unlinked"], {"unlinked": True}),
    (["--since", "2d"], {"since": "2d"}), (["--until", "2000-01-01"], {"until": "2000-01-01"}),
    (["--limit", "1"], {"limit": 1})])
def test_cli_filters_match_projection(history, capsys, arguments, options):
    cli.main(["history", "runs", *arguments, "--json"])
    value = json.loads(capsys.readouterr().out)
    expected = filters(history, **options)
    assert value["total"] == expected["total"]
    assert [run["id"] for run in value["runs"]] == [run["id"] for run in expected["runs"]]


def test_run_detail_retains_steps_git_and_unlinked_documents(history, capsys, api):
    from fleet.web.job_store import ProjectDocuments
    execution = history[3]
    run = history[8]
    git = {"base": "a" * 40, "head": "b" * 40, "commit_count": 1,
           "commits": [{"sha": "b" * 40, "subject": "Ship"}], "pushes": [{"ref": "origin/main"}]}
    execution.observe_steps(run.id, [{"index": 0, "title": "Ship", "git": git, "status": "done"}])
    documents = ProjectDocuments()
    scope = documents.label_scope(run.host, run.label or "")
    document = {"id": "REPORT.md", "name": "Report", "kind": "report", "mtime": 1, "size": 4}
    documents.observe(scope, run.host, {"id": run.remote_job_id, "documents": [document]})
    documents.keep(scope, run.host, run.remote_job_id, document, "Done")
    cli.main(["run", "show", run.id, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["steps"][0]["git"] == git and result["run"]["commit_count"] == 1
    assert result["run"]["push_count"] == 1
    assert result["kept_documents"][0]["stored"] and result["kept_documents"][0]["id"] == "REPORT.md"
    status, listing = get(api, "/api/history/runs", host=run.host)
    assert status == 200
    entry = next(entry for entry in listing["runs"] if entry["id"] == run.id)
    assert entry["document_count"] == 1
    stored_detail = get(api, "/api/runs/" + run.id)[1]
    assert stored_detail["kept_documents"][0]["stored"]
    cli.main(["history", "runs", "--work-item", history[4].id, "--descendants", "--json"])
    assert json.loads(capsys.readouterr().out)["total"] == 2


def test_old_linked_action_resolves_project_through_work(history):
    execution = history[3]
    run = execution.link("legacy", "job", history[4].id, actor="user")
    assert execution.get_action(run.action).project is None
    assert run.id in {entry["id"] for entry in filters(history, project="History")["runs"]}


def test_work_filter_includes_a_step_serving_the_item(history):
    run = history[6]
    history[3].observe("carbon", JobObservation("job", "failed", "codex", run.start, run.end, history[-1],
        step_work=[{"index": 0, "work_item": history[5].id, "status": "done",
                    "start": run.start.isoformat(), "end": run.end.isoformat()}]))
    result = filters(history, work_item=history[5].id)
    assert {entry["id"] for entry in result["runs"]} == {history[6].id, history[7].id}


@pytest.fixture
def api(history):
    state = FleetState([], store=history[0])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, FixtureLibrary({"hosts": []})))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def get(api, path, **query):
    try:
        with urlopen(api + path + "?" + urlencode(query), timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


@pytest.mark.parametrize("query,options", [({"project": "History"}, {"project": "History"}),
    ({"host": "home"}, {"host": "home"}), ({"status": "failed"}, {"status": "failed"}),
    ({"kind": "session"}, {"kind": "session"}), ({"unlinked": "true"}, {"unlinked": True}),
    ({"since": "2d"}, {"since": "2d"}), ({"until": "2000-01-01"}, {"until": "2000-01-01"}),
    ({"limit": 1}, {"limit": 1})])
def test_api_filters_match_projection(api, history, query, options):
    status, value = get(api, "/api/history/runs", **query)
    expected = filters(history, **options)
    assert status == 200 and value["total"] == expected["total"]
    assert [run["id"] for run in value["runs"]] == [run["id"] for run in expected["runs"]]


def test_api_descendants_detail_and_errors(api, history):
    status, result = get(api, "/api/history/runs", work_item=history[4].id, descendants="true")
    assert status == 200 and result["total"] == 2
    assert get(api, "/api/runs/aabb-1")[1]["action"]["work_item"] == history[4].id
    assert get(api, "/api/runs/missing")[0] == 404
    assert get(api, "/api/runs/aabb")[0] == 400
    for query in ({"limit": "bad"}, {"unlinked": "yes"}, {"unknown": "x"}, {"kind": "bad"},
                  {"since": "bad"}, {"until": "bad"}, {"status": "bad"}, {"project": "missing"}):
        assert get(api, "/api/history/runs", **query)[0] == 400
