"""Focus as live workspace state: workspace.json beside the Fleet config, /api/state and POST /api/focus."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet import transport
from fleet.composition import open_workspace
from workspace_support import persist_registry

from fleet.transport import FleetError, Host
from fleet.web.server import FleetState, make_handler

HOSTS = [Host("home", None), Host("gpu", "gpu.example")]
CONFIG = {"hosts": {"home": {}, "gpu": {"ssh": "gpu.example"}}}


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(CONFIG))
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    return path


def start_deck():
    """A deck as `fleet web` builds it; each host has a job and a session labelled `agent-fleet`."""
    state = FleetState(HOSTS, {}, open_workspace().registry, open_workspace())
    for index, host in enumerate(HOSTS):
        def fill(entry, index=index):
            entry["ok"], entry["error"] = True, None
            entry["jobs"][f"j{index}"] = {"id": f"j{index}", "project": "agent-fleet", "created_at": index}
            entry["sessions"][f"s{index}"] = {"id": f"s{index}", "project": "agent-fleet", "started_at": index}
        state.update(host.name, fill)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def deck(config_path):
    server, url = start_deck()
    yield url
    server.shutdown()
    server.server_close()


def fetch_state(base_url):
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return json.load(response)


def post_focus(base_url, body, **headers):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = Request(base_url + "/api/focus", data=data, method="POST",
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def focus_by_host(document):
    """host → focus of its job and session, which must agree."""
    result = {}
    for host in document["hosts"]:
        (focus,) = {item["focus"] for item in host["jobs"] + host["sessions"]}
        result[host["name"]] = focus
    return result


def register(name, *links):
    registry = open_workspace().registry()
    project = registry.create(name)
    for host, label in links:
        registry.link(project.id, host, label)
    persist_registry(registry)
    return project.id


def test_nothing_stored_means_priority_and_no_file(deck, config_path):
    document = fetch_state(deck)
    assert document["focus"] == {"projects": {}, "labels": {}}
    assert focus_by_host(document) == {"home": "priority", "gpu": "priority"}
    assert not (config_path.parent / "workspace.json").exists()


def test_linked_work_follows_its_project_and_unlinked_work_its_label(deck, config_path):
    project_id = register("Agent Fleet", ("home", "agent-fleet"))
    assert post_focus(deck, {"focus": "background", "projects": [project_id]}, Origin=deck) == (
        200, {"projects": {project_id: "background"}, "labels": {}})
    assert focus_by_host(fetch_state(deck)) == {"home": "background", "gpu": "priority"}

    assert post_focus(deck, {"focus": "background", "labels": ["agent-fleet"]})[0] == 200
    assert focus_by_host(fetch_state(deck)) == {"home": "background", "gpu": "background"}
    post_focus(deck, {"focus": "priority", "projects": [project_id]})
    assert focus_by_host(fetch_state(deck)) == {"home": "priority", "gpu": "background"}

    stored = open_workspace().snapshot()
    assert stored["focus"] == {"projects": {project_id: "priority"}, "labels": {"agent-fleet": "background"}}
    config = json.loads(config_path.read_text())
    assert "focus" not in config and "projects" not in config


def test_focus_survives_a_restart(config_path):
    server, url = start_deck()
    post_focus(url, {"focus": "background", "labels": ["agent-fleet"]})
    server.shutdown()
    server.server_close()
    server, url = start_deck()
    try:
        assert focus_by_host(fetch_state(url)) == {"home": "background", "gpu": "background"}
    finally:
        server.shutdown()
        server.server_close()


def test_a_change_is_pushed_to_open_browsers(deck):
    with urlopen(deck + "/api/stream", timeout=5) as stream:
        assert stream.readline() == b"event: state\n"
        stream.readline(), stream.readline()
        post_focus(deck, {"focus": "background", "labels": ["agent-fleet"]})
        assert stream.readline() == b"event: state\n"
        pushed = json.loads(stream.readline().decode().removeprefix("data: "))
    assert pushed["focus"]["labels"] == {"agent-fleet": "background"}
    assert focus_by_host(pushed) == {"home": "background", "gpu": "background"}


@pytest.mark.parametrize("body, headers, status", [
    ({"focus": "background", "projects": ["p-00000000"]}, {}, 400),
    ({"focus": "parked", "labels": ["agent-fleet"]}, {}, 400),
    ({"focus": "background"}, {}, 400),
    ({"focus": "background", "labels": [""]}, {}, 400),
    ({"focus": "background", "labels": "agent-fleet"}, {}, 400),
    (b"not json", {}, 400),
    ({"focus": "background", "labels": ["agent-fleet"]}, {"Origin": "http://evil.example"}, 403),
    ({"focus": "background", "labels": ["agent-fleet"]}, {"Content-Type": "text/plain"}, 415),
])
def test_refused_writes_change_nothing(deck, config_path, body, headers, status):
    assert post_focus(deck, body, **headers)[0] == status
    assert fetch_state(deck)["focus"] == {"projects": {}, "labels": {}}
    assert not (config_path.parent / "workspace.json").exists()


def test_a_broken_focus_file_is_reported_not_ignored(config_path):
    (config_path.parent / "workspace.json").write_text(json.dumps({"focus": {"labels": {"agent-fleet": "parked"}}}))
    with pytest.raises(FleetError, match="priority or background"):
        open_workspace()
