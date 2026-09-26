"""Browser tests: a pipeline's wall screen on the deck and the Sankey it opens, over fixtures/pipelines.json."""

import json
import re
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


Point = dict[str, float]


def _separated(p: list[Point], q: list[Point]) -> bool:
    """Two convex outlines on screen do not overlap: some edge's normal separates them."""
    for poly in (p, q):
        for a, b in zip(poly, poly[1:] + poly[:1]):
            nx, ny = b["y"] - a["y"], a["x"] - b["x"]
            pa, qa = [pt["x"] * nx + pt["y"] * ny for pt in p], [pt["x"] * nx + pt["y"] * ny for pt in q]
            if max(pa) < min(qa) or max(qa) < min(pa):
                return True
    return False


def _gap(p: list[Point], q: list[Point]) -> float:
    """Screen pixels between two convex outlines, 0 if they overlap."""
    if not _separated(p, q):
        return 0.0
    def to_segment(pt: Point, a: Point, b: Point) -> float:
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        t = max(0.0, min(1.0, ((pt["x"] - a["x"]) * dx + (pt["y"] - a["y"]) * dy) / (dx * dx + dy * dy or 1)))
        return ((pt["x"] - a["x"] - t * dx) ** 2 + (pt["y"] - a["y"] - t * dy) ** 2) ** 0.5
    return min(to_segment(pt, a, b) for one, other in ((p, q), (q, p))
               for pt in one for a, b in zip(other, other[1:] + other[:1]))


def test_screens_are_readable_glow_and_leave_the_room_sign_clear(deck: Deck) -> None:
    screens = {p["key"]: p for p in deck.page.evaluate("fleetDeck.pipelines()")}
    for p in screens.values():
        rect, sign = p["rect"], p["sign"]
        assert rect["left"] > sign["right"] or rect["right"] < sign["left"], p   # beside the sign on the wall, not over it
        # and clear of the whiteboard and wall art below it, with wall showing between
        gaps = {thing["kind"] + "@" + thing["wall"]: round(_gap(p["quad"], thing["quad"]), 1) for thing in p["wall"]}
        assert "whiteboard@back" in gaps
        assert min(gaps.values()) >= 8, (p["key"], gaps)
    # a live run's frame glows steadily under reduced motion; the others keep a low glow
    assert screens["home:sample-training"]["glow"] == pytest.approx(0.6)
    assert screens["home:embeddings"]["glow"] == pytest.approx(0.25)


def test_pipeline_labels_wait_in_the_lobby_as_visitors(deck: Deck) -> None:
    """No project claims these labels, so the building lists them as visitors, one row per label; a label with a job
    and a pipeline on the same host is still one row."""
    page = deck.page
    page.locator('#viewToggle [data-view="building"]').click()
    visitors = page.locator(".lobby .visitor")
    expect(visitors).to_have_count(3)
    assert {(row.get_attribute("data-label"), row.get_attribute("data-hosts")) for row in visitors.all()} == {
        ("nightly-eval", "worker"), ("restoke", "home"), ("sample-training", "home")}
    expect(visitors.locator("[data-move-in]")).to_have_count(3)
    page.locator('#viewToggle [data-view="deck"]').click()
    assert deck.errors == []


