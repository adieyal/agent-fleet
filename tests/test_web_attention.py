"""Stored stream attention items, their states, and the endpoints that change them."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet import projects, transport
from fleet.composition import open_attention, open_store
from fleet.transport import Host
from fleet.web.server import FleetState, apply_message, make_handler, workspace_path
from fleet.workspace import WorkspaceStore

HOSTS = [Host("home", None), Host("gpu", "gpu.example")]


def job(job_id, status, steps=(("done", 100),), project="restoke"):
    """A job as fleetd streams it; steps are (status, started_at)."""
    return {"id": job_id, "project": project, "status": status, "created_at": 1, "updated_at": 500,
            "description": f"job {job_id}",
            "steps": [{"index": index, "title": f"step {index}", "status": step_status, "started_at": started,
                       "finished_at": None, "result": None} for index, (step_status, started) in enumerate(steps)]}


def session(session_id, status, activity, project="restoke"):
    return {"id": session_id, "project": project, "agent": "claude", "status": status, "started_at": 1,
            "activity": activity, "events": [activity] if activity else []}


def tool(name, ts=200, summary="Keep the flag?"):
    return {"kind": "tool", "tool": "other", "name": name, "summary": summary, "ts": ts}


class Deck:
    """A live deck whose host state the test sets directly, as the host streams would."""

    def __init__(self, clock=None):
        self.state = FleetState(HOSTS, {}, projects.load_registry, WorkspaceStore(workspace_path()),
                               store=open_store(clock=clock))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def report(self, host, jobs=(), sessions=(), ok=True):
        def fill(entry):
            entry["ok"], entry["error"] = ok, None if ok else "ssh: no route to host"
            entry["jobs"] = {item["id"]: item for item in jobs}
            entry["sessions"] = {item["id"]: item for item in sessions}
        self.state.update(host, fill)

    def items(self):
        with urlopen(self.url + "/api/state", timeout=5) as response:
            return {item["owner"]["key"]: item for item in json.load(response)["attention"]}

    def act(self, action, body, **headers):
        request = Request(f"{self.url}/api/attention/{action}", data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json", **headers})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status
        except HTTPError as error:
            return error.code

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {}, "gpu": {"ssh": "gpu.example"}}}))
    monkeypatch.setattr(transport, "CONFIG_PATH", path)
    return path


@pytest.fixture
def deck(config_path):
    deck = Deck()
    yield deck
    deck.close()


def stored_actions(config_path):
    return {item.id: {"state": item.state} for item in open_attention().list()
            if item.state in ("acknowledged", "snoozed")}


def test_only_genuine_signals_become_items(deck):
    deck.report("home", jobs=[job("f1", "failed", [("done", 90), ("failed", 100), ("pending", None)]),
                              job("s1", "stalled", [("running", 120)]),
                              job("r1", "running", [("running", 130)]), job("d1", "done"), job("q1", "queued", [("pending", None)])],
                sessions=[session("ask", "idle", tool("AskUserQuestion")),
                          session("plan", "working", tool("ExitPlanMode", summary="Plan: split the importer")),
                          session("quiet", "idle", {"kind": "text", "summary": "Shall I go on?", "ts": 210}),
                          session("busy", "working", tool("Grep"))])
    items = deck.items()
    assert {key: (item["kind"], item["state"], item["source"]) for key, item in items.items()} == {
        "home:f1": ("blocker", "open", "job status failed"),
        "home:s1": ("blocker", "open", "job status stalled"),
        "home:ask": ("decision", "open", "session tool AskUserQuestion"),
        "home:plan": ("decision", "open", "session tool ExitPlanMode")}
    failed = items["home:f1"]
    assert failed["owner"] == {"type": "job", "host": "home", "id": "f1", "key": "home:f1"}
    assert failed["summary"] == "step 2 failed: step 1" and failed["since"] == 100
    assert (failed["project"], failed["project_id"]) == ("restoke", None)
    assert items["home:ask"]["owner"]["type"] == "session"
    assert "Keep the flag?" in items["home:ask"]["summary"]
    assert deck.items() == items   # reading changes nothing, ids included


def test_items_resolve_when_their_condition_clears(deck, config_path):
    failing = job("f1", "failed", [("failed", 100)])
    deck.report("home", jobs=[failing, job("gone", "stalled", [("running", 120)])],
                sessions=[session("ask", "idle", tool("AskUserQuestion"))])
    first = deck.items()
    assert deck.act("acknowledge", {"id": first["home:f1"]["id"]}) == 200
    assert first["home:f1"]["id"] in stored_actions(config_path)

    retried = job("f1", "running", [("running", 300)])
    answered = session("ask", "working", {"kind": "text", "summary": "Removing it.", "ts": 260})
    deck.report("home", jobs=[retried], sessions=[answered])
    items = {item["id"]: item for item in deck.items().values()}
    assert {items[first[key]["id"]]["state"] for key in first} == {"resolved"}
    assert all(items[first[key]["id"]]["resolved_at"] for key in first)
    assert stored_actions(config_path) == {}   # resolved items no longer carry an active action
    assert all(item.resolution_details for item in open_attention().list())

    deck.report("home", jobs=[job("f1", "failed", [("failed", 300)])], sessions=[answered])
    again = deck.items()["home:f1"]
    assert again["id"] != first["home:f1"]["id"] and again["state"] == "open"   # a new failure starts open


def test_an_unreachable_host_leaves_its_items_as_they_were(deck):
    deck.report("gpu", jobs=[job("f1", "failed", [("failed", 100)])])
    item = deck.items()["gpu:f1"]
    deck.act("acknowledge", {"id": item["id"]})
    deck.report("gpu", ok=False)
    stale = deck.items()["gpu:f1"]
    assert (stale["state"], stale["stale"], stale["resolved_at"]) == ("acknowledged", True, None)
    assert stale["last_seen"] == item["last_seen"]


def test_acknowledge_snooze_and_reopen(deck, config_path):
    deck.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
    item_id = deck.items()["home:f1"]["id"]

    assert deck.act("acknowledge", {"id": item_id}) == 200
    acknowledged = deck.items()["home:f1"]
    assert acknowledged["state"] == "acknowledged" and acknowledged["acknowledged_at"]

    assert deck.act("snooze", {"id": item_id, "seconds": 3600}) == 200
    snoozed = deck.items()["home:f1"]
    assert snoozed["state"] == "snoozed" and snoozed["snoozed_until"] > time.time() + 3500
    assert stored_actions(config_path)[item_id]["state"] == "snoozed"

    assert deck.act("reopen", {"id": item_id}) == 200
    assert deck.items()["home:f1"]["state"] == "open"
    assert stored_actions(config_path) == {}

    deck.act("snooze", {"id": item_id, "seconds": 0.3})
    time.sleep(0.5)
    assert deck.items()["home:f1"]["state"] == "open"   # the snooze ran out


def test_actions_survive_a_restart(config_path):
    first = Deck()
    first.report("home", jobs=[job("f1", "failed", [("failed", 100)])],
                 sessions=[session("ask", "idle", tool("AskUserQuestion"))])
    first.act("acknowledge", {"id": first.items()["home:f1"]["id"]})
    first.close()
    second = Deck()
    try:
        assert second.items()["home:f1"]["state"] == "acknowledged"
        assert second.items()["home:ask"]["state"] == "open"
        second.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
        assert second.items()["home:f1"]["state"] == "acknowledged"
    finally:
        second.close()


def test_legacy_action_is_imported_before_workspace_stops_writing_attention(config_path):
    path = config_path.parent / "workspace.json"
    path.write_text(json.dumps({"attention": {
        "job:home:f1:failed:0@100": {"state": "acknowledged", "at": 150}}}))
    deck = Deck()
    try:
        deck.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
        item = deck.items()["home:f1"]
        assert item["state"] == "acknowledged" and item["acknowledged_at"] == 150
        deck.state.set_focus("background", [], ["restoke"])
        assert "attention" not in json.loads(path.read_text())
        assert json.loads(path.with_suffix(".json.bak").read_text())["attention"]
    finally:
        deck.close()


def test_changes_and_ending_snoozes_are_pushed(config_path):
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    deck = Deck(clock=lambda: now[0])
    try:
        deck.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
        item_id = deck.items()["home:f1"]["id"]
        with urlopen(deck.url + "/api/stream", timeout=5) as stream:
            def next_items():
                assert stream.readline() == b"event: state\n"
                items = json.loads(stream.readline().decode().removeprefix("data: "))["attention"]
                stream.readline()
                return {item["owner"]["key"]: item["state"] for item in items}
            next_items()
            deck.act("snooze", {"id": item_id, "seconds": 1})
            assert next_items() == {"home:f1": "snoozed"}
            now[0] += timedelta(seconds=2)
            assert next_items() == {"home:f1": "open"}
    finally:
        deck.close()


@pytest.mark.parametrize("action, body, headers, status", [
    ("acknowledge", {"id": "job:home:nope:failed:0@1"}, {}, 404),
    ("acknowledge", {}, {}, 400),
    ("snooze", {"id": "ITEM", "seconds": -5}, {}, 400),
    ("snooze", {"id": "ITEM"}, {}, 400),
    ("acknowledge", {"id": "ITEM"}, {"Origin": "http://evil.example"}, 403),
    ("acknowledge", {"id": "ITEM"}, {"Content-Type": "text/plain"}, 415),
    ("resolve", {"id": "ITEM"}, {}, 404),
])
def test_refused_actions_change_nothing(deck, action, body, headers, status):
    deck.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
    item_id = deck.items()["home:f1"]["id"]
    body = {key: item_id if value == "ITEM" else value for key, value in body.items()}
    assert deck.act(action, body, **headers) == status
    assert deck.items()["home:f1"]["state"] == "open"


def test_a_resolved_item_cannot_be_acted_on(deck):
    deck.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
    item_id = deck.items()["home:f1"]["id"]
    deck.report("home", jobs=[])
    assert deck.act("acknowledge", {"id": item_id}) == 409
    assert deck.act("reopen", {"id": item_id}) == 409


@pytest.mark.parametrize("owner_type", ["job", "session"])
def test_input_observations_deduplicate_resume_and_survive_silence(deck, owner_type):
    observation = {"type": "input_observation", "schema_version": 1, "host": "worker-hostname",
                   "runtime": "claude", "owner_type": owner_type, "job_id": "j1",
                   "session_id": "s1", "step_index": 0, "project": "restoke",
                   "kind": "input_requested", "reason": "permission",
                   "source_event": "PermissionRequest", "source_event_id": "request1",
                   "observed_at": 200, "context_reference": "/retained/hook.json"}
    apply_message(deck.state, HOSTS[0], {"type": "hello"})
    for _ in range(10):
        apply_message(deck.state, HOSTS[0], observation)
    [item] = deck.items().values()
    assert item["kind"] == "decision" and item["state"] == "open"
    assert item["owner"]["host"] == "home"
    assert item["owner"]["id"] == ("j1" if owner_type == "job" else "s1")
    assert item["last_seen"] == 200
    apply_message(deck.state, HOSTS[0], {"type": "heartbeat"})
    deck.report("home", ok=False)
    [quiet] = deck.items().values()
    assert quiet["state"] == "open" and quiet["stale"]
    assert quiet["last_seen"] == item["last_seen"]
    apply_message(deck.state, HOSTS[0], {**observation, "kind": "input_cleared",
                                       "source_event": "PostToolUse", "observed_at": 250})
    [resumed] = deck.items().values()
    assert resumed["state"] == "resolved"
    assert resumed["resolution_details"] == "answered in session"
    apply_message(deck.state, HOSTS[0], observation)
    assert next(iter(deck.items().values()))["state"] == "resolved"
    apply_message(deck.state, HOSTS[0], {**observation, "source_event_id": "request2"})
    assert len(deck.items()) == 1  # projection helper indexes by owner
    assert len(deck.state.attention.list()) == 2
