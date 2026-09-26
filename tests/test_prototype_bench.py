"""The workbench art prototype at /prototype/bench: served by the deck's server but not linked from the deck,
renders the baked scene with its live pieces, and its tiles, criteria lights, lamps, robots and lantern respond."""

import io
from collections.abc import Iterator
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from PIL import Image, ImageStat
from playwright.sync_api import Browser, Page

from conftest import FIXTURE, serve_fixture


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with serve_fixture(FIXTURE) as u:
        yield u


@pytest.fixture
def bench(browser: Browser, url: str) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1672, "height": 941})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.goto(url + "/prototype/bench?still")
    page.wait_for_function("window.bench && window.bench.ready", timeout=120_000)
    yield page
    page.close()
    assert errors == []


def test_the_page_is_served_but_not_linked_from_the_deck(url: str) -> None:
    with urlopen(url + "/prototype/bench") as response:
        assert response.headers["Content-Type"].startswith("text/html")
    with urlopen(url + "/") as response:
        assert b"/prototype/" not in response.read()
    with pytest.raises(HTTPError):
        urlopen(url + "/prototype/../server.py")


def test_it_opens_in_the_l2_state(bench: Page) -> None:
    state = bench.evaluate("window.bench.state()")
    assert [r["action"] for r in state["robots"]] == ["type", "write", "hold"]
    assert all(r["busy"] for r in state["robots"])
    assert state["lantern"] is True
    assert [state["warmth"][f"criteria{i}"] for i in range(5)] == [1, 1, 1, 0, 0]
    assert all(state["warmth"][d] == 1 for d in ("desk1", "desk2", "desk3"))
    assert list(state["tiles"].values()).count("active") == 6
    shot = Image.open(io.BytesIO(bench.screenshot())).convert("RGB")
    assert max(ImageStat.Stat(shot).stddev) > 20  # a rendered scene, not a blank canvas


def test_live_pieces_respond(bench: Page) -> None:
    bench.evaluate("window.bench.flipTile(0, 0)")
    bench.evaluate("window.bench.setCriteria(4, true)")
    bench.evaluate("window.bench.setBusy('desk2', false)")
    bench.evaluate("window.bench.answerAttention()")
    state = bench.evaluate("window.bench.state()")
    assert state["tiles"]["0,0"] == "done"
    assert state["warmth"]["criteria4"] == 1
    blue = next(r for r in state["robots"] if r["desk"] == "desk2")
    assert blue == {"desk": "desk2", "busy": False, "action": "idle"}
    assert state["warmth"]["desk2"] == 0  # the lamp follows activity
    assert state["lantern"] is False
