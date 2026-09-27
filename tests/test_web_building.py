"""The building's floors: capacity from the Fleet config, floors as live state in workspace.json, and moving in."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from fleet import transport
from fleet.composition import open_workspace
from fleet.modules.workspace import Registry
from workspace_support import persist_registry
from fleet.transport import FleetError, Host
from fleet.web.server import FleetState, make_handler


HOSTS = [Host("home", None), Host("gpu", "gpu.example")]
LABELS = ["agent-fleet", "restoke", "invoices"]


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {}, "gpu": {"ssh": "gpu.example"}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    return path


def set_config(path, **changes):
    if "capacity" in changes:
        open_workspace().set_capacity(changes.pop("capacity"))
    if "projects" in changes:
        persist_registry(Registry.from_config({"projects": changes.pop("projects")}))
    if changes:
        path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))


def start_deck():
    """A deck as `fleet web` builds it; home has a job for each label, gpu one `agent-fleet` session."""
    state = FleetState(HOSTS, {"invoices": "Invoice analysis"}, open_workspace().registry,
                       open_workspace(), open_workspace().capacity)

    def fill_home(entry):
        entry["ok"], entry["error"] = True, None
        for index, label in enumerate(LABELS):
            entry["jobs"][f"j{index}"] = {"id": f"j{index}", "project": label, "created_at": index, "status": "running"}

    def fill_gpu(entry):
        entry["ok"], entry["error"] = True, None
        entry["sessions"]["s0"] = {"id": "s0", "project": "agent-fleet", "started_at": 0, "cwd": "/src/agent-fleet"}

    state.update("home", fill_home)
    state.update("gpu", fill_gpu)
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


def post(base_url, path, body, **headers):
    request = Request(base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def register(name, *links):
    registry = open_workspace().registry()
    project = registry.create(name)
    for host, label in links:
        registry.link(project.id, host, label)
    persist_registry(registry)
    return project.id


def unregister(project_id):
    registry = open_workspace().registry()
    registry.remove(project_id)
    persist_registry(registry)


def stored_floors():
    return open_workspace().floors_snapshot()


# ------------------------------------------------------------------ capacity
def test_capacity_is_six_unless_the_config_says_otherwise(deck, config_path):
    assert fetch_state(deck)["building"] == {"capacity": 6, "floors": {}, "focus": {}, "shuttered": {}, "no_floor": [],
                                             "capacity_error": None}
    set_config(config_path, capacity=10)
    assert fetch_state(deck)["building"]["capacity"] == 10


@pytest.mark.parametrize("capacity", [0, 11, "6", 6.5, True, None])
def test_an_invalid_capacity_stops_the_deck_starting(config_path, capacity):
    config_path.write_text(json.dumps({"capacity": capacity}))
    with pytest.raises(FleetError, match="capacity is a whole number of floors from 1 to 10"):
        start_deck()


def test_an_invalid_capacity_edit_keeps_the_last_good_one_and_says_why(deck, config_path):
    set_config(config_path, capacity=8)
    assert fetch_state(deck)["building"]["capacity"] == 8
    with pytest.raises(FleetError, match="capacity is a whole number of floors from 1 to 10, not 12"):
        set_config(config_path, capacity=12)
    building_state = fetch_state(deck)["building"]
    assert building_state["capacity"] == 8
    assert building_state["capacity_error"] is None


# ------------------------------------------------------------------ floor assignment
def test_registered_projects_take_the_lowest_free_floors_and_keep_them(deck, config_path):
    first = register("Agent Fleet", ("home", "agent-fleet"))
    assert fetch_state(deck)["building"]["floors"] == {first: 1}
    second = register("Restoke", ("home", "restoke"))
    assert fetch_state(deck)["building"]["floors"] == {first: 1, second: 2}
    assert stored_floors() == {first: 1, second: 2}

    # focus never moves a floor
    assert post(deck, "/api/focus", {"focus": "background", "projects": [first]})[0] == 200
    building_state = fetch_state(deck)["building"]
    assert building_state["floors"] == {first: 1, second: 2}
    assert building_state["focus"] == {first: "background", second: "priority"}

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
    fetch_state(deck)
    invoices = register("Invoice analysis", ("home", "invoices"))
    fetch_state(deck)
    unregister(restoke)                        # floor 1 is free again, floor 2 taken
    fetch_state(deck)

    status, moved = post(deck, "/api/move-in", {"host": "gpu", "label": "agent-fleet"})
    assert status == 200 and moved["floor"] == 1
    registry = open_workspace().registry()
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
    assert status == 200 and open_workspace().registry().get(moved["project_id"]).name == "Invoice analysis"


def test_moving_in_a_full_building_changes_nothing(deck, config_path):
    set_config(config_path, capacity=1)
    register("Restoke", ("home", "restoke"))
    fetch_state(deck)
    status, body = post(deck, "/api/move-in", {"host": "home", "label": "agent-fleet"})
    assert status == 409 and "full" in body["error"]
    assert len(open_workspace().registry().projects) == 1


def test_moving_in_refuses_a_linked_label_an_unknown_host_and_other_sites(deck):
    register("Restoke", ("home", "restoke"))
    assert post(deck, "/api/move-in", {"host": "home", "label": "restoke"})[0] == 409
    assert post(deck, "/api/move-in", {"host": "nowhere", "label": "restoke"})[0] == 400
    assert post(deck, "/api/move-in", {"host": "home"})[0] == 400
    assert post(deck, "/api/move-in", {"host": "home", "label": "invoices"}, Origin="http://evil.example")[0] == 403
    assert len(open_workspace().registry().projects) == 1


def test_moving_in_a_label_on_several_hosts_makes_one_project(deck):
    status, moved = post(deck, "/api/move-in", {"hosts": ["home", "gpu"], "label": "agent-fleet"})
    assert status == 200 and moved["floor"] == 1
    project = open_workspace().registry().get(moved["project_id"])
    assert sorted((link.host, link.label) for link in project.links) == [("gpu", "agent-fleet"), ("home", "agent-fleet")]
    assert post(deck, "/api/move-in", {"hosts": [], "label": "restoke"})[0] == 400
    assert post(deck, "/api/move-in", {"hosts": ["home", "nowhere"], "label": "restoke"})[0] == 400


# ------------------------------------------------------------------ linking a label to a project it belongs to
def options(base_url, label, *hosts):
    query = urlencode([("label", label), *(("host", host) for host in hosts)])
    with urlopen(f"{base_url}/api/move-in?{query}", timeout=5) as response:
        return json.load(response)


def test_move_in_offers_the_project_a_label_is_linked_to_on_another_host(deck, monkeypatch):
    monkeypatch.setattr(transport, "repository_remotes", lambda host, directories: {directory: [] for directory in directories})
    (agent_fleet,) = housed(deck, "agent-fleet")          # "Agent-Fleet", linked on home, floor 1
    register("Unrelated", ("home", "restoke"))
    offered = options(deck, "agent-fleet", "gpu")
    assert offered["candidates"] == [{"project_id": agent_fleet, "name": "Agent-Fleet", "reasons": ["linked", "name"],
                                      "floor": 1, "shuttered": False}]
    assert offered["errors"] == []


def test_move_in_offers_a_name_match_but_never_links_by_itself(deck):
    invoices = register("Invoice Analysis")                # the room's display name for "invoices", unlinked
    offered = options(deck, "invoices", "home")
    assert [(c["project_id"], c["reasons"], c["floor"]) for c in offered["candidates"]] == [(invoices, ["name"], 1)]
    home = next(host for host in fetch_state(deck)["hosts"] if host["name"] == "home")
    assert next(job for job in home["jobs"] if job["project"] == "invoices")["project_id"] is None


def test_move_in_offers_a_matching_repository(deck, monkeypatch):
    fleet = register("Fleet")
    registry = open_workspace().registry()
    registry.add_repository(fleet, "git@github.com:adieyal/agent-fleet.git")
    persist_registry(registry)
    asked = []

    def remotes(host, directories):
        asked.append((host.name, directories))
        return {directory: ["https://github.com/adieyal/agent-fleet"] for directory in directories}

    monkeypatch.setattr(transport, "repository_remotes", remotes)
    offered = options(deck, "agent-fleet", "gpu")
    assert asked == [("gpu", ["/src/agent-fleet"])]
    assert [(c["project_id"], c["reasons"]) for c in offered["candidates"]] == [(fleet, ["repository"])]

    def unreachable(host, directories):
        raise FleetError(f"{host.name}: could not read repository remotes")

    monkeypatch.setattr(transport, "repository_remotes", unreachable)
    offered = options(deck, "agent-fleet", "gpu")
    assert offered["candidates"] == [] and offered["errors"] == ["gpu: could not read repository remotes"]


def test_linking_joins_a_project_and_takes_no_floor(deck):
    (agent_fleet,) = housed(deck, "agent-fleet")
    before = building_of(deck)
    status, body = post(deck, "/api/link", {"project": agent_fleet, "hosts": ["gpu"], "label": "agent-fleet"})
    assert status == 200 and body == {"project_id": agent_fleet, "floor": 1}
    document = fetch_state(deck)
    assert document["building"] == before and len(document["projects"]) == 1
    assert document["projects"][0]["links"] == [{"host": "gpu", "label": "agent-fleet"}, {"host": "home", "label": "agent-fleet"}]
    gpu = next(host for host in document["hosts"] if host["name"] == "gpu")
    assert gpu["sessions"][0]["project_id"] == agent_fleet


def test_linking_refuses_what_makes_no_sense(deck, config_path):
    set_config(config_path, capacity=1)
    agent_fleet, restoke = housed(deck, "agent-fleet", "restoke")   # restoke gets no floor: the building is full
    assert post(deck, "/api/link", {"project": restoke, "hosts": ["gpu"], "label": "agent-fleet"})[0] == 200   # full: still fine
    assert post(deck, "/api/link", {"project": agent_fleet, "hosts": ["gpu"], "label": "agent-fleet"})[0] == 409
    assert post(deck, "/api/link", {"project": "p-00000000", "hosts": ["home"], "label": "invoices"})[0] == 404
    assert post(deck, "/api/link", {"hosts": ["home"], "label": "invoices"})[0] == 400
    assert post(deck, "/api/link", {"project": agent_fleet, "hosts": ["home"], "label": "invoices"},
                Origin="http://evil.example")[0] == 403


# ------------------------------------------------------------------ merging projects registered by mistake
def test_merging_keeps_the_older_project_and_frees_the_others_floor(deck):
    older, restoke, newer = housed(deck, "agent-fleet", "restoke", "fleet")
    registry = open_workspace().registry()
    registry.unlink("home", "fleet")
    registry.link(newer, "gpu", "agent-fleet")
    registry.add_repository(newer, "git@github.com:adieyal/agent-fleet.git")
    persist_registry(registry)
    assert post(deck, "/api/focus", {"focus": "background", "projects": [newer]})[0] == 200

    assert post(deck, "/api/merge", {"keep": newer, "other": older})[0] == 400   # the newer one isn't kept
    status, body = post(deck, "/api/merge", {"keep": older, "other": newer})
    assert status == 200 and body == {"project_id": older, "merged": newer, "freed": 3, "floor": 1}

    document = fetch_state(deck)
    assert document["building"]["floors"] == {older: 1, restoke: 2}
    assert newer not in document["focus"]["projects"] and newer not in stored_floors()
    kept = next(project for project in document["projects"] if project["id"] == older)
    assert kept["name"] == "Agent-Fleet" and kept["repositories"] == ["git@github.com:adieyal/agent-fleet.git"]
    assert kept["links"] == [{"host": "gpu", "label": "agent-fleet"}, {"host": "home", "label": "agent-fleet"}]
    assert {project["id"] for project in document["projects"]} == {older, restoke}


def test_merging_projects_of_unknown_age_keeps_the_one_the_user_chose(deck, config_path):
    set_config(config_path, projects={"p-0000000a": {"name": "A", "links": [], "repositories": []},
                                      "p-0000000b": {"name": "B", "links": [], "repositories": [], "created_at": 1.0}})
    status, body = post(deck, "/api/merge", {"keep": "p-0000000b", "other": "p-0000000a"})
    assert status == 200 and body["project_id"] == "p-0000000b"   # the user said which to keep
    assert post(deck, "/api/merge", {"keep": "p-0000000b", "other": "p-0000000b"})[0] == 400
    assert post(deck, "/api/merge", {"keep": "p-0000000b", "other": "p-00000000"})[0] == 404
    assert post(deck, "/api/merge", {"keep": "p-0000000b"})[0] == 400


# ------------------------------------------------------------------ shuttering and the storehouse
def building_of(base_url):
    return fetch_state(base_url)["building"]


def housed(base_url, *labels):
    """Register each label on home as a project, one at a time, so they take floors 1, 2, … in order."""
    ids = []
    for label in labels:
        ids.append(register(label.title(), ("home", label)))
        fetch_state(base_url)
    return ids


def test_shuttering_frees_the_floor_and_keeps_the_project(deck, config_path):
    restoke, invoices = housed(deck, "restoke", "invoices")
    status, body = post(deck, "/api/shutter", {"project": restoke})
    assert status == 200 and body == {"project_id": restoke, "floor": 1}

    document = fetch_state(deck)
    assert document["building"]["floors"] == {invoices: 2}
    assert document["building"]["shuttered"][restoke]["floor"] == 1
    assert document["building"]["no_floor"] == []
    project = next(project for project in document["projects"] if project["id"] == restoke)
    assert project["links"] == [{"host": "home", "label": "restoke"}]                 # same ID, same links
    home = next(host for host in document["hosts"] if host["name"] == "home")
    assert next(job for job in home["jobs"] if job["project"] == "restoke")["project_id"] == restoke   # runs still belong
    assert open_workspace().shuttered_snapshot()[restoke]["floor"] == 1

    # the free floor is not handed back by itself: a new project moves into it instead
    agent_fleet = register("Agent Fleet", ("home", "agent-fleet"))
    assert building_of(deck)["floors"] == {invoices: 2, agent_fleet: 1}
    assert restoke in building_of(deck)["shuttered"]


def test_restoring_returns_a_project_exactly_as_it_was(deck, config_path):
    restoke, invoices = housed(deck, "restoke", "invoices")
    assert post(deck, "/api/focus", {"focus": "background", "projects": [restoke]})[0] == 200
    before = fetch_state(deck)
    assert post(deck, "/api/shutter", {"project": restoke})[0] == 200
    status, body = post(deck, "/api/restore", {"project": restoke})
    assert status == 200 and body == {"project_id": restoke, "floor": 1}
    after = fetch_state(deck)
    for key in ("floors", "focus", "no_floor"):
        assert after["building"][key] == before["building"][key]
    assert after["building"]["shuttered"] == {}
    assert after["projects"] == before["projects"]
    assert [host["jobs"] for host in after["hosts"]] == [host["jobs"] for host in before["hosts"]]


def test_restoring_takes_the_lowest_free_floor_when_its_own_is_taken(deck, config_path):
    restoke, invoices = housed(deck, "restoke", "invoices")
    post(deck, "/api/shutter", {"project": restoke})
    agent_fleet = register("Agent Fleet", ("home", "agent-fleet"))   # moves into floor 1
    fetch_state(deck)
    status, body = post(deck, "/api/restore", {"project": restoke})
    assert status == 200 and body["floor"] == 3
    assert building_of(deck)["floors"] == {agent_fleet: 1, invoices: 2, restoke: 3}


def test_a_full_building_offers_only_shuttering(deck, config_path):
    set_config(config_path, capacity=2)
    restoke, invoices = housed(deck, "restoke", "invoices")
    post(deck, "/api/shutter", {"project": restoke})
    agent_fleet = register("Agent Fleet", ("home", "agent-fleet"))
    fetch_state(deck)   # full again: agent fleet has floor 1

    # restoring and moving in are refused when full, and nothing changes
    status, body = post(deck, "/api/restore", {"project": restoke})
    assert status == 409 and "full" in body["error"]
    assert post(deck, "/api/move-in", {"host": "gpu", "label": "agent-fleet"})[0] == 409
    assert building_of(deck)["capacity"] == 2 and restoke in building_of(deck)["shuttered"]
    assert len(open_workspace().registry().projects) == 3

    # clearing a floor on the way in is the one way through
    status, body = post(deck, "/api/restore", {"project": restoke, "shutter": invoices})
    assert status == 200 and body["floor"] == 2
    building_state = building_of(deck)
    assert building_state["floors"] == {agent_fleet: 1, restoke: 2}
    assert list(building_state["shuttered"]) == [invoices] and building_state["capacity"] == 2

    status, moved = post(deck, "/api/move-in", {"host": "gpu", "label": "agent-fleet", "shutter": agent_fleet})
    assert status == 200 and moved["floor"] == 1
    assert set(building_of(deck)["shuttered"]) == {invoices, agent_fleet}


def test_shuttering_and_restoring_refuse_what_makes_no_sense(deck, config_path):
    (restoke,) = housed(deck, "restoke")
    assert post(deck, "/api/shutter", {"project": "p-00000000"})[0] == 404
    assert post(deck, "/api/restore", {"project": restoke})[0] == 409          # it isn't in the storehouse
    assert post(deck, "/api/shutter", {"project": restoke})[0] == 200
    assert post(deck, "/api/shutter", {"project": restoke})[0] == 409          # already there
    assert post(deck, "/api/restore", {"project": restoke, "shutter": restoke})[0] == 400   # it holds no floor
    assert post(deck, "/api/shutter", {})[0] == 400
    assert post(deck, "/api/shutter", {"project": restoke}, Origin="http://evil.example")[0] == 403


def test_the_storehouse_survives_a_restart(deck, config_path):
    (restoke,) = housed(deck, "restoke")
    post(deck, "/api/shutter", {"project": restoke})
    server, url = start_deck()
    try:
        assert building_of(url)["shuttered"][restoke]["floor"] == 1
        assert building_of(url)["floors"] == {}
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ capacity from the CLI
def test_capacity_is_set_from_the_cli_only_within_limits(deck, config_path, capsys):
    from fleet import cli
    cli.main(["building", "capacity", "8"])
    assert open_workspace().capacity() == 8
    assert building_of(deck)["capacity"] == 8
    for bad in ("11", "0"):
        with pytest.raises(SystemExit):
            cli.main(["building", "capacity", bad])
    assert open_workspace().capacity() == 8
    assert "hosts" in json.loads(config_path.read_text())   # the rest of the config is kept
