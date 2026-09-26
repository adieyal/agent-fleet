"""Browser smoke tests: the deck against the recorded fleet, at desktop and phone widths."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page, expect

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "narrow": {"width": 390, "height": 844}}
# The deck ages jobs against the browser clock; pin it to the moment the fixture was recorded.
PIN_CLOCK = """
const offset = %d * 1000 - Date.now();
const realNow = Date.now.bind(Date);
Date.now = () => realNow() + offset;
"""


@dataclass
class Deck:
    page: Page
    errors: list[str] = field(default_factory=list)


@pytest.fixture(scope="module", params=list(VIEWPORTS))
def deck(request: pytest.FixtureRequest, browser: Browser, base_url: str,
         fixture_data: dict[str, Any]) -> Iterator[Deck]:
    """One page per viewport, loaded once; each test leaves it with nothing open."""
    context = browser.new_context(viewport=VIEWPORTS[request.param], reduced_motion="reduce")
    context.add_init_script(PIN_CLOCK % fixture_data["time"])
    page = context.new_page()
    deck = Deck(page)
    page.on("console", lambda message: message.type == "error" and deck.errors.append(message.text))
    page.on("pageerror", lambda error: deck.errors.append(str(error)))
    page.goto(base_url + "/")
    agent_count = sum(len(host["jobs"]) + len(host["sessions"]) for host in fixture_data["hosts"])
    page.wait_for_function(f"window.fleetDeck && fleetDeck.agents().length === {agent_count}")
    yield deck
    context.close()


def test_every_project_gets_a_room(deck: Deck, fixture_data: dict[str, Any]) -> None:
    projects = {item["project"] for host in fixture_data["hosts"] for item in host["jobs"] + host["sessions"]}
    rooms = rooms_by_name(deck.page)
    assert set(rooms) == projects
    assert rooms["invoice-parser"]["label"] == "Invoice parser"
    assert deck.errors == []


def test_every_job_and_session_is_an_agent(deck: Deck, fixture_data: dict[str, Any]) -> None:
    expected = {f"{host['name']}:{item['id']}" for host in fixture_data["hosts"]
                for item in host["jobs"] + host["sessions"]}
    agents = deck.page.evaluate("fleetDeck.agents()")
    assert {agent["key"] for agent in agents} == expected
    assert sum(agent["kind"] == "session" for agent in agents) == 2
    expect(deck.page.locator("#tags .tag")).to_have_count(len(expected))
    expect(deck.page.locator("#tags .tag.sess")).to_have_count(2)
    expect(deck.page.locator("#legendBody .crew.off")).to_have_count(1)
    assert deck.errors == []


def test_document_reader_opens_from_an_agent(deck: Deck) -> None:
    page = deck.page
    page.locator("#tags .tag", has_text="e1b5c8").dispatch_event("click")
    expect(page.locator("#panel")).to_have_class("open")
    expect(page.locator("#panelHead h2")).to_have_text("Upgrade Django to 5.2")
    page.locator('#panelBody [data-doc="report-0"]').click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdBody h1")).to_have_text("Django 5.2 upgrade blocked")
    expect(page.locator("#rdBody")).not_to_contain_text("FLEET_STATUS")
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    page.locator("#panel #close").click()
    expect(page.locator("#panel")).not_to_have_class("open")
    assert deck.errors == []


def test_demo_mode_fills_the_deck_without_errors(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    expect(page.locator("#live")).to_contain_text("demo data")
    assert len(page.evaluate("fleetDeck.rooms()")) > 1
    page.locator("#tags .tag").first.dispatch_event("click")
    expect(page.locator("#panel")).to_have_class("open")
    context.close()
    assert errors == []


def test_library_lists_and_opens_documents(deck: Deck) -> None:
    page = deck.page
    page.locator("#libraryOpen").click()
    expect(page.locator("#libList .lib-doc")).to_have_count(3)
    expect(page.locator("#libList .lib-group h3")).to_have_text(["agent-fleet", "restoke"])
    page.locator('#libList .lib-doc[data-id="docs/suppliers-v2.md"]').click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdBody h1")).to_have_text("Suppliers V2")
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    page.locator(".lib-head [data-lib-close]").click()
    expect(page.locator("#libraryPane")).to_be_hidden()
    assert deck.errors == []


def rooms_by_name(page: Page) -> dict[str, dict[str, Any]]:
    return {room["name"]: room for room in page.evaluate("fleetDeck.rooms()")}


def tag_shown(page: Page, job_id: str) -> bool:
    return page.locator("#tags .tag", has_text=job_id).evaluate("tag => tag.style.display !== 'none'")


def focus_on_server(base_url: str, project_id: str) -> str:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return next(project["focus"] for project in json.load(response)["projects"] if project["id"] == project_id)


def test_background_rooms_are_closed_and_keep_their_androids_quiet(deck: Deck) -> None:
    page = deck.page
    page.wait_for_function("fleetDeck.rooms().find(room => room.name === 'invoice-parser').closed")
    rooms = rooms_by_name(page)
    assert {name: (room["project"], room["focus"], room["open"]) for name, room in rooms.items()} == {
        "restoke": ("p-5e1f0a01", "priority", True),
        "invoice-parser": ("p-1c0ce5a2", "background", False),
        "agent-fleet": (None, None, True)}
    expect(page.locator('.focus-switch[data-room="invoice-parser"]')).to_have_attribute("data-focus", "background")
    unregistered = page.locator('.focus-switch[data-room="agent-fleet"]')
    expect(unregistered).to_have_attribute("data-focus", "none")
    expect(unregistered.locator("button:disabled")).to_have_count(2)
    expect(unregistered).to_have_attribute("title", re.compile("Not a registered project"))
    assert not tag_shown(page, "0a9e3b") and not tag_shown(page, "3c71d5")
    assert tag_shown(page, "c90e11") and tag_shown(page, "f20a6d")
    assert deck.errors == []


def test_the_switch_changes_focus_and_nothing_moves(deck: Deck, base_url: str) -> None:
    page = deck.page
    places = {name: (room["x"], room["y"]) for name, room in rooms_by_name(page).items()}
    switch = page.locator('.focus-switch[data-room="restoke"]')
    switch.locator('[data-set="background"]').dispatch_event("click")
    expect(switch).to_have_attribute("data-focus", "background")
    page.wait_for_function("fleetDeck.rooms().find(room => room.name === 'restoke').closed")
    assert focus_on_server(base_url, "p-5e1f0a01") == "background"
    assert not tag_shown(page, "c90e11")
    switch.locator('[data-set="priority"]').dispatch_event("click")
    page.wait_for_function("fleetDeck.rooms().find(room => room.name === 'restoke').open")
    assert focus_on_server(base_url, "p-5e1f0a01") == "priority"
    assert tag_shown(page, "c90e11")
    assert {name: (room["x"], room["y"]) for name, room in rooms_by_name(page).items()} == places
    assert deck.errors == []
