"""Browser tests: a project's library opens on its overview, and every trace in it opens in the reader."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from conftest import serve_fixture
from fleet.web.fixture import FixtureState
from overview_fixture import overview_fixture, repository
from test_deck_browser import PIN_CLOCK, VIEWPORTS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def overview_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    state = FixtureState(overview_fixture(repository(tmp_path_factory.mktemp("libraries") / "agent-fleet")))
    with serve_fixture(state) as url:
        yield url


def open_library(browser: Browser, url: str, time: float, viewport: str = "desktop") -> tuple[Page, list[str]]:
    context = browser.new_context(viewport=VIEWPORTS[viewport], reduced_motion="reduce")
    context.add_init_script(PIN_CLOCK % (time, time) + "localStorage.removeItem('fleet.library.project');")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "/")
    page.wait_for_function("window.fleetDeck")
    page.locator("#libraryOpen").click()
    return page, errors


def shoot(request: pytest.FixtureRequest, page: Page, name: str) -> None:
    if request.config.getoption("--shots"):
        page.screenshot(path=str(Path(request.config.getoption("--shots")) / f"{name}.png"))


def choose(page: Page, name: str) -> None:
    page.locator("#libProjects button", has_text=name).click()


def test_the_overview_shows_workstreams_and_opens_their_traces(
        browser: Browser, overview_url: str, fixture_data: dict[str, Any], request: pytest.FixtureRequest) -> None:
    page, errors = open_library(browser, overview_url, fixture_data["time"])
    choose(page, "Restoke")
    summary = page.locator("#libList .ov-summary")
    expect(summary).to_contain_text("7 workstreams: 3 in progress, 1 paused, 1 blocked, 2 done.")
    expect(summary).to_contain_text("Ralph loop: V2 suppliers, slice 4")
    expect(page.locator("#libList .ov h4")).to_have_text(["Active now", "Paused and blocked", "Done · 2", "Other work",
                                                                 "Other documents · 1"])
    cards = page.locator("#libList .ov > .ws")
    # active: slice 4 and the two running jobs linked to no folder; then the review (paused) and slice 6 (blocked)
    expect(cards.locator(".ws-state")).to_have_text(["in progress"] * 3 + ["paused", "blocked"])
    expect(cards.nth(0)).to_contain_text("Shadow-parse 60 invoices")
    expect(cards.nth(2)).to_contain_text("slice 4")
    expect(cards.nth(2)).to_contain_text("14/15 stories · 1 job running · last activity")
    expect(cards.nth(3)).to_contain_text("V2 architecture review")
    expect(cards.nth(3)).to_contain_text("last activity 2d ago")
    expect(cards.nth(4)).to_contain_text("0/15 stories · 43 open questions · last activity 2d ago")
    done = page.locator("#libList .ov-done")
    expect(done).not_to_have_attribute("open", "")               # done work stays folded
    expect(done.locator(":scope > summary")).to_have_text("Done · 2")
    expect(page.locator("#libList .ov-week h5").first).to_have_text("Week of 21 Sep 2026")
    shoot(request, page, "overview-restoke")

    draft = cards.nth(4)
    draft.locator(":scope > summary").click()
    expect(draft.locator(".ws-body")).to_contain_text("43 open questions of 43 in questions.md")
    expect(draft.locator(".ws-body")).to_contain_text("prd.json is drafted and has not been reviewed")
    expect(draft.locator(".ws-body > .ws-list").nth(1).locator("li")).to_have_count(3)   # Next: three lines, the rest folded
    expect(draft.locator(".ws-more summary").first).to_have_text("12 more")
    shoot(request, page, "overview-restoke-expanded")

    draft.locator(".ws-body .tr", has_text="43 open questions").click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdTitle")).to_contain_text("Slice 6")
    page.keyboard.press("Escape")

    draft.locator(".chip-tr", has_text="PRD").click()            # prd.json as a page, not JSON
    expect(page.locator("#rdKind")).to_have_text("PRD")
    expect(page.locator("#rdBody h2", has_text="Stories · 0 of 15 passing")).to_be_visible()
    expect(page.locator("#rdBody input[type=checkbox]")).to_have_count(15)
    expect(page.locator("#rdBody")).not_to_contain_text("userStories")
    shoot(request, page, "prd-rendered")
    page.keyboard.press("Escape")

    # a job step's report opens from the workstream the job is linked to
    done.locator(":scope > summary").click()
    first = done.locator(".ws", has_text="foundations and slice 1")
    first.locator(":scope > summary").click()
    first.locator(".tr", has_text="Draft the article").click()
    expect(page.locator("#rdBody h1")).to_have_text("PAR by weekday: draft ready")
    expect(page.locator("#rdMeta")).to_contain_text("worker · d4f7a2")
    page.keyboard.press("Escape")
    page.context.close()
    assert errors == []


def test_a_repository_library_shows_jobs_and_its_folders(
        browser: Browser, overview_url: str, fixture_data: dict[str, Any], request: pytest.FixtureRequest) -> None:
    page, errors = open_library(browser, overview_url, fixture_data["time"])
    choose(page, "agent-fleet")
    expect(page.locator("#libList .ov-summary")).to_have_text(
        "1 workstream: 1 in progress. Active now: Split the deck into ES modules.")
    card = page.locator("#libList .ws")
    expect(card).to_have_count(1)
    card.locator(":scope > summary").click()
    expect(card.locator(".ws-body")).to_contain_text("Running now: Split the deck into ES modules · step 1")
    documents = page.locator("#libList .ov-docs")
    documents.locator(":scope > summary").click()
    expect(documents.locator("h5")).to_have_text(["top level", "docs/adr/", "docs/design/"])
    shoot(request, page, "overview-agent-fleet")
    expect(page.locator("#libList")).not_to_contain_text("Private working notes")   # CLAUDE.local.md stays private
    documents.locator(".tr", has_text="ADR 5: the storehouse").click()
    expect(page.locator("#rdBody h1")).to_have_text("ADR 5: the storehouse")
    page.keyboard.press("Escape")
    page.context.close()
    assert errors == []


def test_all_documents_is_a_folder_tree_with_a_filter(
        browser: Browser, overview_url: str, fixture_data: dict[str, Any], request: pytest.FixtureRequest) -> None:
    page, errors = open_library(browser, overview_url, fixture_data["time"])
    page.locator('[data-lib-view="all"]').click()
    restoke = page.locator("#libList .lib-group", has_text="Restoke")
    folders = restoke.locator(":scope > .tree > .tree-folder > summary span")
    expect(folders).to_have_text(["v2-review/", "v2-suppliers/", "v2-suppliers-slice4/", "v2-suppliers-slice5/",
                                  "v2-suppliers-slice6/"])
    expect(restoke.locator(":scope > .tree > .tree-doc")).to_have_count(1)     # the loose file at the top
    slice6 = restoke.locator('.tree-folder[data-folder="restoke:v2-suppliers-slice6/"]')
    expect(slice6).not_to_have_attribute("open", "")
    slice6.locator(":scope > summary").click()
    expect(slice6.locator(".tree-folder > summary span")).to_have_text(["notes/"])
    shoot(request, page, "all-documents-tree")
    page.locator("#libSearch").fill("local")
    expect(page.locator('#libList .tree-doc[data-id$=".local.md"]')).to_have_count(0)
    page.locator("#libSearch").fill("")

    page.locator("#libSearch").fill("legacy-map")
    expect(page.locator("#libList .tree-doc")).to_have_count(1)
    expect(page.locator('#libList .tree-folder[data-folder="restoke:v2-suppliers-slice6/notes/"]')).to_have_attribute("open", "")
    page.locator("#libList .tree-doc").click()
    expect(page.locator("#reader")).to_be_visible()
    page.keyboard.press("Escape")
    page.context.close()
    assert errors == []


def test_the_overview_fits_a_phone(browser: Browser, overview_url: str, fixture_data: dict[str, Any]) -> None:
    page, errors = open_library(browser, overview_url, fixture_data["time"], viewport="narrow")
    choose(page, "Restoke")
    page.locator("#libList .ov-done > summary").click()
    for card in page.locator("#libList .ws").all():
        card.locator(":scope > summary").first.click()
    # nothing runs off the side, every headline keeps to 12 words, and every briefing list to three visible lines
    assert page.evaluate("document.querySelector('#libList').scrollWidth <= document.querySelector('#libList').clientWidth")
    headlines = page.locator("#libList .ws-head b").all_text_contents()
    assert len(headlines) == 7 and all(len(text.split()) <= 12 for text in headlines), headlines
    assert page.evaluate("""[...document.querySelectorAll('#libList .ws-body > .ws-list')]
        .every(list => list.children.length <= 3)""")
    assert page.evaluate("fleetDeck.textBudget(document.querySelector('#libList .ov > .ws > summary'))") <= 20
    page.context.close()
    assert errors == []
