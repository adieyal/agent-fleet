"""The building view (L0) in the browser: the view switch, a ten-floor building on one screen, open, windowed and
free floors, the text budget, and moving a visitor in."""

import io
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest
from PIL import Image, ImageStat
from playwright.sync_api import Browser, Page, expect

from conftest import FIXTURE, serve_fixture

TEN_FLOORS = Path(__file__).parent / "fixtures" / "building10.json"
DESKTOP = {"width": 1440, "height": 900}
HEADER = 52


@pytest.fixture(scope="module")
def ten_floors_url() -> Iterator[str]:
    with serve_fixture(TEN_FLOORS) as url:
        yield url


@pytest.fixture(scope="module")
def restoke_url() -> Iterator[str]:
    """Its own server: moving in changes the registry, which the deck's tests share."""
    with serve_fixture(FIXTURE) as url:
        yield url


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport=DESKTOP, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert errors == []


def open_building(page: Page, url: str) -> list[dict[str, Any]]:
    page.goto(url + "/")
    page.locator('#viewToggle [data-view="building"]').click()
    page.wait_for_function("fleetBuilding.floors().length > 0 && fleetBuilding.floors().every(floor => floor.built)")
    return page.evaluate("fleetBuilding.floors()")


def state(url: str) -> dict[str, Any]:
    with urlopen(url + "/api/state", timeout=5) as response:
        return json.load(response)


def test_the_deck_stays_the_default_and_the_choice_is_remembered(page: Page, restoke_url: str) -> None:
    page.goto(restoke_url + "/")
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


def test_ten_floors_and_the_lobby_fit_one_desktop_screen(page: Page, ten_floors_url: str) -> None:
    floors = open_building(page, ten_floors_url)
    assert [floor["floor"] for floor in floors] == list(range(1, 11))
    lobby = page.evaluate("fleetBuilding.lobby().screen")
    for rect in [floor["screen"] for floor in floors] + [lobby]:
        assert rect["left"] >= 0 and rect["right"] <= DESKTOP["width"]
        assert rect["top"] >= HEADER and rect["bottom"] <= DESKTOP["height"]
    assert page.evaluate("[document.documentElement.scrollWidth, document.documentElement.scrollHeight]") == [1440, 900]
    # legible: every storey gets a good share of the screen, and each name plate sits clear of its neighbours
    plates = [page.locator(f'.plate[data-floor="{floor}"]').bounding_box() for floor in range(1, 11)]
    for lower, upper in zip(plates, plates[1:]):
        assert lower["y"] - upper["y"] >= 50
        assert upper["y"] + upper["height"] <= lower["y"]
    expect(page.locator(".plate")).to_have_count(10)


def floor_pixels(page: Page, floor: dict[str, Any]) -> tuple[float, float, float]:
    """Mean red, green and blue across the middle of a floor's front, with the plates and lobby hidden."""
    screen = floor["screen"]
    height = (screen["bottom"] - screen["top"]) * 0.3
    box = {"x": screen["left"] + (screen["right"] - screen["left"]) * 0.35, "y": (screen["top"] + screen["bottom"]) / 2 - height / 2,
           "width": (screen["right"] - screen["left"]) * 0.3, "height": height}
    style = page.add_style_tag(content="#buildingUi{visibility:hidden!important}")
    page.wait_for_timeout(100)
    image = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB")
    style.evaluate("tag => tag.remove()")
    return tuple(ImageStat.Stat(image).mean)


def test_priority_floors_are_open_and_background_floors_windowed(page: Page, ten_floors_url: str) -> None:
    fixture = json.loads(TEN_FLOORS.read_text())
    background = set(fixture["focus"]["projects"])
    floors = open_building(page, ten_floors_url)
    active = {item["project_id"] for host in state(ten_floors_url)["hosts"] for item in host["jobs"] + host["sessions"]
              if item["project_id"] and item["status"] in ("running", "working")}
    for floor in floors:
        built = floor["built"]
        assert floor["active"] == (floor["project"] in active)
        if floor["project"] in background:
            assert built["mode"] == "windowed" and built["front"] == "windowed"
            assert built["furniture"] == 0 and not built["pennant"]
        else:
            assert built["mode"] == "open" and built["front"] == "none"
            assert built["furniture"] > 0 and built["pennant"]
        assert built["glow"] == floor["active"]
        expect(page.locator(f'.plate[data-floor="{floor["floor"]}"]')).to_have_attribute("data-mode", built["mode"])

    # warm light shows where runs are active, through the windows too
    def warmth(floor: dict[str, Any]) -> float:
        red, _, blue = floor_pixels(page, floor)
        return red - blue
    windowed = [floor for floor in floors if floor["mode"] == "windowed"]
    busy = next(floor for floor in windowed if floor["active"])
    quiet = next(floor for floor in windowed if not floor["active"])
    assert warmth(busy) > warmth(quiet) + 20
    # and nothing about the building changes when a floor's work is active or not: every storey is the same size
    heights = {round(floor["screen"]["bottom"] - floor["screen"]["top"]) for floor in floors}
    assert len(heights) == 1


def test_free_floors_are_to_let(page: Page, restoke_url: str) -> None:
    floors = open_building(page, restoke_url)
    document = state(restoke_url)
    assert document["building"]["capacity"] == 6 and len(floors) == 6
    by_floor = {floor["floor"]: floor for floor in floors}
    occupied = {floor for floor in document["building"]["floors"].values()}
    for number, floor in by_floor.items():
        plate = page.locator(f'.plate[data-floor="{number}"]')
        if number in occupied:
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
def test_the_building_keeps_to_its_text_budget(page: Page, fixture: str, request: pytest.FixtureRequest) -> None:
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


def test_work_without_a_floor_waits_in_the_lobby(page: Page, ten_floors_url: str) -> None:
    open_building(page, ten_floors_url)
    visitors = page.locator(".lobby .visitor")
    expect(visitors).to_have_count(2)
    assert {(row.get_attribute("data-host"), row.get_attribute("data-label")) for row in visitors.all()} == {
        ("home", "scratch"), ("worker", "notes")}
    expect(page.locator(".lobby .novacancy")).to_be_visible()
    expect(visitors.locator("button")).to_have_count(2)
    for button in visitors.locator("button").all():
        expect(button).to_be_disabled()
    expect(page.locator('.hostkey .host[data-host="gpu-box"]')).to_have_class("host off")
    expect(page.locator('.hostkey .host[data-host="home"]')).to_have_class("host")


def test_a_visitor_moves_in_to_the_lowest_free_floor(page: Page, restoke_url: str) -> None:
    open_building(page, restoke_url)
    visitor = page.locator('.lobby .visitor[data-label="agent-fleet"]')
    expect(visitor).to_have_attribute("data-host", "worker")
    visitor.locator("[data-move-in]").click()
    expect(page.locator('.plate[data-floor="3"]')).not_to_have_attribute("data-mode", "to-let")
    expect(page.locator('.plate[data-floor="3"] b')).to_have_text("agent-fleet")
    expect(page.locator('.lobby .visitor[data-label="agent-fleet"]')).to_have_count(0)
    document = state(restoke_url)
    project = next(project for project in document["projects"] if project["name"] == "agent-fleet")
    assert project["links"] == [{"host": "worker", "label": "agent-fleet"}]
    assert document["building"]["floors"][project["id"]] == 3
