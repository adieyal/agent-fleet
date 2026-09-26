"""Browser tests: a pipeline's wall screen on the deck and the Sankey it opens, over fixtures/pipelines.json."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from conftest import serve_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "pipelines.json"
VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "narrow": {"width": 390, "height": 844}}
PIN_CLOCK = """
const offset = %d * 1000 - Date.now();
const realNow = Date.now.bind(Date);
Date.now = () => realNow() + offset;
"""


@dataclass
class Deck:
    page: Page
    name: str
    shots: str | None = None   # pytest --shots DIR
    errors: list[str] = field(default_factory=list)

    def shot(self, name: str) -> None:
        if self.shots:
            self.page.screenshot(path=str(Path(self.shots) / f"{name}-{self.name}.png"))


@pytest.fixture(scope="module")
def fixture_pipelines() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def pipeline_url() -> Iterator[str]:
    with serve_fixture(FIXTURE) as url:
        yield url


@pytest.fixture(scope="module", params=list(VIEWPORTS))
def deck(request: pytest.FixtureRequest, browser: Browser, pipeline_url: str,
         fixture_pipelines: dict[str, Any]) -> Iterator[Deck]:
    context = browser.new_context(viewport=VIEWPORTS[request.param], reduced_motion="reduce")
    context.add_init_script(PIN_CLOCK % fixture_pipelines["time"])
    page = context.new_page()
    deck = Deck(page, request.param, request.config.getoption("--shots"))
    page.on("console", lambda message: message.type == "error" and deck.errors.append(message.text))
    page.on("pageerror", lambda error: deck.errors.append(str(error)))
    page.goto(pipeline_url + "/")
    page.wait_for_function("window.fleetDeck && fleetDeck.pipelines().filter(p => p.screen).length === 3")
    yield deck
    context.close()


def open_screen(deck: Deck, key: str, hover_shot: str | None = None) -> None:
    page = deck.page
    screen = next(p for p in page.evaluate("fleetDeck.pipelines()") if p["key"] == key)["screen"]
    if not (0 < screen["x"] < page.viewport_size["width"] and 60 < screen["y"] < page.viewport_size["height"] - 60):
        page.evaluate(f"fleetDeck.lookAt({json.dumps(key)})")
        screen = next(p for p in page.evaluate("fleetDeck.pipelines()") if p["key"] == key)["screen"]
    page.mouse.move(screen["x"], screen["y"])
    expect(page.locator("#docTip")).to_contain_text("click to open")
    if hover_shot:
        page.wait_for_timeout(100)   # a frame to light the hovered screen's frame
        deck.shot(hover_shot)
    page.mouse.click(screen["x"], screen["y"])
    expect(page.locator("#sankey")).to_be_visible()


def close(deck: Deck) -> None:
    deck.page.keyboard.press("Escape")
    expect(deck.page.locator("#sankey")).to_be_hidden()


def test_declared_pipelines_get_rooms_even_without_work(deck: Deck) -> None:
    rooms = {room["name"] for room in deck.page.evaluate("fleetDeck.rooms()")}
    assert rooms == {"restoke", "sample-training", "nightly-eval"}
    screens = {p["key"]: p["room"] for p in deck.page.evaluate("fleetDeck.pipelines()")}
    assert screens == {"home:sample-training": "sample-training", "home:embeddings": "restoke",
                       "worker:nightly-eval": "nightly-eval"}
    deck.shot("room")
    if deck.shots:
        deck.page.evaluate("fleetDeck.lookAt('home:sample-training', 90)")
        deck.page.wait_for_timeout(300)
        deck.shot("screen")
        deck.page.keyboard.press("f")   # fit the deck again
    assert deck.errors == []


def test_the_screen_opens_the_run_as_a_sankey(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    page = deck.page
    open_screen(deck, "home:sample-training", hover_shot="hover")
    run = fixture_pipelines["pipeline_reports"][0]["run"]
    expect(page.locator("#skTitle")).to_have_text("sample-training")
    expect(page.locator("#skMeta")).to_contain_text("synthetic run")
    expect(page.locator("#skMeta")).to_contain_text("running")
    expect(page.locator("#skMeta")).to_contain_text("3,000 total")
    nodes = [node for column in run["nodes"] for node in column]
    expect(page.locator("#skSvg .sk-node")).to_have_count(len(nodes))
    expect(page.locator("#skSvg .sk-band")).to_have_count(len(run["edges"]))
    expect(page.locator("#skSvg .sk-ghost")).to_have_count(len(run["edges"]))   # the baseline has every edge too
    for node in nodes:
        label = page.locator(f'#skSvg .sk-node[data-node="{node}"] text')
        expect(label).to_contain_text(f"{run['counts'][node]:,}")
    expect(page.locator('#skSvg .sk-node[data-node="confident"] text')).to_contain_text(
        f"{100 * run['counts']['confident'] / run['total']:.1f}%")
    deck.shot("sankey")
    close(deck)
    assert deck.errors == []


def test_an_end_node_lists_its_latest_items(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    page = deck.page
    open_screen(deck, "home:sample-training")
    run = fixture_pipelines["pipeline_reports"][0]["run"]
    expect(page.locator("#skSide tbody tr")).to_have_count(6)
    page.locator('#skSvg .sk-node[data-node="null cell"] rect').click()
    items = run["recent"]["null cell"]
    expect(page.locator("#skSide h3")).to_contain_text("null cell")
    expect(page.locator("#skSide .sk-items > li")).to_have_count(len(items))
    newest = page.locator("#skSide .sk-items > li").first
    expect(newest).to_contain_text(items[-1]["item"])
    for reason in items[-1]["attrs"]["reasons"]:
        expect(newest).to_contain_text(reason)
    deck.shot("drilldown")
    page.locator("#skSide [data-back]").click()
    expect(page.locator("#skSide tbody tr")).to_have_count(6)
    close(deck)
    assert deck.errors == []


DOT_PIXELS = """(() => { const c = document.getElementById('skDots'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; })()"""


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_the_demo_pipeline_moves_while_the_sankey_is_open(browser: Browser, pipeline_url: str,
                                                          request: pytest.FixtureRequest, motion: str) -> None:
    """Bands take each demo tick's new counts; with motion allowed they ease and dots travel, reduced they don't."""
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion=motion)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(pipeline_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.pipelines().some(p => p.screen)")
    deck = Deck(page, f"demo-{motion}", request.config.getoption("--shots"))
    open_screen(deck, "node-a:demo-training")
    expect(page.locator("#skMeta")).to_contain_text("demo run 1 (synthetic)")
    first = int(page.locator('#skSvg .sk-node[data-node="items"] text .ct').text_content().replace(",", ""))
    page.wait_for_function(f"""Number(document.querySelector('#skSvg .sk-node[data-node="items"] text .ct')
        .textContent.replace(/,/g, '')) > {first}""", timeout=8000)
    page.wait_for_timeout(250)   # mid-way through an update: bands easing, dots on their way
    deck.shot("sankey-live")
    if motion == "reduce":
        assert page.evaluate(DOT_PIXELS) == 0
    else:
        assert page.evaluate(DOT_PIXELS) > 0
    close(deck)
    context.close()
    assert errors == []


def test_empty_and_offline_pipelines_say_so(deck: Deck) -> None:
    page = deck.page
    open_screen(deck, "home:embeddings")
    expect(page.locator("#skChart .sk-empty")).to_contain_text("No runs of embeddings on home yet")
    expect(page.locator("#skSvg .sk-node")).to_have_count(0)
    close(deck)
    open_screen(deck, "worker:nightly-eval")
    expect(page.locator("#skChart .sk-empty")).to_contain_text("worker is offline")
    expect(page.locator("#skMeta")).to_contain_text("worker offline")
    close(deck)
    assert deck.errors == []
