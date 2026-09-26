"""/api/state resolves project IDs from the registry in the Fleet config, leaving labels as they were."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet import projects, transport
from fleet.transport import Host
from fleet.web.server import FleetState, make_handler

HOSTS = [Host("home", None), Host("gpu", "gpu.example")]


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {}, "gpu": {"ssh": "gpu.example"}}}))
    monkeypatch.setattr(transport, "CONFIG_PATH", path)
    return path


@pytest.fixture
def deck(config_path):
    """A running deck whose hosts each have one job and one session labelled `agent-fleet`."""
    state = FleetState(HOSTS, {"agent-fleet": "Room sign"}, projects.load_registry, projects.save_registry)
    for index, host in enumerate(HOSTS):
        def fill(entry, index=index, host=host):
            entry["ok"], entry["error"] = True, None
            entry["jobs"][f"j{index}"] = {"id": f"j{index}", "project": "agent-fleet", "created_at": index}
            entry["sessions"][f"s{index}"] = {"id": f"s{index}", "project": "agent-fleet", "started_at": index}
            entry["sessions"]["loose"] = {"id": "loose", "project": None, "started_at": 9}
        state.update(host.name, fill)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def fetch_state(base_url):
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return json.load(response)


def post_focus(base_url, body, **headers):
    """POST /api/focus; returns (status, JSON reply)."""
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = Request(base_url + "/api/focus", data=data, method="POST",
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def ids(document, host_name, kind="jobs"):
    host = next(host for host in document["hosts"] if host["name"] == host_name)
    return {item["id"]: (item["project"], item["project_id"]) for item in host[kind]}


def register(name, *links):
    registry = projects.load_registry()
    project = registry.create(name)
    for host, label in links:
        registry.link(project.id, host, label)
    projects.save_registry(registry)
    return project.id


def test_unlinked_labels_get_null_and_existing_fields_stay(deck):
    document = fetch_state(deck)
    assert document["projects"] == [] and document["projects_error"] is None
    assert document["project_labels"] == {"agent-fleet": "Room sign"}
    assert ids(document, "home") == {"j0": ("agent-fleet", None)}
    assert ids(document, "home", "sessions") == {"s0": ("agent-fleet", None), "loose": (None, None)}
    home = document["hosts"][0]
    assert home["ok"] is True and home["jobs"][0]["created_at"] == 0


def test_linked_label_resolves_on_its_host_only(deck):
    project_id = register("Agent Fleet", ("home", "agent-fleet"))
    document = fetch_state(deck)
    assert ids(document, "home") == {"j0": ("agent-fleet", project_id)}
    assert ids(document, "home", "sessions")["s0"] == ("agent-fleet", project_id)
    assert ids(document, "gpu") == {"j1": ("agent-fleet", None)}
    assert document["projects"] == [{"id": project_id, "name": "Agent Fleet", "repositories": [], "focus": "priority",
                                     "links": [{"host": "home", "label": "agent-fleet"}]}]


def test_same_label_on_two_hosts_merges_only_when_both_link_one_id(deck):
    first = register("Fleet at home", ("home", "agent-fleet"))
    second = register("Fleet on gpu", ("gpu", "agent-fleet"))
    document = fetch_state(deck)
    assert ids(document, "home")["j0"][1] == first
    assert ids(document, "gpu")["j1"][1] == second

    registry = projects.load_registry()
    registry.unlink("gpu", "agent-fleet")
    registry.link(first, "gpu", "agent-fleet")
    projects.save_registry(registry)
    document = fetch_state(deck)
    assert ids(document, "home")["j0"][1] == ids(document, "gpu")["j1"][1] == first


def test_rename_keeps_the_id(deck):
    project_id = register("Agent Fleet", ("home", "agent-fleet"))
    registry = projects.load_registry()
    registry.rename(project_id, "Fleet")
    projects.save_registry(registry)
    document = fetch_state(deck)
    assert [(project["id"], project["name"]) for project in document["projects"]] == [(project_id, "Fleet")]
    assert ids(document, "home")["j0"] == ("agent-fleet", project_id)


def test_broken_registry_keeps_the_last_good_one_and_says_so(deck, config_path):
    project_id = register("Agent Fleet", ("home", "agent-fleet"))
    fetch_state(deck)
    config_path.write_text(json.dumps({"projects": {"not-an-id": {"name": "X"}}}))
    document = fetch_state(deck)
    assert "invalid project id" in document["projects_error"]
    assert ids(document, "home")["j0"] == ("agent-fleet", project_id)


def test_focus_set_from_the_deck_is_saved_and_pushed(deck, config_path):
    project_id = register("Agent Fleet", ("home", "agent-fleet"))
    with urlopen(deck + "/api/stream", timeout=5) as stream:
        assert stream.readline() == b"event: state\n"
        stream.readline(), stream.readline()
        assert post_focus(deck, {"project": project_id, "focus": "background"}, Origin=deck) == (
            200, {"project": project_id, "focus": "background"})
        assert stream.readline() == b"event: state\n"
        pushed = json.loads(stream.readline().decode().removeprefix("data: "))
    assert [project["focus"] for project in pushed["projects"]] == ["background"]
    assert json.loads(config_path.read_text())["projects"][project_id]["focus"] == "background"
    assert json.loads(config_path.read_text())["hosts"] == {"home": {}, "gpu": {"ssh": "gpu.example"}}


@pytest.mark.parametrize("body, headers, status", [
    ({"project": "p-00000000", "focus": "background"}, {}, 400),
    ({"project": "PROJECT", "focus": "parked"}, {}, 400),
    (b"not json", {}, 400),
    ({"project": "PROJECT", "focus": "background"}, {"Origin": "http://evil.example"}, 403),
    ({"project": "PROJECT", "focus": "background"}, {"Content-Type": "text/plain"}, 415),
])
def test_focus_writes_that_are_refused_change_nothing(deck, config_path, body, headers, status):
    project_id = register("Agent Fleet", ("home", "agent-fleet"))
    if isinstance(body, dict) and body["project"] == "PROJECT":
        body = {**body, "project": project_id}
    assert post_focus(deck, body, **headers)[0] == status
    assert json.loads(config_path.read_text())["projects"][project_id]["focus"] == "priority"


def test_a_deck_without_a_registry_to_save_to_refuses_focus(config_path):
    state = FleetState(HOSTS, {}, projects.load_registry)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        project_id = register("Agent Fleet", ("home", "agent-fleet"))
        status, reply = post_focus(f"http://127.0.0.1:{server.server_port}", {"project": project_id, "focus": "background"})
        assert status == 400 and "no project registry" in reply["error"]
    finally:
        server.shutdown()
        server.server_close()
