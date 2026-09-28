"""Browser tests: a running job's documents list and an open reader follow the agent as it writes."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from conftest import FIXTURE, serve_fixture
from fleet.web.fixture import FixtureState
from test_deck_browser import PIN_CLOCK, VIEWPORTS, finish_jobs


# A fixture server of this module's own: other tests move rooms to the background, shutter them and finish jobs
# on the shared one.
@pytest.fixture(scope="module")
def own_state() -> FixtureState:
    return FixtureState.load(FIXTURE)


@pytest.fixture(scope="module")
def own_url(own_state: FixtureState) -> Iterator[str]:
    with serve_fixture(own_state) as url:
        yield url


@pytest.fixture
def page(browser: Browser, own_url: str, fixture_data: dict[str, Any]) -> Iterator[Page]:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    context.add_init_script(PIN_CLOCK % (fixture_data["time"], fixture_data["time"]))
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(own_url + "/")
    page.wait_for_function("window.fleetDeck && (fleetDeck.advanceTime(0), fleetDeck.agents().some(agent => agent.key === 'home:a1c3e9'))")
    yield page
    context.close()
    assert errors == []


def shoot(request: pytest.FixtureRequest, page: Page, name: str) -> None:
    if request.config.getoption("--shots"):
        page.screenshot(path=str(Path(request.config.getoption("--shots")) / f"{name}.png"))


def with_documents(own_url: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """The server's state document with the running job a1c3e9 holding these documents."""
    doc = finish_jobs(own_url, {})
    job = next(job for host in doc["hosts"] if host["name"] == "home" for job in host["jobs"] if job["id"] == "a1c3e9")
    job["documents"] = documents
    return doc


def brief(fixture_data: dict[str, Any]) -> dict[str, Any]:
    return {"id": "brief-0", "kind": "brief", "name": "Step 1 brief", "step": 0, "size": 120,
            "path": "/home/adi/.fleet/jobs/a1c3e9/brief-0.md", "mtime": fixture_data["time"] - 2400}


def notes(fixture_data: dict[str, Any], seconds_ago: int, size: int) -> dict[str, Any]:
    return {"id": "file-0", "kind": "file", "name": "migration-notes.md", "step": 0, "size": size,
            "path": "/home/adi/Development/restoke/migration-notes.md", "mtime": fixture_data["time"] - seconds_ago}


def test_the_documents_list_shows_new_documents_while_the_step_runs(
        page: Page, own_url: str, fixture_data: dict[str, Any], request: pytest.FixtureRequest) -> None:
    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [brief(fixture_data)]))
    page.locator("#tags .tag", has_text="a1c3e9").dispatch_event("click")
    documents = page.locator("#panelBody ul.docs li")
    expect(documents).to_have_count(1)
    expect(documents.first).to_contain_text("Step 1 brief")
    expect(documents.first).to_contain_text("brief · step 1")
    expect(page.locator("#panelBody .upd")).to_have_count(0)            # what the agent was given is not "updating"

    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [brief(fixture_data), notes(fixture_data, 5, 900)]))
    expect(documents).to_have_count(2)
    expect(page.locator("#panelBody h3", has_text="Documents")).to_have_text("Documents · 2")
    expect(documents.first).to_contain_text("migration-notes.md")         # what it produced comes first
    expect(documents.first).to_have_class("updating")
    expect(documents.first.locator(".upd")).to_have_text("updating")
    expect(documents.nth(1)).not_to_have_class("updating")
    shoot(request, page, "live-document-panel")

    page.evaluate("advanceClock(90)")                                    # a minute and a half without a change
    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [brief(fixture_data), notes(fixture_data, 5, 900)]))
    expect(page.locator("#panelBody .upd")).to_have_count(0)
    expect(documents).to_have_count(2)


def long_document(version: int, paragraphs: int) -> dict[str, Any]:
    body = "".join(f"<p>Paragraph {index} of the migration notes, draft {version}.</p>" for index in range(paragraphs))
    return {"id": "file-0", "kind": "file", "name": "migration-notes.md", "step": 0, "mtime": None,
            "html": f"<h1>Migration notes</h1>{body}<p id=\"tail\">End of draft {version}</p>",
            "markdown": "…", "toc": [], "words": 10 * paragraphs, "minutes": 1}