def test_the_screen_opens_the_run_as_a_sankey(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    page = deck.page
    open_screen(deck, "home:sample-training", hover_shot="hover")
    run = fixture_pipelines["pipeline_reports"][0]["run"]
    expect(page.locator("#skTitle")).to_have_text("sample-training")
    expect(page.locator("#skMeta")).to_contain_text("synthetic run")
    expect(page.locator("#skMeta")).to_contain_text("running")
    expect(page.locator("#skMeta")).to_contain_text(f"{run['counts']['items']:,} / 3,000 processed")
    rate = run["item_rate"]
    expect(page.locator("#skMeta")).to_contain_text(f"{rate:g} item{'' if rate == 1 else 's'}/s")
    nodes = [node for column in run["nodes"] for node in column]
    expect(page.locator("#skSvg .sk-node")).to_have_count(len(nodes))
    expect(page.locator("#skSvg .sk-band")).to_have_count(len(run["edges"]))
    expect(page.locator("#skSvg .sk-ghost")).to_have_count(len(run["edges"]))   # the baseline has every edge too
    for node in nodes:
        label = page.locator(f'#skSvg .sk-node[data-node="{node}"] text')
        expect(label).to_contain_text(f"{run['counts'][node]:,}")
    # a share is of the items that reached the node's column; a running run shows the baseline's share, no change
    counts, base = run["counts"], fixture_pipelines["pipeline_reports"][0]["baseline"]["counts"]
    confident = page.locator('#skSvg .sk-node[data-node="confident"] text')
    expect(confident).to_contain_text(f"{100 * counts['confident'] / (counts['confident'] + counts['review']):.1f}%")
    was = f"{100 * base['confident'] / (base['confident'] + base['review']):.1f}%"
    expect(confident).to_contain_text(f"prev {was}")
    expect(page.locator("#skSide thead")).to_contain_text("Prev")
    expect(page.locator('#skSide tr[data-node="confident"] td').last).to_have_text(was)
    assert not any(ch in page.locator("#skSvg").text_content() for ch in "+−±")
    # bands take the tone the run line gives their target; reasons after a warning are bad; the rest stay neutral
    tone = lambda key: page.locator(f'#skSvg .sk-band[data-band="{key}"]').get_attribute("class")
    assert [tone(key) for key in ("items→decided", "agree→confident", "alone→review", "review→null cell",
                                  "items→unlearnable")] == [
        "sk-band", "sk-band t-good", "sk-band t-warn", "sk-band t-bad", "sk-band t-muted"]
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
    # each item is counted under its first reason: the chart and the list say so
    expect(newest.locator("li.first")).to_have_text("null cell first · counted here")
    expect(page.locator("#skSide .sk-items li.first")).to_have_count(len(items))
    expect(page.locator("#skSide .sk-sub")).to_contain_text("counted once, under the first")
    expect(page.locator("#skSvg .sk-cap")).to_have_count(1)
    expect(page.locator("#skSvg .sk-cap")).to_contain_text("by first of its reasons")
    deck.shot("drilldown")
    page.locator("#skSide [data-back]").click()
    expect(page.locator("#skSide tbody tr")).to_have_count(6)
    close(deck)
    assert deck.errors == []


INSIDE_SVG = """[...document.querySelectorAll('#skSvg .sk-ghost, #skSvg .sk-band, #skSvg text')].filter(el => {
  const r = el.getBoundingClientRect(), s = document.getElementById('skSvg').getBoundingClientRect();
  return r.top < s.top - 0.5 || r.bottom > s.bottom + 0.5 || r.left < s.left - 0.5 || r.right > s.right + 0.5;
}).map(el => el.closest('[data-node]')?.dataset.node || el.getAttribute('class'))"""


def test_bands_outlines_and_labels_stay_inside_the_chart(deck: Deck) -> None:
    open_screen(deck, "home:sample-training")
    outside = deck.page.evaluate(INSIDE_SVG)
    close(deck)
    assert outside == []


LABELS_ON_BANDS = """(() => {
  const svg = document.getElementById('skSvg'), hits = [];
  const bands = [...svg.querySelectorAll('.sk-band')], stubs = [...svg.querySelectorAll('.sk-wait')];
  for (const g of svg.querySelectorAll('.sk-node')) {
    const r = g.querySelector('text').getBoundingClientRect(), chip = g.classList.contains('chip');
    for (const b of bands) {   // every 2 px of the label's box, tested against the band's own shape
      const inv = b.getScreenCTM().inverse();
      let hit = false;
      for (let x = r.left; x <= r.right && !hit; x += 2) for (let y = r.top; y <= r.bottom && !hit; y += 2)
        hit = b.isPointInFill(new DOMPoint(x, y).matrixTransform(inv));
      if (hit) hits.push([g.dataset.node, b.dataset.band, chip]);
    }
    for (const s of stubs) {
      const q = s.getBoundingClientRect();
      if (r.left < q.right && q.left < r.right && r.top < q.bottom && q.top < r.bottom) hits.push([g.dataset.node, 'stub', chip]);
    }
  }
  const boxes = [...svg.querySelectorAll('.sk-node text, .sk-cap')].map(t => t.getBoundingClientRect());
  const crowded = boxes.some((p, i) => boxes.some((q, j) => i < j && p.left < q.right && q.left < p.right && p.top < q.bottom && q.top < p.bottom));
  return { hits, crowded, chips: [...svg.querySelectorAll('.sk-node.chip')].map(g => g.dataset.node) };
})()"""


def test_no_label_lies_on_a_band(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    """Each label is in a free gap near its node. Only a middle node with no gap left puts its label on a chip level
    with the node, never over a waiting stub; no two labels overlap."""
    open_screen(deck, "home:sample-training")
    got = deck.page.evaluate(LABELS_ON_BANDS)
    close(deck)
    assert [hit for hit in got["hits"] if not hit[2] or hit[1] == "stub"] == []
    assert not got["crowded"]
    nodes = fixture_pipelines["pipeline_reports"][0]["run"]["nodes"]
    assert set(got["chips"]) <= {n for column in nodes[1:-1] for n in column}


def test_on_a_phone_the_chart_scrolls_to_every_column(deck: Deck) -> None:
    """A phone keeps the columns apart and scrolls sideways: a button says how many columns are out of view and takes
    the reader there, and at the end every label is in view."""
    page = deck.page
    open_screen(deck, "home:sample-training")
    more = page.locator("#skMore")
    if deck.name != "narrow":
        expect(more).to_be_hidden()
        close(deck)
        return
    expect(more).to_be_visible()
    expect(more).to_contain_text("more column")
    while more.is_visible():   # it goes once every column is in view
        more.click()
        page.wait_for_timeout(500)
    page.evaluate("(c => { c.scrollLeft = c.scrollWidth; })(document.getElementById('skChart'))")   # the rest of the labels
    page.wait_for_timeout(100)
    deck.shot("sankey-end")
    in_view = page.evaluate("""(() => { const c = document.getElementById('skChart').getBoundingClientRect();
      return [...document.querySelectorAll('#skSvg .sk-node')].filter(g => g.getBoundingClientRect().left >= c.left)
        .map(g => [g.dataset.node, g.getBoundingClientRect().right <= c.right + 0.5]); })()""")
    close(deck)
    assert {node for node, _ in in_view} >= {"null cell", "sum mismatch", "profile disagree", "low support"}
    assert all(inside for _, inside in in_view), in_view


def test_items_not_yet_gone_on_are_waiting(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    """Mid-run, what a middle column holds beyond what has left it is waiting: a stub and 'N waiting' per node, adding
    up to the difference between that column and the next."""
    page = deck.page
    open_screen(deck, "home:sample-training")
    run = fixture_pipelines["pipeline_reports"][0]["run"]
    counts, (middle, after) = run["counts"], run["nodes"][2:4]
    gap = sum(counts[n] for n in middle) - sum(counts[n] for n in after)
    assert gap > 0
    shown = 0
    for node in middle:
        text = page.locator(f'#skSvg .sk-node[data-node="{node}"] text tspan').last.text_content()
        assert re.fullmatch(r"[\d,]+ waiting", text), text
        shown += int(text.split()[0].replace(",", ""))
    assert shown == gap
    expect(page.locator("#skSvg .sk-wait")).to_have_count(len(middle))
    close(deck)
    assert deck.errors == []


def test_once_a_run_is_done_what_a_node_holds_ended_there(deck: Deck) -> None:
    got = deck.page.evaluate("""import('/js/sankey.js').then(({ terminals, waiting }) => {
      const run = status => ({ status, nodes: [['in'], ['a', 'b'], ['x', 'y']], counts: { in: 10, a: 6, b: 4, x: 5, y: 0 },
        edges: [['in', 'a', 6], ['in', 'b', 4], ['a', 'x', 5]] });
      return ['running', 'failed', 'done'].map(s => { const r = run(s), ends = terminals(r, null);
        return [[...ends].sort(), Object.fromEntries(waiting(r, ends))]; });
    })""")
    # an empty end node (y) is never an end; b has nothing leaving it but its neighbour passes items on
    assert got == [[["b", "x"], {"a": 1}], [["b", "x"], {"a": 1}], [["a", "b", "x"], {}]]


def test_a_finished_run_shows_its_change_on_the_baseline(deck: Deck) -> None:
    got = deck.page.evaluate("""import('/js/sankey.js').then(({ versus }) => {
      const base = { counts: { confident: 100, review: 60 } }, nodes = [['confident', 'review']];
      return [versus({ status: 'running', nodes }, base, 'confident', 90), versus({ status: 'done', nodes }, base, 'confident', 90),
              versus({ status: 'done', nodes }, base, 'confident', 112), versus({ status: 'done', nodes }, null, 'confident', 1)]
        .map(v => v && v.text);
    })""")
    assert got == ["prev 62.5%", "−10", "+12", None]


DOT_PIXELS ="""(() => { const c = document.getElementById('skDots'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
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
    glow = "fleetDeck.pipelines().find(p => p.key === 'node-a:demo-training').glow"
    glows = [page.evaluate(glow), page.wait_for_timeout(400), page.evaluate(glow)][::2]
    assert (glows[0] != glows[1]) == (motion == "no-preference")   # a live run's frame pulses, unless motion is reduced
    open_screen(deck, "node-a:demo-training")
    expect(page.locator("#skMeta")).to_contain_text("demo run 1 (synthetic)")
    first = int(page.locator('#skSvg .sk-node[data-node="items"] text .ct').text_content().replace(",", ""))
    page.wait_for_function(f"""Number(document.querySelector('#skSvg .sk-node[data-node="items"] text .ct')
        .textContent.replace(/,/g, '')) > {first}""", timeout=8000)
    page.wait_for_timeout(250)   # mid-way through an update: bands easing, dots on their way
    deck.shot("sankey-live")
    # the table lists exactly the chart's end nodes, and an end node holds items: empty ones are in neither
    ends = page.evaluate("""[[...document.querySelectorAll('#skSvg .sk-node.end')].map(g => g.dataset.node),
      [...document.querySelectorAll('#skSide tbody tr')].map(r => [r.dataset.node, r.cells[1].textContent])]""")
    assert sorted(ends[0]) == sorted(node for node, _ in ends[1])
    assert all(count != "0" for _, count in ends[1]), ends
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
