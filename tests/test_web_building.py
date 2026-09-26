"""The building's floors: capacity from the Fleet config, floors as live state in workspace.json, and moving in."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet import building, projects, transport
from fleet.transport import FleetError, Host
from fleet.web.server import FleetState, make_handler, workspace_path
from fleet.workspace import WorkspaceStore

HOSTS = [Host("home", None), Host("gpu", "gpu.example")]
LABELS = ["agent-fleet", "restoke", "invoices"]


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {}, "gpu": {"ssh": "gpu.example"}}}))
    monkeypatch.setattr(transport, "CONFIG_PATH", path)
    return path


def set_config(path, **changes):
    path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))


def start_deck():
    """A deck as `fleet web` builds it; home has a job for each label, gpu one `agent-fleet` session."""
    state = FleetState(HOSTS, {"invoices": "Invoice analysis"}, projects.load_registry,
                       WorkspaceStore(workspace_path()), building.load_capacity)

    def fill_home(entry):
        entry["ok"], entry["error"] = True, None
        for index, label in enumerate(LABELS):
            entry["jobs"][f"j{index}"] = {"id": f"j{index}", "project": label, "created_at": index, "status": "running"}

    def fill_gpu(entry):
        entry["ok"], entry["error"] = True, None
        entry["sessions"]["s0"] = {"id": "s0", "project": "agent-fleet", "started_at": 0}

    state.update("home", fill_home)
    state.update("gpu", fill_gpu)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
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


def post(base_url, path, body, **headers):
    request = Request(base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def register(name, *links):
    registry = projects.load_registry()
    project = registry.create(name)
    for host, label in links:
        registry.link(project.id, host, label)
    projects.save_registry(registry)
    return project.id


def unregister(project_id):
    registry = projects.load_registry()
    registry.remove(project_id)
    projects.save_registry(registry)


def stored_floors():
    return json.loads(workspace_path().read_text())["floors"]


# ------------------------------------------------------------------ capacity
def test_capacity_is_six_unless_the_config_says_otherwise(deck, config_path):
    assert fetch_state(deck)["building"] == {"capacity": 6, "floors": {}, "no_floor": [], "capacity_error": None}
    set_config(config_path, capacity=10)
    assert fetch_state(deck)["building"]["capacity"] == 10


@pytest.mark.parametrize("capacity", [0, 11, "6", 6.5, True, None])
def test_an_invalid_capacity_stops_the_deck_starting(config_path, capacity):
    set_config(config_path, capacity=capacity)
    with pytest.raises(FleetError, match="capacity is a whole number of floors from 1 to 10"):
        start_deck()


def test_an_invalid_capacity_edit_keeps_the_last_good_one_and_says_why(deck, config_path):
    set_config(config_path, capacity=8)
    assert fetch_state(deck)["building"]["capacity"] == 8
    set_config(config_path, capacity=12)
    building_state = fetch_state(deck)["building"]
    assert building_state["capacity"] == 8
    assert "capacity is a whole number of floors from 1 to 10, not 12" in building_state["capacity_error"]


# ------------------------------------------------------------------ floor assignment
def test_registered_projects_take_the_lowest_free_floors_and_keep_them(deck, config_path):
    first = register("Agent Fleet", ("home", "agent-fleet"))
    assert fetch_state(deck)["building"]["floors"] == {first: 1}
    second = register("Restoke", ("home", "restoke"))
    assert fetch_state(deck)["building"]["floors"] == {first: 1, second: 2}
    assert stored_floors() == {first: 1, second: 2}

    # focus never moves a floor
    assert post(deck, "/api/focus", {"focus": "background", "projects": [first]})[0] == 200
    assert fetch_state(deck)["building"]["floors"] == {first: 1, second: 2}

    # a project that leaves the registry frees its floor; the next one moves into the gap, others stay
    unregister(first)
    third = register("Invoices", ("home", "invoices"))
    assert fetch_state(deck)["building"]["floors"] == {second: 2, third: 1}


def test_floors_survive_a_restart(deck, config_path):
    project_id = register("Restoke", ("home", "restoke"))
    fetch_state(deck)
    server, url = start_deck()
    try:
        assert fetch_state(url)["building"]["floors"] == {project_id: 1}
    finally:
        server.shutdown()
        server.server_close()


def test_projects_without_a_floor_are_listed_and_keep_their_place(deck, config_path):
    set_config(config_path, capacity=2)
    ids = [register(label, ("home", label)) for label in LABELS]
    building_state = fetch_state(deck)["building"]
    housed = building_state["floors"]
    assert sorted(housed.values()) == [1, 2]
    assert building_state["no_floor"] == [project_id for project_id in ids if project_id not in housed]
    assert len(building_state["no_floor"]) == 1

    # lowering capacity leaves a floor above it empty in the building, but assigned: raising it again restores it
    top = next(project_id for project_id, floor in housed.items() if floor == 2)
    set_config(config_path, capacity=1)
    building_state = fetch_state(deck)["building"]
    assert top not in building_state["floors"] and top in building_state["no_floor"]
    set_config(config_path, capacity=3)
    assert fetch_state(deck)["building"]["floors"][top] == 2


# ------------------------------------------------------------------ moving in
def test_moving_in_registers_links_and_takes_the_lowest_free_floor(deck, config_path):
    restoke = register("Restoke", ("home", "restoke"))
    invoices = register("Invoice analysis", ("home", "invoices"))
    fetch_state(deck)
    unregister(restoke)                        # floor 1 is free again, floor 2 taken
    fetch_state(deck)

    status, moved = post(deck, "/api/move-in", {"host": "gpu", "label": "agent-fleet"})
    assert status == 200 and moved["floor"] == 1
    registry = projects.load_registry()
    project = registry.get(moved["project_id"])
    assert project.name == "agent-fleet" and [(link.host, link.label) for link in project.links] == [("gpu", "agent-fleet")]

    document = fetch_state(deck)
    assert document["building"]["floors"] == {invoices: 2, moved["project_id"]: 1}
    gpu = next(host for host in document["hosts"] if host["name"] == "gpu")
    assert gpu["sessions"][0]["project_id"] == moved["project_id"]
    home = next(host for host in document["hosts"] if host["name"] == "home")
    assert next(job for job in home["jobs"] if job["project"] == "agent-fleet")["project_id"] is None   # only gpu's label


def test_moving_in_uses_the_rooms_display_name(deck):
    status, moved = post(deck, "/api/move-in", {"host": "home", "label": "invoices"})
    assert status == 200 and projects.load_registry().get(moved["project_id"]).name == "Invoice analysis"


def test_moving_in_a_full_building_changes_nothing(deck, config_path):
    set_config(config_path, capacity=1)
    register("Restoke", ("home", "restoke"))
    fetch_state(deck)
    status, body = post(deck, "/api/move-in", {"host": "home", "label": "agent-fleet"})
    assert status == 409 and "full" in body["error"]
    assert len(projects.load_registry().projects) == 1


def test_moving_in_refuses_a_linked_label_an_unknown_host_and_other_sites(deck):
    register("Restoke", ("home", "restoke"))
    assert post(deck, "/api/move-in", {"host": "home", "label": "restoke"})[0] == 409
    assert post(deck, "/api/move-in", {"host": "nowhere", "label": "restoke"})[0] == 400
    assert post(deck, "/api/move-in", {"host": "home"})[0] == 400
    assert post(deck, "/api/move-in", {"host": "home", "label": "invoices"}, Origin="http://evil.example")[0] == 403
    assert len(projects.load_registry().projects) == 1
