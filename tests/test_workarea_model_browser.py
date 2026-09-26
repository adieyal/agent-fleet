"""The workarea model (fleet/web/js/workarea-model.js): a pure function over an /api/state document, run in the browser
against the module the deck serves and the Restoke fixture's state."""

import json
from collections.abc import Iterator
from typing import Any
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page

IMPORT = "import('/js/workarea-model.js')"


@pytest.fixture(scope="module")
def module_page(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page()
    page.goto(base_url + "/api/state")
    yield page
    page.close()


@pytest.fixture(scope="module")
def state(base_url: str) -> dict[str, Any]:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return json.load(response)


def workareas(page: Page, state: dict[str, Any], now: float) -> dict[str, dict[str, Any]]:
    rooms = page.evaluate(f"([state, now]) => {IMPORT}.then(m => m.workareasOf(state, now))", [state, now])
    return {room["room"]: room for room in rooms}


def test_benches_are_active_jobs_and_those_that_ran_in_the_last_hour(module_page, state, fixture_data):
    rooms = workareas(module_page, state, fixture_data["time"])
    restoke = rooms["restoke"]
    # oldest first; the failed Django job last ran over an hour ago, so its bench is cleared away
    assert [bench["key"] for bench in restoke["benches"]] == ["worker:d4f7a2", "home:a1c3e9", "home:b7d042", "worker:c90e11"]
    assert [(bench["key"], bench["active"]) for bench in restoke["benches"]][0] == ("worker:d4f7a2", False)
    assert [bench["key"] for bench in rooms["invoice-parser"]["benches"]] == ["worker:3c71d5", "worker:0a9e3b"]
    assert restoke["projectIds"] == ["p-5e1f0a01"]


def test_tiles_criteria_and_reports_follow_the_steps(module_page, state, fixture_data):
    benches = {bench["key"]: bench for room in workareas(module_page, state, fixture_data["time"]).values()
               for bench in room["benches"]}
    fix = benches["home:b7d042"]
    assert [(tile["status"], tile["mark"]) for tile in fix["tiles"]] == [("done", "check"), ("running", "glow"), ("pending", "blank")]
    assert fix["criteria"] == {"met": 1, "total": 3, "lights": [True, False, False]}
    assert [(tile["status"], tile["mark"]) for tile in benches["worker:3c71d5"]["tiles"]] == [("failed", "cross")]
    assert benches["worker:d4f7a2"]["reports"] == [{"id": "report-1", "name": "Step 2: Draft the article", "step": 1}]   # outbox files aren't reports
    assert fix["reports"] == []


def test_the_question_desk_holds_the_rooms_attention_with_one_lantern(module_page, state, fixture_data):
    desk = workareas(module_page, state, fixture_data["time"])["restoke"]["desk"]
    assert sorted(item["kind"] for item in desk["items"]) == ["blocker", "decision"]
    assert desk["lantern"] == {"count": 2, "level": "open", "kind": "blocker"}

    acknowledged = {**state, "attention": [{**item, "state": "acknowledged"} if item["project"] == "restoke" else item
                                           for item in state["attention"]]}
    assert workareas(module_page, acknowledged, fixture_data["time"])["restoke"]["desk"]["lantern"]["level"] == "acknowledged"
    snoozed = {**state, "attention": [{**item, "state": "snoozed"} for item in state["attention"]]}
    desk = workareas(module_page, snoozed, fixture_data["time"])["restoke"]["desk"]
    assert desk == {"items": [], "lantern": None}


def test_footprints_lead_to_benches_used_in_the_last_hour(module_page, state, fixture_data):
    now = fixture_data["time"]
    restoke = workareas(module_page, state, now)["restoke"]
    assert {step["to"] for step in restoke["footprints"]} == {bench["key"] for bench in restoke["benches"]}
    assert all(step["from"] == "entrance" and now - step["at"] <= 3600 for step in restoke["footprints"])

    later = workareas(module_page, state, now + 2 * 3600)["restoke"]
    assert later["footprints"] == []
    assert [bench["key"] for bench in later["benches"]] == ["home:a1c3e9", "home:b7d042", "worker:c90e11"]   # still running


def test_a_room_with_nothing_is_absent(module_page, state, fixture_data):
    assert module_page.evaluate(f"([state, now]) => {IMPORT}.then(m => m.workareaOf(state, 'nowhere', now))",
                                [state, fixture_data["time"]]) is None