def test_an_open_reader_refreshes_in_place_when_the_document_changes(
        page: Page, own_url: str, fixture_data: dict[str, Any], request: pytest.FixtureRequest) -> None:
    served = {"version": 1, "paragraphs": 80}

    def serve(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(long_document(served["version"], served["paragraphs"])))
    page.route("**/api/doc?*", serve)

    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [notes(fixture_data, 5, 900)]))
    page.locator("#tags .tag", has_text="a1c3e9").dispatch_event("click")
    page.locator('#panelBody [data-doc="file-0"]').click()
    body = page.locator("#rdBody")
    expect(body.locator("#doc-tail")).to_have_text("End of draft 1")
    expect(page.locator("#rdMeta")).to_contain_text("updating live")

    # read from the middle; watch for anything that would show as a flicker: the grid rebuilt, or a jump in position
    position = page.evaluate("""async () => {
        const body = document.getElementById('rdBody');
        body.scrollTop = 900;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        window.readerGrid = body.querySelector('.rd-grid');
        window.readerPositions = [];
        body.addEventListener('scroll', () => window.readerPositions.push(body.scrollTop));
        return body.scrollTop;
    }""")
    assert position > 800
    served["version"] = 2
    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [notes(fixture_data, 1, 950)]))
    expect(body.locator("#doc-tail")).to_have_text("End of draft 2")
    shoot(request, page, "live-document-reader")
    assert page.evaluate("document.getElementById('rdBody').scrollTop") == position
    assert page.evaluate("document.querySelector('#rdBody .rd-grid') === window.readerGrid")
    assert page.evaluate("window.readerPositions") in ([], [position])

    # a reader at the bottom follows the new text down
    page.evaluate("const body = document.getElementById('rdBody'); body.scrollTop = body.scrollHeight")
    served.update(version=3, paragraphs=120)
    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [notes(fixture_data, 0, 1400)]))
    expect(body.locator("#doc-tail")).to_have_text("End of draft 3")
    assert page.evaluate("""() => {
        const body = document.getElementById('rdBody');
        return body.scrollHeight - body.clientHeight - body.scrollTop <= 1;
    }""")

    # a state update that leaves the document alone fetches nothing
    requests: list[str] = []
    page.on("request", lambda item: "/api/doc" in item.url and requests.append(item.url))
    page.evaluate("doc => fleetDeck.apply(doc)", with_documents(own_url, [notes(fixture_data, 0, 1400)]))
    page.wait_for_timeout(300)
    assert requests == []
    page.keyboard.press("Escape")


def test_a_departed_jobs_report_opens_from_the_library(
        page: Page, own_state: Any, fixture_data: dict[str, Any], request: pytest.FixtureRequest) -> None:
    """The job left its host (fleet rm) long ago; its copy in the project's store is all that remains."""
    store = own_state.documents
    project_id = own_state.registry.project_for("home", "restoke").id
    report = {"id": "report-0", "kind": "report", "name": "Step 1: Audit the stock counts", "step": 0,
              "path": "/home/adi/.fleet/jobs/0ld5ob/result-0.md", "size": 120, "mtime": fixture_data["time"] - 86400}
    job = {"id": "0ld5ob", "project": "restoke", "description": "Audit last month's stock counts", "agent": "claude",
           "status": "done", "created_at": fixture_data["time"] - 90000, "updated_at": fixture_data["time"] - 86400,
           "steps": [{"index": 0, "title": "Audit the stock counts", "status": "done"}], "documents": [report]}
    for document in store.observe(project_id, "worker", job):
        store.keep(project_id, "worker", job["id"], document, "# Stock count audit\n\nThree counts disagree.\n\nFLEET_STATUS: done")

    page.locator("#libraryOpen").click()
    departed = page.locator('#libList .lib-job[data-job="worker-0ld5ob"]')
    expect(departed).to_contain_text("Audit last month's stock counts")
    expect(departed).to_contain_text("worker · done")
    expect(departed).to_contain_text("left its host")
    # a job still on the floor is listed from the store too
    expect(page.locator('#libList .lib-job[data-job="home-e1b5c8"] .lib-doc')).to_have_count(1)
    shoot(request, page, "library-job-documents")

    page.locator("#libSearch").fill("stock counts")
    expect(page.locator("#libList .lib-job")).to_have_count(1)
    shoot(request, page, "library-job-documents-filtered")
    departed.locator('.lib-doc[data-id="report-0"]').click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdTitle")).to_have_text("Step 1: Audit the stock counts")
    expect(page.locator("#rdBody h1")).to_have_text("Stock count audit")
    expect(page.locator("#rdBody")).not_to_contain_text("FLEET_STATUS")
    expect(page.locator("#rdMeta")).to_contain_text("worker · 0ld5ob · claude")
    shoot(request, page, "library-departed-report")
    page.keyboard.press("Escape")
