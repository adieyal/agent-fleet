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
    assert [r["action"] for r in state["robots"]] == ["Type", "Write", "Hold"]
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
    assert blue == {"desk": "desk2", "busy": False, "action": "Rest"}
    assert state["warmth"]["desk2"] == 0  # the lamp follows activity
    assert state["lantern"] is False


def view(page: Page) -> dict:
    return page.evaluate("window.bench.view()")


# The camera tests use a small viewport and few, large input events: each event waits for a frame, and
# without a GPU the browser renders the scene in software.
SMALL = {"width": 800, "height": 450}


@pytest.fixture
def small(browser: Browser, url: str) -> Iterator[Page]:
    page = browser.new_page(viewport=SMALL)
    page.goto(url + "/prototype/bench?still")
    page.wait_for_function("window.bench && window.bench.ready", timeout=120_000)
    yield page
    page.close()


def drag(page: Page, start: tuple[int, int], end: tuple[int, int], button: str = "left") -> None:
    page.mouse.move(*start)
    page.mouse.down(button=button)
    page.mouse.move(*end, steps=2)
    page.mouse.up(button=button)


def test_zoom_pan_and_turn_change_the_view_within_limits(small: Page) -> None:
    home = view(small)
    limits = home["limits"]
    small.mouse.move(400, 225)
    small.mouse.wheel(0, -200)
    assert view(small)["zoom"] > home["zoom"]
    small.mouse.wheel(0, -5000)  # far past the closest sharp zoom
    assert view(small)["zoom"] == pytest.approx(limits["max"])
    small.mouse.wheel(0, 5000)  # far past the whole-room zoom
    assert view(small)["zoom"] == pytest.approx(limits["min"])

    before = view(small)["target"]
    drag(small, (400, 240), (300, 200))
    assert view(small)["target"] != before
    for _ in range(3):  # drag far beyond the room: the target stays over it
        drag(small, (780, 440), (20, 20))
    x, height, z = view(small)["target"]
    assert -1 <= x <= 12.5 and 0 <= height <= 2.5 and -6 <= z <= 3.5

    drag(small, (400, 240), (20, 440), button="right")  # a big turn: clamped
    turned = view(small)
    assert turned["yaw"] == pytest.approx(limits["yaw"]) and turned["pitch"] == pytest.approx(limits["pitch"])


def test_a_drag_is_not_a_click_and_reset_restores_the_view(small: Page) -> None:
    tiles = small.evaluate("window.bench.state()")["tiles"]
    drag(small, (330, 80), (370, 110))  # starts on the plan wall
    assert small.evaluate("window.bench.state()")["tiles"] == tiles
    small.mouse.wheel(0, -400)
    assert view(small)["zoom"] != 1
    small.get_by_role("button", name="Reset view").click()
    home = view(small)
    assert home["zoom"] == pytest.approx(1) and home["yaw"] == 0 and home["pitch"] == 0


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_damping_eases_unless_motion_is_reduced(browser: Browser, url: str, motion: str) -> None:
    context = browser.new_context(viewport=SMALL, reduced_motion=motion)
    page = context.new_page()
    page.goto(url + "/prototype/bench")
    page.wait_for_function("window.bench && window.bench.ready", timeout=120_000)
    page.mouse.move(400, 225)
    page.mouse.wheel(0, -400)
    page.wait_for_timeout(30)  # a frame or two
    early = view(page)["zoom"]
    page.wait_for_timeout(1500)
    settled = view(page)["zoom"]
    context.close()
    assert settled > 1
    if motion == "reduce":
        assert early == pytest.approx(settled)
    else:
        assert early < settled
