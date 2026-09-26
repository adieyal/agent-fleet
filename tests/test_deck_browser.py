"""Browser smoke tests: the deck against the recorded fleet, at desktop and phone widths."""

import io
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.request import urlopen

import pytest
from PIL import Image, ImageStat
from playwright.sync_api import Browser, Page, expect

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "narrow": {"width": 390, "height": 844}}
# The deck ages jobs against the browser clock; pin it to the moment the fixture was recorded.
PIN_CLOCK = """
let offset = %d * 1000 - Date.now();
const realNow = Date.now.bind(Date);
Date.now = () => realNow() + offset;
window.advanceClock = seconds => { offset += seconds * 1000; };
"""
FINISHED = {"done", "cancelled"}
BLOCKED = {"failed", "stalled"}
ASKING = "home:8e1f0c42-2b7d-4a55-9c1e-7f3a2d6b9e10"     # idle two minutes, with a decision waiting
REVIEWING = "worker:019a7c3e-55d1-7b20-a8f4-3c9e0d1b2a67"  # idle about eighteen minutes


def on_the_floor(fixture_data: dict[str, Any]) -> set[str]:
    """Every job and session the deck draws: finished jobs have left, blocked ones are under their lantern."""
    return {f"{host['name']}:{item['id']}" for host in fixture_data["hosts"]
            for item in host["jobs"] + host["sessions"] if item["status"] not in FINISHED | BLOCKED}


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
    page.wait_for_function(f"window.fleetDeck && fleetDeck.agents().length === {len(on_the_floor(fixture_data))}")
    yield deck
    context.close()


def test_every_project_gets_a_room(deck: Deck, fixture_data: dict[str, Any]) -> None:
    projects = {item["project"] for host in fixture_data["hosts"] for item in host["jobs"] + host["sessions"]}
    rooms = rooms_by_name(deck.page)
    assert set(rooms) == projects
    assert rooms["invoice-parser"]["label"] == "Invoice parser"
    assert deck.errors == []


def test_every_unfinished_job_and_session_is_an_agent(deck: Deck, fixture_data: dict[str, Any]) -> None:
    expected = on_the_floor(fixture_data)
    agents = deck.page.evaluate("fleetDeck.agents()")
    assert {agent["key"] for agent in agents} == expected
    assert sum(agent["kind"] == "session" for agent in agents) == 2
    expect(deck.page.locator("#tags .tag")).to_have_count(len(expected))
    expect(deck.page.locator("#tags .tag.sess")).to_have_count(2)
    expect(deck.page.locator("#legendBody .crew.off")).to_have_count(1)
    assert deck.errors == []


def test_finished_jobs_are_off_the_deck_until_the_chip_shows_them(deck: Deck, fixture_data: dict[str, Any]) -> None:
    page = deck.page
    finished = {f"{host['name']}:{job['id']}" for host in fixture_data["hosts"]
                for job in host["jobs"] if job["status"] in FINISHED}
    assert finished == {"worker:d4f7a2"}
    assert not finished & {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(page.locator("#tags .tag", has_text="d4f7a2")).to_have_count(0)
    chip = page.locator("#toggleFinished")
    expect(chip).to_have_text("1 finished · show")

    chip.dispatch_event("click")
    page.wait_for_function("fleetDeck.agents().some(agent => agent.key === 'worker:d4f7a2')")
    expect(page.locator("#tags .tag", has_text="d4f7a2")).to_have_count(1)
    expect(chip).to_have_text("hide finished")

    chip.dispatch_event("click")
    page.wait_for_function("!fleetDeck.agents().some(agent => agent.key === 'worker:d4f7a2')")
    expect(page.locator("#tags .tag", has_text="d4f7a2")).to_have_count(0)
    expect(chip).to_have_text("1 finished · show")
    assert deck.errors == []


def finish_jobs(base_url: str, statuses: dict[str, str]) -> dict[str, Any]:
    """The server's state document with some jobs finished, as the next state update would bring it."""
    with urlopen(base_url + "/api/state", timeout=5) as response:
        doc = json.load(response)
    for host in doc["hosts"]:
        for job in host["jobs"]:
            job["status"] = statuses.get(f"{host['name']}:{job['id']}", job["status"])
    return doc


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_a_job_that_finishes_walks_out(browser: Browser, base_url: str, fixture_data: dict[str, Any],
                                       motion: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion=motion)
    context.add_init_script(PIN_CLOCK % fixture_data["time"])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/")
    page.wait_for_function(f"window.fleetDeck && fleetDeck.agents().length === {len(on_the_floor(fixture_data))}")
    leaving = {"worker:c90e11": "done", "home:b7d042": "cancelled"}
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, leaving))
    gone = f"!fleetDeck.agents().some(agent => {json.dumps(list(leaving))}.includes(agent.key))"
    if motion == "reduce":
        assert page.evaluate(gone)                                          # removed at once
    else:
        agents = {agent["key"]: agent for agent in page.evaluate("fleetDeck.agents()")}
        assert all(agents[key]["leaving"] for key in leaving)               # a completion moment, then the door
        assert not agents["home:a1c3e9"]["leaving"]
        page.wait_for_function(gone, timeout=60_000)
    expect(page.locator("#tags .tag", has_text="c90e11")).to_have_count(0)
    expect(page.locator("#toggleFinished")).to_have_text("3 finished · show")
    context.close()
    assert errors == []


