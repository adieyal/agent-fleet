"""Pipelines on the deck: fleetd's reports kept per host, declared rooms, and the pipeline SSE event."""
import json
import runpy
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from fleet import projects, transport
from fleet.transport import Host
from fleet.web.fixture import FixtureState
from fleet.web.server import FleetState, apply_message, make_handler, workspace_path
from fleet.workspace import WorkspaceStore

FIXTURES = Path(__file__).parent / "fixtures"
HOME = Host("home", None)
DECLARED = {"invoice-training": {"host": "home", "project": "invoice-training"}}
RUN = {"run_id": "r2", "label": "orient v4", "status": "running", "nodes": [["invoices"], ["decided"]],
       "edges": [["invoices", "decided", 3]], "counts": {"invoices": 3, "decided": 3}, "recent": {}}


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    return path


def online(state: FleetState) -> None:
    apply_message(state, HOME, {"type": "hello"})


def test_a_declared_pipeline_is_in_the_document_before_any_run(config_path) -> None:
    state = FleetState([HOME], load_registry=projects.load_registry, pipelines=DECLARED)
    [pipeline] = state.document()["pipelines"]
    assert pipeline == {"host": "home", "pipeline": "invoice-training", "project": "invoice-training",
                        "project_id": None, "declared": True, "host_ok": False, "host_error": "connecting…",
                        "run": None, "baseline": None, "seq": 0}


def test_reports_are_kept_per_host_and_pipeline(config_path) -> None:
    state = FleetState([HOME], load_registry=projects.load_registry, pipelines=DECLARED)
    online(state)
    version = state.version
    apply_message(state, HOME, {"type": "pipeline", "pipeline": "invoice-training", "run": RUN, "baseline": None})
    apply_message(state, HOME, {"type": "pipeline", "pipeline": "other", "run": {**RUN, "run_id": "o1"},
                                "baseline": None})
    assert state.version == version   # no new state document for a pipeline report
    declared, other = state.document()["pipelines"]
    assert (declared["run"], declared["host_ok"]) == (RUN, True)
    assert (other["pipeline"], other["declared"], other["project"]) == ("other", False, None)
    assert [p["pipeline"] for p in state.pipeline_updates(declared["seq"])] == ["other"]

    apply_message(state, HOME, {"type": "error", "error": "fleetd went away"})
    declared = state.document()["pipelines"][0]
    assert declared["run"] == RUN and not declared["host_ok"] and declared["host_error"] == "fleetd went away"


def test_a_declared_pipeline_resolves_its_room_to_a_registered_project(config_path) -> None:
    registry = projects.load_registry()
    project = registry.create("Invoice training")
    registry.link(project.id, "home", "invoice-training")
    projects.save_registry(registry)
    state = FleetState([HOME], load_registry=projects.load_registry, pipelines=DECLARED)
    assert state.document()["pipelines"][0]["project_id"] == project.id


def test_a_pipeline_label_moves_in_like_any_visitor(config_path, monkeypatch) -> None:
    """A room held only by a declared pipeline has no working directory to read remotes from; linking it to a
    project it may belong to is offered as for any label, and once linked the pipeline's room is that project's."""
    monkeypatch.setattr(transport, "repository_remotes", lambda host, directories: pytest.fail("no directories"))
    registry = projects.load_registry()
    project = registry.create("Invoice training")
    projects.save_registry(registry)
    state = FleetState([HOME], load_registry=projects.load_registry,
                       workspace=WorkspaceStore(workspace_path()), pipelines=DECLARED)
    online(state)
    offered = state.move_in_options("invoice-training", ["home"])
    assert [(c["project_id"], c["reasons"]) for c in offered["candidates"]] == [(project.id, ["name"])]
    assert offered["errors"] == []
    state.link_in(project.id, ["home"], "invoice-training")
    assert state.document()["pipelines"][0]["project_id"] == project.id


def test_pipeline_reports_stream_as_their_own_event(config_path) -> None:
    state = FleetState([HOME], load_registry=projects.load_registry, pipelines=DECLARED)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/stream", timeout=5) as stream:
            first = read_event(stream)
            assert first[0] == "state" and first[1]["pipelines"][0]["run"] is None
            state.report_pipeline("home", "invoice-training", RUN, None)
            kind, data = read_event(stream)
            assert kind == "pipeline"
            assert (data["pipeline"], data["project"], data["run"]) == ("invoice-training", "invoice-training", RUN)
    finally:
        server.shutdown()
        server.server_close()


def read_event(stream) -> tuple[str, dict]:
    kind, data = None, None
    while True:
        text = stream.readline().decode().rstrip("\n")
        if text.startswith("event: "):
            kind = text[7:]
        elif text.startswith("data: "):
            data = json.loads(text[6:])
        elif not text and kind:
            if kind != "ping":
                return kind, data
            kind = None


def test_the_pipeline_fixture_is_what_its_generator_makes() -> None:
    """Regenerate with: uv run python tests/fixtures/make_pipelines.py"""
    generator = runpy.run_path(str(FIXTURES / "make_pipelines.py"))
    assert json.loads((FIXTURES / "pipelines.json").read_text()) == generator["fixture"]()


def test_a_fixture_serves_its_recorded_pipeline_reports() -> None:
    state = FixtureState({"time": 1, "hosts": [{"name": "home", "ok": True, "error": None, "jobs": [], "sessions": []}],
                          "pipelines": DECLARED,
                          "pipeline_reports": [{"host": "home", "pipeline": "invoice-training", "run": RUN,
                                                "baseline": None}]})
    [pipeline] = state.document()["pipelines"]
    assert pipeline["run"] == RUN and pipeline["host_ok"]
