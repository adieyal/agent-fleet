"""The sprite-world floor in the app (fleet/web/js/world/floor-view.js): a view beside the deck, chosen with the header
switch and remembered, the deck staying the default; the project's live jobs at desks they keep; host colour and kit,
agent face; a robot's click opens its job panel; names only at close zoom, within a text budget; a job that needs you
has a lantern over its desk."""

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page, expect

READY = "window.fleetWorld && (fleetWorld.ready || fleetWorld.error)"
REST = "!fleetWorld.engine.camera.moving"
WORD = re.compile(r"[^\W_][\w'’-]*")


def state(base_url: str) -> dict[str, Any]:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return json.load(response)


def running_restoke(doc: dict[str, Any]) -> set[str]:
    return {f"{h['name']}:{j['id']}" for h in doc["hosts"] for j in h["jobs"] if j["project"] == "restoke" and j["status"] == "running"}


@pytest.fixture
def world(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.goto(base_url + "/?view=world")
    page.wait_for_function(READY, timeout=60_000)
    assert page.evaluate("fleetWorld.error") is None
    yield page
    page.close()
    assert errors == []


def members(page: Page) -> dict[str, dict[str, Any]]:
    return {m["key"]: m for m in page.evaluate("fleetWorld.state().members")}


def frame(page: Page, which: str) -> None:
    page.evaluate(f"fleetWorld.frame('{which}')")
    page.wait_for_function(REST, timeout=15_000)
    page.wait_for_timeout(300)


def test_the_deck_is_the_default_and_the_world_is_remembered(browser: Browser, base_url: str) -> None:
    page = browser.new_page()
    try:
        page.goto(base_url + "/")
        page.wait_for_function("window.fleetDeck")
        expect(page.locator("body")).to_have_attribute("data-view", "deck")
        expect(page.locator("#floorWorld")).to_be_hidden()
        page.locator('#viewToggle [data-view="world"]').click()
        expect(page.locator("body")).to_have_attribute("data-view", "world")
        expect(page.locator('#viewToggle [data-view="world"]')).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#floorWorld")).to_be_visible()
        expect(page.locator("#world")).to_be_hidden()   # (the deck's canvas)
        page.wait_for_function(READY, timeout=60_000)
        page.reload()
        expect(page.locator("body")).to_have_attribute("data-view", "world")
        page.wait_for_function(READY, timeout=60_000)
        page.locator('#viewToggle [data-view="deck"]').click()
        expect(page.locator("body")).to_have_attribute("data-view", "deck")
        page.reload()
        expect(page.locator("body")).to_have_attribute("data-view", "deck")
    finally:
        page.close()


def test_the_floor_seats_the_projects_live_jobs_in_their_hosts_looks(world: Page, base_url: str) -> None:
    doc = state(base_url)
    assert world.evaluate("fleetWorld.state().label") == "restoke"   # the busiest floor
    expect(world.locator("#floorWorldProject")).to_have_value("restoke")
    seated = members(world)
    assert set(seated) == running_restoke(doc)   # the finished job has left, as on the deck
    looks = world.evaluate("""keys => import('/js/looks.js').then(L => Object.fromEntries(keys.map(k => {
      const h = L.hostLook(k.split(':')[0]); return [k, [h.color, h.acc]]; })))""", list(seated))
    agents = {f"{h['name']}:{j['id']}": j["agent"] for h in doc["hosts"] for j in h["jobs"]}
    for key, m in seated.items():
        assert m["state"] == "arrived" and not m["leaving"]
        assert [m["host"], m["kit"]] == looks[key] and m["agent"] == agents[key]
    # the robots' composed sprites carry the agent's face: a Codex robot's eyes, a Claude robot's band
    faces = world.evaluate("""fleetWorld.state().members.map(m => fleetWorld.robots.layers({ ...m, kit: m.kit }, 'Typing', 'high')[1])""")
    assert sorted(faces) == sorted("face_eyes" if m["agent"] == "codex" else "face_band" for m in seated.values())


def test_every_job_keeps_its_desk_as_others_come_and_go_and_across_reloads(world: Page, base_url: str) -> None:
    before = world.evaluate("fleetWorld.state().desks")
    assert sorted(before.values()) == [0, 1, 2]   # the workarea bench first
    doc = state(base_url)
    home = next(h for h in doc["hosts"] if h["name"] == "home")
    worker = next(h for h in doc["hosts"] if h["name"] == "worker")
    first = min((j for h in doc["hosts"] for j in h["jobs"] if f"{h['name']}:{j['id']}" in before), key=lambda j: j["created_at"])
    gone = next(f"{h['name']}:{j['id']}" for h in doc["hosts"] for j in h["jobs"] if j is first)
    # the oldest job goes, and two new ones arrive, one created before everything else
    for h in doc["hosts"]:
        h["jobs"] = [j for j in h["jobs"] if j is not first]
    new = [{**first, "id": "n0ld", "created_at": first["created_at"] - 1000, "description": "Older new job"},
           {**first, "id": "n3w", "created_at": first["created_at"] + 5000, "description": "Newer new job"}]
    home["jobs"] += new[:1]
    worker["jobs"] += new[1:]
    world.evaluate("doc => fleetDeck.apply(doc)", doc)
    after = world.evaluate("fleetWorld.state().desks")
    for key, desk in before.items():
        if key != gone:
            assert after[key] == desk   # nobody moved
    assert gone not in after
    assert {after["home:n0ld"], after["worker:n3w"]} == {before[gone], 3}   # the free desk first, in creation order
    world.reload()
    world.wait_for_function(READY, timeout=60_000)
    world.evaluate("doc => fleetDeck.apply(doc)", doc)
    assert world.evaluate("fleetWorld.state().desks") == after   # the same after a reload


def test_clicking_a_robot_opens_its_job_panel(world: Page, base_url: str) -> None:
    frame(world, "near")
    key = sorted(members(world))[0]
    x, y = world.evaluate("k => fleetWorld.robotAt(k)", key)
    world.mouse.click(x, y)
    expect(world.locator("#panel")).to_have_class(re.compile(r"\bopen\b"))
    job = next(j for h in state(base_url)["hosts"] for j in h["jobs"] if f"{h['name']}:{j['id']}" == key)
    expect(world.locator("#panelHead h2")).to_have_text(job["description"])
    world.keyboard.press("Escape")


def test_names_show_only_at_close_zoom_within_the_text_budget(world: Page) -> None:
    ui = "document.getElementById('floorWorldUi')"
    frame(world, "far")
    expect(world.locator("#floorWorldUi .name:visible")).to_have_count(0)
    expect(world.locator("#floorWorldUi .bubble:visible")).to_have_count(0)
    far_words = world.evaluate(f"fleetDeck.textBudget({ui})")
    frame(world, "near")
    names = world.locator("#floorWorldUi .name:visible").all_inner_texts()
    assert len(names) == len(members(world))
    assert all(1 <= len(WORD.findall(n)) <= 3 for n in names)
    near_words = world.evaluate(f"fleetDeck.textBudget({ui})")
    # the whole floor: only numbers (the lift's and the lantern's); a bench close up: names of at most three words each
    assert far_words <= 12 and near_words <= far_words + 3 * len(names)
    assert world.evaluate("fleetDeck.textBudget(document.getElementById('floorWorld'))") <= 40


def test_a_job_that_needs_you_hangs_a_lantern_over_its_desk(world: Page, base_url: str) -> None:
    doc = state(base_url)
    key = sorted(running_restoke(doc))[0]
    doc["attention"].append({"id": "att-test", "kind": "decision", "state": "open", "project": "restoke", "summary": "Ship it?",
                             "owner": {"key": key, "kind": "job"}})
    world.evaluate("doc => fleetDeck.apply(doc)", doc)
    lanterns = world.evaluate("fleetWorld.state().lanterns")
    at_desk = [lantern for lantern in lanterns if lantern["place"] == f"run:{key}"]
    assert len(at_desk) == 1 and at_desk[0]["count"] == 1
    seat = members(world)[key]["at"]
    assert abs(at_desk[0]["at"][0] - seat[0]) < 0.8 and abs(at_desk[0]["at"][1] - seat[1]) < 0.3   # over its desk
    # the floor's other items still hang over the question desk
    assert any(lantern["place"] == "attention" for lantern in lanterns)


def test_a_finished_job_leaves_and_a_new_one_walks_in(browser: Browser, base_url: str) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="no-preference")
    try:
        page.goto(base_url + "/?view=world")
        page.wait_for_function(READY, timeout=60_000)
        doc = state(base_url)
        key = sorted(running_restoke(doc))[0]
        host, jid = key.split(":")
        job = next(j for h in doc["hosts"] if h["name"] == host for j in h["jobs"] if j["id"] == jid)
        job["status"] = "done"
        page.evaluate("doc => fleetDeck.apply(doc)", doc)
        page.wait_for_function(f"fleetWorld.state().members.some(m => m.key === '{key}' && m.leaving)", timeout=10_000)
        page.wait_for_function(f"!fleetWorld.state().members.some(m => m.key === '{key}')", timeout=40_000)   # back to the lift
        page.evaluate("doc => fleetDeck.apply(doc)", doc)
        assert key not in page.evaluate("fleetWorld.state().desks")   # retired: it doesn't come back
        home = next(h for h in doc["hosts"] if h["name"] == host)
        home["jobs"].append({**job, "id": "fresh1", "status": "running", "created_at": doc["time"]})
        page.evaluate("doc => fleetDeck.apply(doc)", doc)
        page.wait_for_function("fleetWorld.state().members.some(m => m.key.endsWith(':fresh1') && m.state === 'walking')", timeout=10_000)
        page.wait_for_function("fleetWorld.state().members.some(m => m.key.endsWith(':fresh1') && m.state === 'arrived')", timeout=40_000)
    finally:
        page.close()