def test_idle_sessions_rest_without_a_bubble(deck: Deck) -> None:
    page = deck.page
    page.wait_for_function(f"fleetDeck.agents().filter(agent => [{json.dumps(ASKING)}, {json.dumps(REVIEWING)}]"
                           ".includes(agent.key) && agent.clip === 'Sitting').length === 2")
    for room, session, job in [("restoke", "Why does the st", "a1c3e9"), ("agent-fleet", "review the unstaged", "f20a6d")]:
        rooms_on_screen(page, room)
        expect(page.locator("#tags .tag", has_text=job).locator(".bubble")).to_be_visible()
        expect(page.locator("#tags .tag", has_text=session).locator(".bubble")).to_be_hidden()
    page.evaluate("fleetDeck.lookAtRoom(null)")
    assert deck.errors == []


def session_state(base_url: str, seconds_later: int, drop_decisions: bool = False,
                  working: str | None = None) -> dict[str, Any]:
    """The server's state document, optionally without its decision items or with one session back at work."""
    with urlopen(base_url + "/api/state", timeout=5) as response:
        doc = json.load(response)
    if drop_decisions:
        doc["attention"] = [item for item in doc["attention"] if item["kind"] != "decision"]
    for host in doc["hosts"]:
        for session in host["sessions"]:
            if f"{host['name']}:{session['id']}" == working:
                session.update(status="working", updated_at=doc["time"] + seconds_later)
    return doc


