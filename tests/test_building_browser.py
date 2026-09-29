"""The building view (L0) in the browser: the view switch, a ten-floor building on one screen, open, windowed and
free floors, the text budget, lanterns, entering floors and the lift, the focus switch, shuttering and the storehouse,
and moving in.

One server per recorded fleet and one browser context per motion setting serve the whole module, so the 3D assets
load once. Tests that change a fleet put it back as they found it (reopen, undo, restore, flip back); the two that
can't (moving a visitor in, clearing a floor for one) come last for their fleet.
"""

import io
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest
from PIL import Image, ImageStat
from playwright.sync_api import Browser, BrowserContext, Page, expect

from conftest import FIXTURE, serve_fixture

TEN_FLOORS = Path(__file__).parent / "fixtures" / "building10.json"
DESKTOP = {"width": 1440, "height": 900}
HEADER = 52
INVOICES = "p-1c0ce5a2"   # Invoice analysis: floor 2 in the Restoke fleet, in the background, with a failed job


@pytest.fixture(scope="module")
def ten_floors_url() -> Iterator[str]:
    with serve_fixture(TEN_FLOORS) as url:
        yield url


@pytest.fixture(scope="module")
def restoke_url() -> Iterator[str]:
    """Its own server: moving in changes the registry, which the deck's tests share."""
    with serve_fixture(FIXTURE) as url:
        yield url


@pytest.fixture(scope="module")
def visitor_asks_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The Restoke fleet with a question from a visitor: work no floor holds."""
    fixture = json.loads(FIXTURE.read_text())
    worker = next(host for host in fixture["hosts"] if host["name"] == "worker")
    session = next(session for session in worker["sessions"] if session["project"] == "agent-fleet")
    session["activity"] = {"kind": "tool", "name": "AskUserQuestion", "summary": "Ship it?", "ts": fixture["time"] - 60}
    path = tmp_path_factory.mktemp("visitor") / "visitor.json"
    path.write_text(json.dumps(fixture))
    with serve_fixture(path) as url:
        yield url


@pytest.fixture(scope="module")
def linking_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The Restoke fleet where some visitors belong to projects already: agent-fleet runs on home and worker and an
    "Agent Fleet" project (floor 3) links neither; fleet-docs on worker is a clone of Restoke's repository. And
    "invoice-parser" on home was moved in as a second project (floor 4) for the work "Invoice analysis" holds."""
    fixture = json.loads(FIXTURE.read_text())
    worker = next(host for host in fixture["hosts"] if host["name"] == "worker")
    home = next(host for host in fixture["hosts"] if host["name"] == "home")
    session = next(session for session in worker["sessions"] if session["project"] == "agent-fleet")
    home["sessions"].append({**session, "id": "home-fleet", "host": "home"})
    worker["sessions"].append({**session, "id": "docs", "project": "fleet-docs", "cwd": "/src/fleet-docs"})
    home["sessions"].append({**session, "id": "home-invoices", "host": "home", "project": "invoice-parser"})
    fixture["remotes"] = {"worker": {"/src/fleet-docs": ["https://github.com/restoke/restoke"]}}
    fixture["projects"]["p-5e1f0a01"]["repositories"] = ["git@github.com:restoke/restoke.git"]
    fixture["projects"]["p-1c0ce5a2"]["created_at"] = 1790000000.0
    fixture["projects"]["p-0000fa01"] = {"name": "Agent Fleet", "links": [], "repositories": []}
    fixture["projects"]["p-00001c02"] = {"name": "Invoice parser", "links": [{"host": "home", "label": "invoice-parser"}],
                                         "repositories": [], "created_at": 1790300000.0}
    fixture["floors"] = {"p-5e1f0a01": 1, "p-1c0ce5a2": 2, "p-0000fa01": 3, "p-00001c02": 4}
    path = tmp_path_factory.mktemp("linking") / "linking.json"
    path.write_text(json.dumps(fixture))
    with serve_fixture(path) as url:
        yield url


@pytest.fixture(scope="module")
def still(browser: Browser) -> Iterator[BrowserContext]:
    context = browser.new_context(viewport=DESKTOP, reduced_motion="reduce")
    yield context
    context.close()


@pytest.fixture(scope="module")
def moving(browser: Browser) -> Iterator[BrowserContext]:
    """Motion allowed, so lanterns swing."""
    context = browser.new_context(viewport=DESKTOP, reduced_motion="no-preference")
    yield context
    context.close()


