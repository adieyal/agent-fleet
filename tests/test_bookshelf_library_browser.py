"""A project's bookshelf opens its library: openProjectLibrary (packages/fleet-web/src/fleet_web/static/js/library.js) chooses the project on the
overview as its project button would, and the bookcases in a deck room and the shelves on a world floor open it, with
a hand and a '<project> library' tip on hover."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

RESTOKE = "p-5e1f0a01"   # the fixture's registered Restoke project: its jobs carry this ID


@pytest.fixture
def page(browser: Browser, base_url: str, fixture_data: dict[str, Any]) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.goto(base_url + "/")
    page.wait_for_function("window.fleetDeck && fleetDeck.rooms().some(r => r.library)")
    yield page
    page.close()
    assert errors == []


def shoot(request: pytest.FixtureRequest, page: Page, name: str) -> None:
    if request.config.getoption("--shots"):
        page.screenshot(path=f"{request.config.getoption('--shots')}/{name}.png")


def expect_library_of(page: Page, key: str) -> None:
    expect(page.locator("#libraryPane")).to_be_visible()
    expect(page.locator('#libraryPane [data-lib-view="overview"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(f'#libProjects [data-project-key="{key}"]')).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("localStorage.getItem('fleet.library.project')") == key


def test_open_project_library_chooses_the_project_on_the_overview(page: Page) -> None:
    page.evaluate("import('/js/library.js').then(L => L.openProjectLibrary('library:agent-fleet'))")
    expect_library_of(page, "library:agent-fleet")
    page.locator('#libraryPane [data-lib-view="all"]').click()
    page.evaluate(f"import('/js/library.js').then(L => L.openProjectLibrary('{RESTOKE}'))")   # back to the overview
    expect_library_of(page, RESTOKE)
    expect(page.locator("#libList .ov-summary")).to_be_visible()
    # a project with neither an overview nor documents still opens, and says so
    page.evaluate("import('/js/library.js').then(L => L.openProjectLibrary('p-0000', 'Ghost'))")
    expect(page.locator("#libList")).to_have_text("Ghost has no documents yet.")
    expect(page.locator("#libProjects [aria-pressed=true]")).to_have_count(0)


def test_clicking_a_deck_rooms_bookcase_opens_its_library(page: Page, request: pytest.FixtureRequest) -> None:
    rooms = {r["name"]: r for r in page.evaluate("fleetDeck.rooms()")}
    assert rooms["restoke"]["library"] == RESTOKE and rooms["agent-fleet"]["library"] == "library:agent-fleet"
    page.evaluate("fleetDeck.lookAtRoom('restoke', 55); fleetDeck.advanceTime(0)")
    shelf = next(r for r in page.evaluate("fleetDeck.rooms()") if r["name"] == "restoke")["shelves"][0]
    page.mouse.move(shelf["x"], shelf["y"])
    expect(page.locator("#world")).to_have_class(re.compile(r"\bhot\b"))
    expect(page.locator("#docTip")).to_contain_text("restoke library")
    page.evaluate("fleetDeck.advanceTime(0)")
    shoot(request, page, "deck-bookshelf-hover")
    page.mouse.click(shelf["x"], shelf["y"])
    expect(page.locator("#docTip")).to_be_hidden()
    expect_library_of(page, RESTOKE)
    expect(page.locator("#panel")).not_to_have_class(re.compile(r"\bopen\b"))


def test_clicking_a_world_floors_bookshelf_opens_its_library(page: Page, request: pytest.FixtureRequest) -> None:
    page.locator('#viewToggle [data-view="world"]').click()
    page.wait_for_function("window.fleetWorld && fleetWorld.ready")
    assert page.evaluate("fleetWorld.state().label") == "restoke"
    page.evaluate("fleetWorld.frame('far')")
    page.wait_for_function("!fleetWorld.engine.camera.moving", timeout=15_000)
    x, y = page.evaluate("fleetWorld.shelfAt('shelf-a')")
    box = page.locator("#floorWorldCanvas").bounding_box()
    page.mouse.move(box["x"] + x, box["y"] + y)
    expect(page.locator("#floorWorldCanvas")).to_have_class(re.compile(r"\bhot\b"))
    expect(page.locator("#floorWorldUi .shelf-tip")).to_have_text("Restoke library")
    shoot(request, page, "world-bookshelf-hover")
    page.mouse.click(box["x"] + x, box["y"] + y)
    expect(page.locator("#floorWorldUi .shelf-tip")).to_be_hidden()
    expect_library_of(page, RESTOKE)
    # the lower shelf opens it too; a robot's click still opens its panel (test_world_app_browser)
    page.locator("#libraryPane button[data-lib-close]").click()
    x, y = page.evaluate("fleetWorld.shelfAt('shelf-b')")
    page.mouse.click(box["x"] + x, box["y"] + y)
    expect_library_of(page, RESTOKE)