def test_sessions_idle_for_half_an_hour_leave_unless_a_decision_waits(browser: Browser, base_url: str,
                                                                      fixture_data: dict[str, Any]) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    context.add_init_script(PIN_CLOCK % fixture_data["time"])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/")
    page.wait_for_function(f"window.fleetDeck && fleetDeck.agents().length === {len(on_the_floor(fixture_data))}")
    live = page.locator("#stats .chip.sess")
    expect(live).to_have_text("2 live · 2 waiting")

    later = 40 * 60                                                     # both now idle for over half an hour
    page.evaluate(f"advanceClock({later})")
    page.wait_for_function(f"!fleetDeck.agents().some(agent => agent.key === {json.dumps(REVIEWING)})")
    assert ASKING in {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(live).to_have_text("2 live · 2 waiting")

    page.evaluate("doc => fleetDeck.apply(doc)", session_state(base_url, later, drop_decisions=True))
    assert not {ASKING, REVIEWING} & {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(live).to_have_text("2 live · 2 waiting")

    page.evaluate("doc => fleetDeck.apply(doc)", session_state(base_url, later, drop_decisions=True, working=REVIEWING))
    assert REVIEWING in {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(live).to_have_text("2 live · 1 waiting")
    context.close()
    assert errors == []


def test_bubbles_show_action_glyphs_and_the_words_stay_a_click_away(deck: Deck) -> None:
    page = deck.page
    expected = {"a1c3e9": "test", "b7d042": "edit", "Why does the st": "ask", "f20a6d": "edit", "c90e11": "think",
                "0a9e3b": "queued"}
    for agent, action in expected.items():
        bubble = page.locator("#tags .tag", has_text=agent).locator(".bubble")
        expect(bubble).to_have_attribute("data-action", action)
        expect(bubble.locator(f'.glyph[data-action="{action}"][role="img"]')).to_have_count(1)
        assert bubble.text_content().strip() == ""                       # no sentence in the bubble
    running = page.locator("#tags .tag", has_text="a1c3e9")
    expect(running.locator(".bubble")).to_have_attribute("title", re.compile("tests"))
    running.dispatch_event("click")
    expect(page.locator("#panel")).to_have_class("open")
    expect(page.locator("#panelBody .evs")).to_contain_text("pnpm vitest run suppliers")
    page.locator("#panel #close").click()
    expect(page.locator("#panel")).not_to_have_class("open")
    expect(page.locator("#feed")).to_contain_text("pnpm vitest run suppliers")   # and the deck log keeps it
    assert deck.errors == []


def test_a_jobs_workarea_shows_its_plan_desk_and_tray(deck: Deck, fixture_data: dict[str, Any]) -> None:
    page = deck.page
    page.locator("#tags .tag", has_text="a1c3e9").dispatch_event("click")
    page.locator("#panelBody [data-workarea]").click()
    workarea = page.locator("#workarea")
    expect(workarea).to_be_visible()
    expect(workarea.locator(".wa-head h2")).to_have_text("restoke")

    # tiles match each bench's step statuses, marked without relying on colour
    jobs = {f"{host['name']}:{job['id']}": job for host in fixture_data["hosts"] for job in host["jobs"]}
    benches = workarea.locator("[data-bench]")
    expect(benches).to_have_count(4)
    marks = {"done": "✓", "running": "●", "failed": "✗", "pending": ""}
    for key in benches.evaluate_all("benches => benches.map(b => b.dataset.bench)"):
        tiles = workarea.locator(f'[data-bench="{key}"] [data-tile]')
        steps = jobs[key]["steps"]
        assert tiles.evaluate_all("tiles => tiles.map(t => t.dataset.status)") == [step["status"] for step in steps]
        assert [text.strip() for text in tiles.locator(".mk").all_inner_texts()] == [marks[step["status"]] for step in steps]
        done = sum(step["status"] == "done" for step in steps)
        expect(workarea.locator(f'[data-bench="{key}"] .criteria')).to_have_attribute("aria-label", f"{done} of {len(steps)} steps done")
    expect(workarea.locator('[data-bench="home:b7d042"] [data-mark="glow"]')).to_have_count(1)

    # the lantern hangs over the question desk, with the room's two items
    lantern = workarea.locator(".wa-lantern")
    expect(lantern).to_have_attribute("data-count", "2")
    hang, desk = lantern.bounding_box(), workarea.locator(".desk-top").bounding_box()
    assert desk["x"] <= hang["x"] + hang["width"] / 2 <= desk["x"] + desk["width"]
    assert hang["y"] + hang["height"] <= desk["y"] + 2
    expect(workarea.locator(".wa-steps path")).to_have_count(4)   # every bench here was used in the last hour

    # the tray opens the job's step report in the reader; Esc closes the reader, then the workarea
    expect(workarea.locator('[data-tray="home:a1c3e9"]')).to_be_disabled()
    workarea.locator('[data-tray="worker:d4f7a2"]').click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdTitle")).to_have_text("Step 2: Draft the article")
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    expect(workarea).to_be_visible()
    page.keyboard.press("Escape")
    expect(workarea).to_be_hidden()
    expect(page.locator("#panel")).to_have_class(re.compile("open"))   # one level at a time
    page.locator("#panel #close").click()
    assert deck.errors == []


def test_document_reader_opens_from_a_failed_jobs_panel(deck: Deck) -> None:
    page = deck.page
    page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    page.locator("#attnPanel [data-close]").click()
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
    assert not any(agent["status"] in BLOCKED for agent in page.evaluate("fleetDeck.agents()"))
    expect(page.locator('.lantern[data-kind="blocker"]')).to_have_count(2)   # the failed job's and the stalled one's
    page.locator('.lantern[data-room="demo-docs"]').dispatch_event("click")
    page.locator("#attnPanel [data-owner]").dispatch_event("click")   # the demo pushes a new state every second
    expect(page.locator("#panelHead h2")).to_have_text("Refresh the onboarding guide screenshots")
    page.locator("#attnPanel [data-close]").dispatch_event("click")
    page.locator("#tags .tag").first.dispatch_event("click")
    expect(page.locator("#panel")).to_have_class("open")
    context.close()
    assert errors == []


def test_demo_androids_finish_and_walk_out(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    assert not any(agent["status"] in FINISHED for agent in page.evaluate("fleetDeck.agents()"))
    leaving = page.wait_for_function("fleetDeck.agents().find(agent => agent.leaving)", timeout=60_000).json_value()
    assert leaving["status"] == "done"
    page.wait_for_function(f"!fleetDeck.agents().some(agent => agent.key === '{leaving['key']}')", timeout=60_000)
    context.close()
    assert errors == []


def test_demo_session_idle_for_an_hour_comes_back_to_work(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    sessions = page.evaluate("fleetDeck.agents().filter(agent => agent.kind === 'session')")
    assert {agent["status"] for agent in sessions} == {"working", "idle"}
    expect(page.locator("#stats .chip.sess")).to_have_text(f"{len(sessions) + 1} live · 2 waiting")
    page.wait_for_function(f"fleetDeck.agents().filter(agent => agent.kind === 'session').length === {len(sessions) + 1}",
                           timeout=60_000)
    expect(page.locator("#stats .chip.sess")).to_have_text(f"{len(sessions) + 1} live · 1 waiting")
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


def test_bubbles_appear_only_in_a_room_you_zoom_into(deck: Deck) -> None:
    page = deck.page
    agents = page.evaluate("fleetDeck.agents()")
    room = next(agent["room"] for agent in agents if agent["key"].endswith(":a1c3e9"))
    none_speak = "[...document.querySelectorAll('#tags .tag')].every(tag => getComputedStyle(tag.firstChild).display === 'none')"
    page.evaluate("fleetDeck.lookAtRoom(null)")                          # the whole deck: no bubbles
    page.wait_for_function(none_speak)
    on_screen = rooms_on_screen(page, room)                              # zooms into the room
    far_away = [agent["key"].split(":")[1] for agent in agents if agent["room"] not in on_screen]
    expect(page.locator("#tags .tag", has_text="a1c3e9").locator(".bubble")).to_be_visible()
    for job in far_away:
        expect(page.locator("#tags .tag", has_text=job).locator(".bubble")).to_be_hidden()
    page.evaluate("fleetDeck.lookAtRoom(null)")
    page.wait_for_function(none_speak)
    assert deck.errors == []


def rooms_on_screen(page: Page, zoomed: str) -> set[str]:
    """Rooms whose middle would be on screen with the view zoomed into another room."""
    page.evaluate(f"fleetDeck.lookAtRoom({json.dumps(zoomed)}, 60)")
    size = page.viewport_size
    return {name for name, room in rooms_by_name(page).items()
            if 0 < room["screen"]["x"] < size["width"] and 0 < room["screen"]["y"] < size["height"]}


def rooms_by_name(page: Page) -> dict[str, dict[str, Any]]:
    return {room["name"]: room for room in page.evaluate("fleetDeck.rooms()")}


def tag_is_calm(page: Page, job_id: str) -> bool:
    """A calm tag has no speech bubble showing."""
    tag = page.locator("#tags .tag", has_text=job_id)
    return tag.evaluate("tag => tag.classList.contains('calm') && getComputedStyle(tag.firstChild).display === 'none'")


def focus_on_server(base_url: str, room: str) -> set[str]:
    """The focus the server resolves for every job and session in a room."""
    with urlopen(base_url + "/api/state", timeout=5) as response:
        hosts = json.load(response)["hosts"]
    return {item["focus"] for host in hosts for item in host["jobs"] + host["sessions"] if item["project"] == room}


def room_colour(page: Page, room: str) -> tuple[float, float]:
    """Mean brightness and saturation of the rendered floor around a room's centre, with overlays hidden."""
    centre = rooms_by_name(page)[room]["screen"]
    viewport = page.viewport_size
    box = {"x": max(0, centre["x"] - 40), "y": max(0, centre["y"] - 30), "width": 80, "height": 60}
    assert box["x"] + 80 <= viewport["width"] and box["y"] + 60 <= viewport["height"]
    style = page.add_style_tag(content="#tags,#floorUi,header,.card,#zoom{visibility:hidden!important}")
    page.wait_for_timeout(100)
    image = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("HSV")
    style.evaluate("tag => tag.remove()")
    _, saturation, value = ImageStat.Stat(image).mean
    return value, saturation


def wait_for_dim(page: Page, room: str, dim: int) -> None:
    page.wait_for_function(f"fleetDeck.rooms().find(room => room.name === '{room}').dim === {dim}")


def test_background_rooms_are_dim_and_quiet(deck: Deck) -> None:
    page = deck.page
    wait_for_dim(page, "invoice-parser", 1)
    rooms = rooms_by_name(page)
    assert {name: (room["focus"], room["dim"]) for name, room in rooms.items()} == {
        "restoke": ("priority", 0), "invoice-parser": ("background", 1), "agent-fleet": ("priority", 0)}
    expect(page.locator('.focus-switch[data-room="invoice-parser"]')).to_have_attribute("data-focus", "background")
    expect(page.locator('.focus-switch[data-room="agent-fleet"]')).to_have_attribute("data-focus", "priority")
    assert tag_is_calm(page, "0a9e3b")
    assert not tag_is_calm(page, "c90e11") and not tag_is_calm(page, "f20a6d")
    assert deck.errors == []


def test_the_switch_dims_the_room_and_nothing_moves(deck: Deck, base_url: str) -> None:
    page = deck.page
    before = {name: (room["x"], room["y"], room["screen"]) for name, room in rooms_by_name(page).items()}
    bright, vivid = room_colour(page, "restoke")
    switch = page.locator('.focus-switch[data-room="restoke"]')
    switch.locator('[data-set="background"]').dispatch_event("click")
    expect(switch).to_have_attribute("data-focus", "background")
    wait_for_dim(page, "restoke", 1)
    assert focus_on_server(base_url, "restoke") == {"background"}
    dim, grey = room_colour(page, "restoke")
    assert dim < bright * 0.75 and grey < vivid * 0.6
    assert tag_is_calm(page, "c90e11") and tag_is_calm(page, "a1c3e9")
    assert {name: (room["x"], room["y"], room["screen"]) for name, room in rooms_by_name(page).items()} == before

    switch.locator('[data-set="priority"]').dispatch_event("click")
    wait_for_dim(page, "restoke", 0)
    assert focus_on_server(base_url, "restoke") == {"priority"}
    assert not tag_is_calm(page, "c90e11")
    assert {name: (room["x"], room["y"], room["screen"]) for name, room in rooms_by_name(page).items()} == before
    assert deck.errors == []


def test_focus_of_an_unregistered_room_survives_reload(deck: Deck, base_url: str, fixture_data: dict[str, Any]) -> None:
    page = deck.page
    page.locator('.focus-switch[data-room="agent-fleet"] [data-set="background"]').dispatch_event("click")
    wait_for_dim(page, "agent-fleet", 1)
    assert focus_on_server(base_url, "agent-fleet") == {"background"}
    page.reload()
    page.wait_for_function(f"window.fleetDeck && fleetDeck.agents().length === {len(on_the_floor(fixture_data))}")
    wait_for_dim(page, "agent-fleet", 1)
    expect(page.locator('.focus-switch[data-room="agent-fleet"]')).to_have_attribute("data-focus", "background")
    assert tag_is_calm(page, "f20a6d")
    page.locator('.focus-switch[data-room="agent-fleet"] [data-set="priority"]').dispatch_event("click")
    wait_for_dim(page, "agent-fleet", 0)
    assert focus_on_server(base_url, "agent-fleet") == {"priority"}
    assert deck.errors == []


def test_the_deck_log_leaves_every_focus_switch_clear(deck: Deck) -> None:
    page = deck.page
    if page.viewport_size["width"] < 760:
        pytest.skip("on phones the log starts closed and the column scrolls under it")
    log = page.locator("#feed").bounding_box()
    switches = page.locator(".focus-switch")
    expect(switches).to_have_count(3)
    for switch in switches.all():
        box = switch.bounding_box()
        overlaps = (box["x"] < log["x"] + log["width"] and log["x"] < box["x"] + box["width"]
                    and box["y"] < log["y"] + log["height"] and log["y"] < box["y"] + box["height"])
        assert not overlaps, f"{switch.get_attribute('data-room')}'s switch is under the deck log"
    assert deck.errors == []


def attention_on_server(base_url: str) -> dict[str, str]:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return {item["owner"]["key"]: item["state"] for item in json.load(response)["attention"]}


def expect_need_you(page: Page, base_url: str) -> None:
    """The header's count is the open items, the same ones the lanterns stand for."""
    open_items = sum(state == "open" for state in attention_on_server(base_url).values())
    expect(page.locator("#needYou b")).to_have_text(str(open_items))


def act_on_every_item(page: Page, action: str, state: str) -> None:
    """Press an action on each listed item in turn, waiting for the pushed state each time."""
    for row in page.locator("#attnPanel .attn-item").all():
        row.locator(f'[data-act="{action}"]').click()
        expect(row).to_have_attribute("data-state", state)


def test_a_room_with_open_items_gets_one_lantern_with_a_count(deck: Deck, base_url: str) -> None:
    page = deck.page
    expect(page.locator(".lantern")).to_have_count(2)
    lantern = page.locator('.lantern[data-room="restoke"]')
    expect(lantern).to_have_count(1)
    expect(lantern).to_have_attribute("data-count", "2")   # the failed job and the session asking a question
    expect(lantern.locator("b")).to_have_text("2")
    expect(lantern).to_have_attribute("data-state", "open")
    expect(page.locator('.lantern[data-room="invoice-parser"] b')).to_have_text("")   # one item: no count
    expect(page.locator('.lantern[data-room="agent-fleet"]')).to_have_count(0)      # a running job and an idle session: nothing needs you
    assert rooms_by_name(page)["agent-fleet"]["attention"] is None
    expect(page.locator("#needYou b")).to_have_text("3")
    expect_need_you(page, base_url)

    before = attention_on_server(base_url)
    lantern.dispatch_event("click")
    expect(page.locator("#attnPanel")).to_be_visible()
    expect(page.locator("#attnPanel .attn-item")).to_have_count(2)
    expect(page.locator('#attnPanel .attn-item[data-kind="decision"] b')).to_contain_text("Keep the double fetch")
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panelHead h2")).to_have_text("Upgrade Django to 5.2")
    expect(page.locator("#panelBody .steps li.failed")).to_contain_text("Bump Django and run the test suite")
    assert attention_on_server(base_url) == before   # reading and opening change nothing
    page.locator("#panel #close").click()
    page.keyboard.press("Escape")
    expect(page.locator("#attnPanel")).to_be_hidden()
    assert deck.errors == []


def test_acknowledging_dims_the_lantern_and_snoozing_hides_it(deck: Deck, base_url: str) -> None:
    page = deck.page
    lantern = page.locator('.lantern[data-room="restoke"]')
    lantern.dispatch_event("click")
    act_on_every_item(page, "acknowledge", "acknowledged")
    expect(lantern).to_have_class("lantern ack")
    expect(lantern).to_have_attribute("data-state", "acknowledged")
    expect(lantern).to_have_attribute("data-count", "2")
    assert set(attention_on_server(base_url).values()) == {"acknowledged", "open"}   # invoice-parser untouched
    expect(page.locator("#needYou b")).to_have_text("1")
    expect_need_you(page, base_url)

    act_on_every_item(page, "snooze", "snoozed")
    expect(lantern).to_have_count(0)
    assert rooms_by_name(page)["restoke"]["attention"] is None
    expect(page.locator("#needYou b")).to_have_text("1")
    act_on_every_item(page, "reopen", "open")
    expect(lantern).to_have_attribute("data-state", "open")
    expect(lantern).not_to_have_class("lantern ack")
    expect(page.locator("#needYou b")).to_have_text("3")
    page.keyboard.press("Escape")
    assert set(attention_on_server(base_url).values()) == {"open"}
    assert deck.errors == []


def test_failed_and_stalled_jobs_leave_the_floor_to_their_lantern(deck: Deck, base_url: str,
                                                                  fixture_data: dict[str, Any]) -> None:
    page = deck.page
    blocked = {f"{host['name']}:{job['id']}" for host in fixture_data["hosts"]
               for job in host["jobs"] if job["status"] in BLOCKED}
    assert blocked == {"home:e1b5c8", "worker:3c71d5"}
    assert not blocked & {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    for key in blocked:
        expect(page.locator("#tags .tag", has_text=key.split(":")[1])).to_have_count(0)
    rooms = rooms_by_name(page)
    assert rooms["restoke"]["attention"] == {"kind": "blocker", "state": "open", "count": 2}
    assert rooms["invoice-parser"]["attention"] == {"kind": "blocker", "state": "open", "count": 1}
    expect(page.locator("#needYou b")).to_have_text("3")
    expect_need_you(page, base_url)
    assert deck.errors == []


def blocked_deck(browser: Browser, base_url: str, fixture_data: dict[str, Any]) -> tuple[Page, list[str]]:
    """A deck of its own, with motion, so what one test changes stays out of the shared deck."""
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    context.add_init_script(PIN_CLOCK % fixture_data["time"])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/")
    page.wait_for_function(f"window.fleetDeck && fleetDeck.agents().length === {len(on_the_floor(fixture_data))}")
    return page, errors


def test_a_job_that_fails_or_stalls_leaves_the_floor_at_once(browser: Browser, base_url: str,
                                                             fixture_data: dict[str, Any]) -> None:
    page, errors = blocked_deck(browser, base_url, fixture_data)
    blocked = {"worker:c90e11": "stalled", "home:b7d042": "failed"}
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, blocked))
    assert not page.evaluate(f"fleetDeck.agents().some(agent => {json.dumps(list(blocked))}.includes(agent.key))")
    expect(page.locator("#tags .tag", has_text="c90e11")).to_have_count(0)
    expect(page.locator("#tags .tag", has_text="b7d042")).to_have_count(0)
    expect(page.locator("#toggleFinished")).to_have_text("1 finished · show")   # blocked is not finished
    expect(page.locator("#needYou b")).to_have_text("3")
    page.context.close()
    assert errors == []


def test_a_failed_job_can_still_be_dismissed_from_its_panel(browser: Browser, base_url: str,
                                                            fixture_data: dict[str, Any]) -> None:
    page, errors = blocked_deck(browser, base_url, fixture_data)
    page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panelHead h2")).to_have_text("Upgrade Django to 5.2")
    page.locator("#panel #dismiss").click()
    expect(page.locator("#panel")).not_to_have_class(re.compile("open"))
    chip = page.locator("#restoreDismissed")
    expect(chip).to_have_text("1 hidden · show")
    expect(page.locator('#attnPanel [data-owner="home:e1b5c8"]')).to_have_count(0)   # nothing to open while hidden
    expect(page.locator('.lantern[data-room="restoke"]')).to_have_attribute("data-count", "2")
    chip.click()
    expect(chip).to_have_count(0)
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panel")).to_have_class(re.compile("open"))
    page.context.close()
    assert errors == []