def page_in(context: BrowserContext) -> Iterator[Page]:
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    page.close()
    assert errors == []


@pytest.fixture
def page(still: BrowserContext) -> Iterator[Page]:
    yield from page_in(still)


@pytest.fixture(scope="module")
def reading_page(still: BrowserContext) -> Iterator[Page]:
    yield from page_in(still)


@pytest.fixture
def moving_page(moving: BrowserContext) -> Iterator[Page]:
    yield from page_in(moving)


def open_building(page: Page, url: str) -> list[dict[str, Any]]:
    if page.url != url + "/":
        page.goto(url + "/")
    page.locator('#viewToggle [data-view="building"]').click()
    page.wait_for_function("fleetBuilding.floors().length > 0 && fleetBuilding.floors().every(floor => floor.built)")
    settle(page)
    return page.evaluate("fleetBuilding.floors()")


def settle(page: Page) -> None:
    """Wait for the next two frames: the building draws on demand and places its labels after drawing."""
    page.evaluate("new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")


def state(url: str) -> dict[str, Any]:
    with urlopen(url + "/api/state", timeout=5) as response:
        return json.load(response)


def post(url: str, path: str, body: dict[str, Any]) -> int:
    request = Request(url + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return response.status


def lanterns(page: Page) -> dict[Any, dict[str, Any]]:
    return {lantern["place"]: lantern for lantern in page.evaluate("fleetBuilding.lanterns()")}


@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_stored_attention_display(browser: Browser, restoke_url: str, motion: str) -> None:
    with browser.new_context(viewport=DESKTOP, reduced_motion=motion) as context:
        page = context.new_page()
        open_building(page, restoke_url)
        page.evaluate("fleetDeck.advanceTime(0)")
        before = state(restoke_url)
        page.evaluate("""doc => {
            for (const marker of doc.attention_display.places) marker.open_ids = marker.open_ids.map(id => id + ':arrival');
            fleetDeck.apply(doc);
        }""", before)
        lamps = page.evaluate("fleetDeck.lanterns()")
        assert [(l["place"], l["count"], l["glyph"]) for l in lamps] == [(1, 2, "✱"), (2, 1, "✱")]
        page.locator('#buildingUi').evaluate("el => el.style.filter = 'grayscale(1)'")
        expect(page.locator('.floor-lantern[data-place="1"] .lg')).to_have_text("✱")
        assert all(l["swinging"] == (motion == "no-preference") for l in lamps)
        page.evaluate("fleetDeck.advanceTime(3)")
        assert not any(l["swinging"] for l in page.evaluate("fleetDeck.lanterns()"))
        page.evaluate("doc => fleetDeck.apply(doc)", before)
        assert not any(l["swinging"] for l in page.evaluate("fleetDeck.lanterns()"))
        expect(page.locator('[data-attention-context]')).to_have_count(3)
        page.locator('.front-desk summary').click()
        page.locator('[data-attention-context]').first.click()
        expect(page.locator('#reader')).to_be_visible()
        expect(page.locator('#rdBody')).to_contain_text(before["attention"][0]["summary"])
        expect(page.locator('#rdBody')).to_contain_text(before["attention"][0]["context_reference"])
        expect(page.locator('#rdBody')).to_contain_text("Last seen:")
        assert state(restoke_url)["attention"] == before["attention"]
        page.keyboard.press("Escape")
        page.locator('[data-enter="1"]').click()
        expect(page.locator('#lift [data-lift="1"] .lift-lantern')).to_have_attribute("data-glyph", "✱")
        assert not any(l["place"] == 3 for l in page.evaluate("fleetDeck.lanterns()"))


# ------------------------------------------------------------------ the view switch
def test_the_deck_stays_the_default_and_the_choice_is_remembered(page: Page, restoke_url: str) -> None:
    page.goto(restoke_url + "/")
    page.evaluate("localStorage.removeItem('fleet.view')")   # the context is shared: start from a first visit
    page.reload()
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    expect(page.locator("body")).to_have_attribute("data-view", "deck")
    expect(page.locator("#world")).to_be_visible()
    expect(page.locator("#building")).to_be_hidden()

    page.locator('#viewToggle [data-view="building"]').click()
    expect(page.locator("#building")).to_be_visible()
    expect(page.locator("#world")).to_be_hidden()
    expect(page.locator('#viewToggle [data-view="building"]')).to_have_attribute("aria-pressed", "true")
    page.reload()
    expect(page.locator("body")).to_have_attribute("data-view", "building")
    expect(page.locator("#building")).to_be_visible()

    page.locator('#viewToggle [data-view="deck"]').click()
    expect(page.locator("#world")).to_be_visible()
    page.reload()
    expect(page.locator("body")).to_have_attribute("data-view", "deck")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    expect(page.locator("#tags .tag").first).to_be_visible()


# ------------------------------------------------------------------ the building at L0
def test_ten_floors_and_the_lobby_fit_one_desktop_screen(reading_page: Page, ten_floors_url: str) -> None:
    page = reading_page
    floors = open_building(page, ten_floors_url)
    assert [floor["floor"] for floor in floors] == list(range(1, 11))
    lobby = page.evaluate("fleetBuilding.lobby().screen")
    for rect in [floor["screen"] for floor in floors] + [lobby]:
        assert rect["left"] >= 0 and rect["right"] <= DESKTOP["width"]
        assert rect["top"] >= HEADER and rect["bottom"] <= DESKTOP["height"]
    assert page.evaluate("[document.documentElement.scrollWidth, document.documentElement.scrollHeight]") == [1440, 900]
    # legible: every storey gets a good share of the screen, and each name plate sits clear of its neighbours
    plates = [page.locator(f'.plate[data-floor="{floor}"]').bounding_box() for floor in range(1, 11)]
    for plate in plates:
        assert plate["x"] >= 0 and plate["x"] + plate["width"] <= DESKTOP["width"]
    for lower, upper in zip(plates, plates[1:]):
        assert lower["y"] - upper["y"] >= 50
        assert upper["y"] + upper["height"] <= lower["y"]
    expect(page.locator(".plate")).to_have_count(10)


def floor_pixels(page: Page, floor: dict[str, Any]) -> tuple[float, float, float]:
    """Mean red, green and blue across a band through a floor's front, with the plates and lobby hidden."""
    screen = floor["screen"]
    height = (screen["bottom"] - screen["top"]) * 0.3
    box = {"x": screen["left"] + (screen["right"] - screen["left"]) * 0.2, "y": (screen["top"] + screen["bottom"]) / 2 - height / 2,
           "width": (screen["right"] - screen["left"]) * 0.7, "height": height}
    style = page.add_style_tag(content="#buildingUi{visibility:hidden!important}")
    settle(page)
    image = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB")
    style.evaluate("tag => tag.remove()")
    return tuple(ImageStat.Stat(image).mean)


def test_priority_floors_are_open_and_background_floors_windowed(reading_page: Page, ten_floors_url: str) -> None:
    page = reading_page
    fixture = json.loads(TEN_FLOORS.read_text())
    background = set(fixture["focus"]["projects"])
    floors = open_building(page, ten_floors_url)
    active = {item["project_id"] for host in state(ten_floors_url)["hosts"] for item in host["jobs"] + host["sessions"]
              if item["project_id"] and item["status"] in ("running", "working")}
    for floor in floors:
        built = floor["built"]
        assert floor["active"] == (floor["project"] in active)
        top = "-top" if floor["floor"] == len(floors) else ""
        lit = "-lit" if floor["active"] else "-unlit"
        if floor["project"] in background:
            assert built["mode"] == "windowed" and built["front"] == "windowed"
            assert built["piece"] == f"floor-glass{top}{lit}"      # behind glass
        else:
            assert built["mode"] == "open" and built["front"] == "none"
            assert built["piece"] == f"floor-open{top}{lit}"       # the furnished open office
        assert built["glow"] == floor["active"]
        expect(page.locator(f'.plate[data-floor="{floor["floor"]}"]')).to_have_attribute("data-mode", built["mode"])

    # warm light shows where runs are active, softly through the windows too; a busy background floor stays quieter
    # than a busy open one, so busy never looks important
    def warmth(floor: dict[str, Any]) -> float:
        red, _, blue = floor_pixels(page, floor)
        return red - blue

    def brightness(floor: dict[str, Any]) -> float:
        return sum(floor_pixels(page, floor)) / 3
    windowed = [floor for floor in floors if floor["mode"] == "windowed"]
    busy = next(floor for floor in windowed if floor["active"])
    attention_projects = {item["project_id"] for item in state(ten_floors_url)["attention"]}
    quiet_signal = [floor for floor in windowed if floor["active"] and floor["project"] not in attention_projects]
    assert quiet_signal
    lit_places = {lamp["place"] for lamp in page.evaluate("fleetDeck.lanterns()")}
    assert all(floor["floor"] not in lit_places for floor in quiet_signal)
    quiet = next(floor for floor in windowed if not floor["active"])
    assert warmth(busy) > warmth(quiet) + 4
    busy_open = [floor for floor in floors if floor["mode"] == "open" and floor["active"]]
    assert brightness(busy) + 4 < min(brightness(floor) for floor in busy_open)
    # and nothing about the building changes when a floor's work is active or not: every storey is the same size
    heights = {round(floor["screen"]["bottom"] - floor["screen"]["top"]) for floor in floors}
    assert len(heights) == 1


def test_open_floors_are_lit_while_their_projects_rooms_work(reading_page: Page, ten_floors_url: str) -> None:
    page = reading_page
    floors = {floor["floor"]: floor for floor in open_building(page, ten_floors_url)}
    # Agent Fleet (floor 3) has two rooms on the deck: agent-fleet, working, and agent-fleet-docs, idle
    rooms = floors[3]["rooms"]
    assert [(room["label"], room["active"]) for room in rooms] == [("agent-fleet", True), ("agent-fleet-docs", False)]
    assert floors[3]["built"]["glow"]
    open_floors = [floor for floor in floors.values() if floor["mode"] == "open"]
    assert any(floor["built"]["glow"] for floor in open_floors) and not all(floor["built"]["glow"] for floor in open_floors)
    for floor in open_floors:   # warm light only while one of its rooms is working
        assert floor["built"]["glow"] == any(room["active"] for room in floor["rooms"])


def test_free_floors_are_to_let(reading_page: Page, restoke_url: str) -> None:
    page = reading_page
    floors = open_building(page, restoke_url)
    document = state(restoke_url)
    assert document["building"]["capacity"] == 6 and len(floors) == 6
    occupied = set(document["building"]["floors"].values())
    for floor in floors:
        plate = page.locator(f'.plate[data-floor="{floor["floor"]}"]')
        if floor["floor"] in occupied:
            assert floor["mode"] in ("open", "windowed")
            expect(plate.locator("[data-progress=unknown]")).to_have_count(1)   # no plans yet: unknown, never zero
        else:
            assert floor["mode"] == "to-let" and floor["built"]["front"] == "to-let" and floor["project"] is None
            expect(plate).to_have_attribute("data-mode", "to-let")
            expect(plate.locator("b")).to_have_text("To let")
            expect(plate.locator(".progress")).to_have_count(0)


WORD = re.compile(r"[^\W_][\w'’-]*")
SENTENCE = re.compile(r"[^\W_][.!?](\s|$)")


def visible_texts(page: Page) -> list[str]:
    """The text of every visible element in the building that holds text directly."""
    return page.evaluate("""() => [...document.querySelectorAll('#building *')]
      .filter(el => el.checkVisibility() && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
      .map(el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim())""")


@pytest.mark.parametrize("fixture", ["ten_floors_url", "restoke_url"])
def test_the_building_keeps_to_its_text_budget(reading_page: Page, fixture: str, request: pytest.FixtureRequest) -> None:
    page = reading_page
    open_building(page, request.getfixturevalue(fixture))
    names = page.locator(".plate b").all_inner_texts()
    assert names and all(len(WORD.findall(name)) <= 3 for name in names)
    texts = visible_texts(page)
    assert texts
    for text in texts:
        assert len(WORD.findall(text)) <= 3, f"more than three words at L0: {text!r}"
        assert not SENTENCE.search(text), f"a sentence at L0: {text!r}"
    # no androids and no speech at L0
    expect(page.locator("#tags")).to_be_hidden()
    expect(page.locator("#world")).to_be_hidden()


def test_work_without_a_floor_waits_in_the_lobby(reading_page: Page, ten_floors_url: str) -> None:
    page = reading_page
    open_building(page, ten_floors_url)
    visitors = page.locator(".lobby .visitor")
    expect(visitors).to_have_count(2)
    assert {(row.get_attribute("data-hosts"), row.get_attribute("data-label")) for row in visitors.all()} == {
        ("home", "scratch"), ("worker", "notes")}
    expect(page.locator(".lobby .novacancy")).to_be_visible()
    expect(page.locator("#building").get_by_text("No vacancies")).to_have_count(1)   # one sign in the lobby is enough
    expect(visitors.locator("button")).to_have_count(2)
    for button in visitors.locator("button").all():
        expect(button).to_have_text("Move in")
    # the lobby's list sits beside the building and covers no floor
    card = page.locator(".lobby").bounding_box()
    for floor in page.evaluate("fleetBuilding.floors()"):
        screen = floor["screen"]
        assert card["x"] + card["width"] <= screen["left"] or card["y"] >= screen["bottom"]
    expect(page.locator('.hostkey .host[data-host="gpu-box"]')).to_have_class("host off")
    expect(page.locator('.hostkey .host[data-host="home"]')).to_have_class("host")


# ------------------------------------------------------------------ attention
def test_attention_rolls_up_to_one_lantern_per_floor(moving_page: Page, restoke_url: str) -> None:
    page, url = moving_page, restoke_url
    open_building(page, url)
    # restoke (floor 1): a failed job and a session asking a question; invoice analysis (floor 2): a failed job
    page.wait_for_function("fleetBuilding.lanterns().length === 2")
    assert {place: (lantern["level"], lantern["count"], lantern["swings"]) for place, lantern in lanterns(page).items()} == {
        1: ("open", 2, 1), 2: ("open", 1, 1)}
    expect(page.locator(".floor-lantern")).to_have_count(2)
    restoke = page.locator('.floor-lantern[data-place="1"]')
    expect(restoke.locator(".lg")).to_have_text("✱")
    expect(restoke.locator("b")).to_have_text("2")
    expect(page.locator('.floor-lantern[data-place="2"] b')).to_have_text("")   # one item: no count
    expect(page.locator('.floor-lantern[data-place="lobby"]')).to_have_count(0)
    # each hangs on a bracket from the spine, right beside its floor's name plate (the swing turns only the diamond)
    for floor in (1, 2):
        hang = page.locator(f'.lantern-hang[data-floor="{floor}"]').bounding_box()
        plate = page.locator(f'.plate[data-floor="{floor}"]').bounding_box()
        assert abs(hang["x"] + hang["width"] - plate["x"]) <= 2
        assert plate["y"] <= hang["y"] + 27 <= plate["y"] + plate["height"]

    items = [item for item in state(url)["attention"] if item["project"] == "restoke"]
    for item in items:
        post(url, "/api/attention/acknowledge", {"id": item["id"]})
    expect(restoke).to_have_attribute("data-state", "acknowledged")
    expect(restoke).to_have_class("floor-lantern ack")
    for item in items:
        post(url, "/api/attention/snooze", {"id": item["id"], "seconds": 3600})
    expect(restoke).to_have_count(0)
    for item in items:
        post(url, "/api/attention/reopen", {"id": item["id"]})
    expect(restoke).to_have_attribute("data-state", "open")
    # it swung once when the items arrived; coming back from a snooze is not an arrival
    assert lanterns(page)[1]["swings"] == 1


def test_attention_with_no_floor_hangs_by_the_lobby(page: Page, visitor_asks_url: str) -> None:
    open_building(page, visitor_asks_url)
    lobby = page.locator('.floor-lantern[data-place="lobby"]')
    expect(lobby).to_have_count(1)
    expect(lobby).to_have_attribute("data-state", "open")
    assert lanterns(page)["lobby"]["count"] == 1


# ------------------------------------------------------------------ entering floors and the lift
def enter_by_click(page: Page, floor: int) -> None:
    """Click the middle of a floor's front, clear of the lobby's list."""
    screen = next(f for f in page.evaluate("fleetBuilding.floors()") if f["floor"] == floor)["screen"]
    page.mouse.click((screen["left"] + screen["right"]) / 2, screen["top"] + (screen["bottom"] - screen["top"]) * 0.4)


def test_clicking_a_floor_enters_it(page: Page, restoke_url: str) -> None:
    open_building(page, restoke_url)
    enter_by_click(page, 1)
    expect(page.locator("body")).to_have_attribute("data-view", "floor")
    expect(page.locator("#world")).to_be_visible()
    page.wait_for_function("fleetDeck.rooms().map(room => room.name).join() === 'restoke'")
    agents = page.evaluate("fleetDeck.agents()")
    assert agents and {agent["room"] for agent in agents} == {"restoke"}
    expect(page.locator("#lift")).to_be_visible()
    expect(page.locator('#lift [aria-current="true"]')).to_have_attribute("data-lift", "1")
    # the deck's own lantern for the room is still there to open
    expect(page.locator('.lantern[data-room="restoke"]')).to_have_count(1)


def test_the_lift_goes_between_floors_and_back_to_the_building(page: Page, restoke_url: str) -> None:
    open_building(page, restoke_url)
    occupied = set(state(restoke_url)["building"]["floors"].values())
    page.locator('.plate[data-floor="1"] .enter').click()
    lift = page.locator("#lift")
    expect(lift.locator("button")).to_have_count(8)   # six floors, L and S
    assert lift.locator("button").all_inner_texts() == ["6", "5", "4", "3", "2", "1", "L", "S"]
    expect(lift.locator('[data-lift="S"]')).to_be_enabled()
    for free in {1, 2, 3, 4, 5, 6} - occupied:
        expect(lift.locator(f'[data-lift="{free}"]')).to_be_disabled()
    expect(lift.locator(".lift-lantern")).to_have_count(2)   # floors 1 and 2 need you
    expect(lift.locator('[data-lift="2"] .lift-lantern')).to_have_count(1)

    lift.locator('[data-lift="2"]').click()
    expect(lift.locator('[aria-current="true"]')).to_have_attribute("data-lift", "2")
    page.wait_for_function("fleetDeck.rooms().map(room => room.name).join() === 'invoice-parser'")

    lift.locator('[data-lift="L"]').click()
    expect(page.locator("body")).to_have_attribute("data-view", "building")
    expect(lift).to_be_hidden()
    page.wait_for_function("fleetDeck.rooms().length === 3")   # the deck behind has everything again

    # Esc steps out one level, but only once whatever is open over the deck has closed
    page.locator('.plate[data-floor="1"] .enter').click()
    expect(page.locator("body")).to_have_attribute("data-view", "floor")
    page.locator("#tags .tag", has_text="a1c3e9").dispatch_event("click")   # a running job: failed ones have left the floor
    expect(page.locator("#panel")).to_have_class("open")
    page.keyboard.press("Escape")
    expect(page.locator("#panel")).not_to_have_class("open")
    expect(page.locator("body")).to_have_attribute("data-view", "floor")
    page.keyboard.press("Escape")
    expect(page.locator("body")).to_have_attribute("data-view", "building")

    # S rides to the storehouse
    page.locator('.plate[data-floor="1"] .enter').click()
    lift.locator('[data-lift="S"]').click()
    expect(page.locator("body")).to_have_attribute("data-view", "building")
    expect(page.locator(".storehouse")).to_be_visible()


# ------------------------------------------------------------------ focus
def layout(page: Page) -> dict[str, Any]:
    """Where every floor, plate and lantern is on screen."""
    return {"floors": [floor["screen"] for floor in page.evaluate("fleetBuilding.floors()")],
            "plates": [plate.bounding_box() for plate in page.locator(".plate").all()],
            "lanterns": [lantern.bounding_box() for lantern in page.locator(".floor-lantern").all()]}


def test_the_focus_switch_changes_a_floor_and_moves_nothing(page: Page, ten_floors_url: str) -> None:
    url = ten_floors_url
    open_building(page, url)
    before = layout(page)
    plate = page.locator('.plate[data-floor="1"]')
    project = plate.get_attribute("data-project")
    plate.locator('.fswitch [data-focus="background"]').click()
    expect(plate).to_have_attribute("data-mode", "windowed")
    page.wait_for_function("fleetBuilding.floors()[0].built.mode === 'windowed'")
    assert state(url)["building"]["focus"][project] == "background"
    expect(page.locator("body")).to_have_attribute("data-view", "building")   # a switch is not a way in
    settle(page)
    assert layout(page) == before

    plate.locator('.fswitch [data-focus="priority"]').click()
    page.wait_for_function("fleetBuilding.floors()[0].built.mode === 'open'")
    assert state(url)["building"]["focus"][project] == "priority"
    settle(page)
    assert layout(page) == before


# ------------------------------------------------------------------ shuttering and the storehouse
def test_shuttering_packs_a_floor_away_and_undo_brings_it_back(page: Page, restoke_url: str) -> None:
    url = restoke_url
    open_building(page, url)
    page.locator('.shutter-handle[data-floor="2"]').click()   # one pull, no confirmation

    plate = page.locator('.plate[data-floor="2"]')
    expect(plate).to_have_attribute("data-mode", "to-let")
    toast = page.locator("#toast")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("Invoice analysis is packed away in the storehouse. Floor 2 is free.")
    assert [crate["id"] for crate in page.evaluate("fleetBuilding.crates()")] == [INVOICES]
    expect(page.locator(".annex-sign b")).to_have_text("1")
    # its attention goes to the front desk and the storehouse door, never the floor it left
    assert set(lanterns(page)) == {1, "lobby", "store"}
    expect(page.locator('.floor-lantern[data-place="2"]')).to_have_count(0)
    assert state(url)["building"]["shuttered"][INVOICES]["floor"] == 2
    # what is painted follows: the floor is drawn empty and the storehouse holds a crate, without a reload
    page.wait_for_function("fleetBuilding.floors().find(f => f.floor === 2).built?.piece === 'floor-free'", timeout=5000)
    page.wait_for_function("fleetBuilding.pieces().some(p => p.name === 'annex-1')", timeout=5000)

    toast.locator("[data-undo]").click()
    expect(plate).to_have_attribute("data-mode", "windowed")          # as it was: its floor, its focus
    expect(plate).to_have_attribute("data-project", INVOICES)
    expect(toast).to_be_hidden()
    assert page.evaluate("fleetBuilding.crates()") == []
    assert set(lanterns(page)) == {1, 2}
    building = state(url)["building"]
    assert building["floors"][INVOICES] == 2 and building["focus"][INVOICES] == "background" and building["shuttered"] == {}


def test_a_crate_opens_read_only_and_moves_back_to_its_floor(page: Page, restoke_url: str) -> None:
    url = restoke_url
    open_building(page, url)
    page.locator('.shutter-handle[data-floor="2"]').click()
    expect(page.locator('.plate[data-floor="2"]')).to_have_attribute("data-mode", "to-let")
    assert INVOICES in state(url)["building"]["shuttered"]   # no undo: it stays packed away

    page.locator(".annex-sign").click()
    crate = page.locator(f'.storehouse .crate[data-project="{INVOICES}"]')
    expect(crate.locator("b")).to_have_text("Invoice analysis")
    expect(crate.locator(".runs")).to_have_text("2 runs")           # its runs still show, in the storehouse

    crate.locator("[data-open-crate]").click()
    expect(page.locator("body")).to_have_attribute("data-view", "floor")
    expect(page.locator("body")).to_have_attribute("data-readonly", "")
    page.wait_for_function("fleetDeck.rooms().map(room => room.name).join() === 'invoice-parser'")
    expect(page.locator("#readonlyTag")).to_be_visible()
    expect(page.locator(".focus-switch").first).to_be_hidden()      # nothing to change in a crate
    expect(page.locator('#lift [aria-current="true"]')).to_have_attribute("data-lift", "S")

    page.keyboard.press("Escape")                                   # back out to the storehouse
    expect(page.locator("body")).to_have_attribute("data-view", "building")
    expect(page.locator("body")).not_to_have_attribute("data-readonly", "")
    page.locator(f'.storehouse .crate[data-project="{INVOICES}"] [data-restore]').click()
    expect(page.locator('.plate[data-floor="2"]')).to_have_attribute("data-project", INVOICES)
    expect(page.locator(".storehouse .crate")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.locator(".storehouse")).to_have_count(0)
    assert state(url)["building"]["shuttered"] == {}


# ------------------------------------------------------------------ moving in: these change their fleet for good, so come last
def test_a_visitor_moves_in_to_the_lowest_free_floor(page: Page, restoke_url: str) -> None:
    open_building(page, restoke_url)
    visitor = page.locator('.lobby .visitor[data-label="agent-fleet"]')
    expect(visitor).to_have_attribute("data-hosts", "worker")
    visitor.locator("[data-move-in]").click()
    expect(page.locator('.plate[data-floor="3"]')).not_to_have_attribute("data-mode", "to-let")
    expect(page.locator('.plate[data-floor="3"] b')).to_have_text("agent-fleet")
    expect(page.locator('.lobby .visitor[data-label="agent-fleet"]')).to_have_count(0)
    document = state(restoke_url)
    project = next(project for project in document["projects"] if project["name"] == "agent-fleet")
    assert project["links"] == [{"host": "worker", "label": "agent-fleet"}]
    assert document["building"]["floors"][project["id"]] == 3


def test_a_full_building_offers_only_clearing_a_floor_or_cancelling(page: Page, ten_floors_url: str) -> None:
    url = ten_floors_url
    open_building(page, url)
    notes = page.locator('.lobby .visitor[data-label="notes"] button')
    notes.click()
    prompt = page.locator(".vacancy")
    expect(prompt).to_be_visible()
    expect(prompt.locator("[data-clear]")).to_have_count(10)
    expect(prompt.locator("button")).to_have_count(11)                 # a floor to clear, or cancel: nothing else
    prompt.locator("[data-cancel]").click()
    expect(prompt).to_have_count(0)
    assert state(url)["building"]["shuttered"] == {}

    notes.click()
    top = next(project for project, floor in state(url)["building"]["floors"].items() if floor == 10)
    page.locator(f'.vacancy [data-clear="{top}"]').click()
    expect(page.locator('.plate[data-floor="10"] b')).to_have_text("notes")
    expect(page.locator(".vacancy")).to_have_count(0)
    building = state(url)["building"]
    assert building["capacity"] == 10 and list(building["shuttered"]) == [top]
    assert [crate["name"] for crate in page.evaluate("fleetBuilding.crates()")] == ["Research notes"]


# ------------------------------------------------------------------ linking and merging: these change their fleet too
def test_visitors_are_grouped_by_label_with_their_hosts(page: Page, linking_url: str) -> None:
    open_building(page, linking_url)
    row = page.locator('.lobby .visitor[data-label="agent-fleet"]')
    expect(row).to_have_count(1)
    expect(row).to_have_attribute("data-hosts", "home worker")
    expect(row.locator(".vhosts i")).to_have_text(["home", "worker"])


def test_moving_in_offers_linking_first_and_linking_takes_no_floor(page: Page, linking_url: str) -> None:
    open_building(page, linking_url)
    before = state(linking_url)["building"]["floors"]
    page.locator('.lobby .visitor[data-label="agent-fleet"] [data-move-in]').click()
    prompt = page.locator(".movein")
    expect(prompt).to_be_visible()
    expect(prompt.locator("[data-host-pick]:checked")).to_have_count(2)   # both hosts, and the user can drop one
    first = prompt.locator("button").first
    expect(first).to_have_attribute("data-link", "p-0000fa01")
    expect(first).to_contain_text("Link to Agent Fleet (floor 3)")
    expect(prompt.locator("[data-new-project]")).to_have_text("New project")
    first.click()
    expect(prompt).to_have_count(0)
    expect(page.locator('.lobby .visitor[data-label="agent-fleet"]')).to_have_count(0)
    document = state(linking_url)
    project = next(project for project in document["projects"] if project["id"] == "p-0000fa01")
    assert project["links"] == [{"host": "home", "label": "agent-fleet"}, {"host": "worker", "label": "agent-fleet"}]
    assert document["building"]["floors"] == before and len(document["projects"]) == 4


def test_a_matching_repository_is_offered_and_a_new_project_stays_possible(page: Page, linking_url: str) -> None:
    open_building(page, linking_url)
    page.locator('.lobby .visitor[data-label="fleet-docs"] [data-move-in]').click()
    prompt = page.locator(".movein")
    link = prompt.locator('[data-link="p-5e1f0a01"]')
    expect(link).to_contain_text("Link to Restoke (floor 1)")
    expect(link).to_contain_text("same repository")
    prompt.locator("[data-new-project]").click()
    expect(page.locator('.plate[data-floor="5"] b')).to_have_text("fleet-docs")
    restoke = next(project for project in state(linking_url)["projects"] if project["id"] == "p-5e1f0a01")
    assert {"host": "worker", "label": "fleet-docs"} not in restoke["links"]


def test_merging_from_a_floor_keeps_the_older_project_and_frees_the_floor(page: Page, linking_url: str) -> None:
    open_building(page, linking_url)
    page.locator('.plate[data-floor="4"] [data-merge]').click()
    dialog = page.locator(".merge")
    dialog.locator('[data-merge-with="p-1c0ce5a2"]').click()
    expect(dialog).to_contain_text("Invoice analysis is older, so it stays")
    dialog.locator('[data-merge-keep="p-1c0ce5a2"]').click()
    expect(page.locator('.plate[data-floor="4"]')).to_have_attribute("data-mode", "to-let")
    expect(page.locator("#toast")).to_contain_text("Floor 4 is free")
    document = state(linking_url)
    assert "p-00001c02" not in {project["id"] for project in document["projects"]}
    invoices = next(project for project in document["projects"] if project["id"] == "p-1c0ce5a2")
    assert invoices["links"] == [{"host": "home", "label": "invoice-parser"}, {"host": "worker", "label": "invoice-parser"}]
    assert document["building"]["floors"]["p-1c0ce5a2"] == 2
