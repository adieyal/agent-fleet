"""Browser tests: a pipeline's wall screen on the deck and the Sankey it opens, over fixtures/pipelines.json."""

import json
import re
import runpy
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from conftest import serve_fixture
from browser_clock import advance_until

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
        page.evaluate("fleetDeck.advanceTime(0)")
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
        deck.page.evaluate("fleetDeck.advanceTime(0)")
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
    expect(page.locator("#skSide tbody tr")).to_have_count(7)
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
    assert page.evaluate(DRILL_FITS % json.dumps("null cell")) == {"label": True, "wide": []}
    page.locator("#skSide [data-back]").click()
    expect(page.locator("#skSide tbody tr")).to_have_count(7)
    # chosen from the table, a node off to the side of a phone's chart is brought into view with its whole label
    long = next(n for n in run["nodes"][-1] if len(n) >= 45)
    page.evaluate("(c => { c.scrollLeft = 0; })(document.getElementById('skChart'))")
    page.locator(f'#skSide tr[data-node="{long}"]').click()
    expect(page.locator("#skSide h3")).to_contain_text(long)
    deck.shot("drilldown-long")
    assert page.evaluate(DRILL_FITS % json.dumps(long)) == {"label": True, "wide": []}
    close(deck)
    assert deck.errors == []


DRILL_FITS = """(name => {   // the chosen node's column's labels in the chart's view; nothing in the side list wider than it
  const c = document.getElementById('skChart').getBoundingClientRect(), side = document.getElementById('skSide');
  const nodes = [...document.querySelectorAll('#skSvg .sk-node')], g = nodes.find(g => g.dataset.node === name);
  const x = g.querySelector('rect').getBoundingClientRect().left, s = side.getBoundingClientRect();
  const column = nodes.filter(n => Math.abs(n.querySelector('rect').getBoundingClientRect().left - x) < 1)
    .map(n => n.querySelector('text').getBoundingClientRect());
  return { label: column.every(t => t.left >= c.left && t.right <= c.right),
    wide: [...side.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > s.right + 0.5).map(el => el.textContent.slice(0, 40)) };
})(%s)"""


INSIDE_SVG = """[...document.querySelectorAll('#skSvg .sk-ghost, #skSvg .sk-band, #skSvg text')].filter(el => {
  const r = el.getBoundingClientRect(), s = document.getElementById('skSvg').getBoundingClientRect();
  return r.top < s.top - 0.5 || r.bottom > s.bottom + 0.5 || r.left < s.left - 0.5 || r.right > s.right + 0.5;
}).map(el => el.closest('[data-node]')?.dataset.node || el.getAttribute('class'))"""


def test_bands_outlines_and_labels_stay_inside_the_chart(deck: Deck) -> None:
    open_screen(deck, "home:sample-training")
    outside = deck.page.evaluate(INSIDE_SVG)
    close(deck)
    assert outside == []


LONG_LABEL = """(name => {
  const chart = document.getElementById('skChart');
  chart.scrollLeft = chart.scrollWidth;   // a phone scrolls to the last column
  const g = [...document.querySelectorAll('#skSvg .sk-node')].find(g => g.dataset.node === name);
  const r = g.querySelector('text').getBoundingClientRect(), c = chart.getBoundingClientRect();
  return { inside: r.left >= c.left && r.right <= c.right && r.top >= c.top && r.bottom <= c.bottom,
    shown: [...g.querySelectorAll('text .nm')].map(t => t.textContent).join(' '), tip: g.querySelector('title')?.textContent ?? null };
})"""


def test_a_long_end_label_is_whole_and_inside_the_chart(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    """The last column's margin is as wide as its longest name needs, so a long reason shows in full, in view."""
    name = next(n for n in fixture_pipelines["pipeline_reports"][0]["run"]["nodes"][-1] if len(n) >= 45)
    open_screen(deck, "home:sample-training")
    got = deck.page.evaluate(f"{LONG_LABEL}({json.dumps(name)})")
    deck.shot("long-label")
    close(deck)
    assert got == {"inside": True, "shown": name, "tip": None}, got


def test_an_end_name_wider_than_its_margin_wraps_then_ends_in_an_ellipsis(deck: Deck) -> None:
    got = deck.page.evaluate("""import('/js/sankey.js').then(({ nameLines }) => [
      nameLines('null cell', 200), nameLines('line totals do not add up to the printed total', 200),
      nameLines('line totals do not add up to the printed total on any of the pages of this invoice', 200),
      nameLines('line_totals_do_not_add_up_to_the_printed_total', 200)])""")
    assert got[0] == ["null cell"]
    assert " ".join(got[1]) == "line totals do not add up to the printed total" and len(got[1]) == 2
    assert len(got[2]) == 2 and got[2][0].startswith("line totals") and got[2][1].endswith("…")
    assert len(got[3]) == 1 and got[3][0].endswith("…")


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
  const els = [...svg.querySelectorAll('.sk-node text, .sk-cap')], boxes = els.map(t => t.getBoundingClientRect());
  const name = el => el.closest('[data-node]')?.dataset.node || 'caption';
  const crowded = boxes.flatMap((p, i) => boxes.map((q, j) => i < j && p.left < q.right && q.left < p.right && p.top < q.bottom
    && q.top < p.bottom ? [name(els[i]), name(els[j])] : null)).filter(Boolean);
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


SHEET_FIT = """(() => {
  const r = el => el.getBoundingClientRect(), sheet = r(document.querySelector('#sankey .rd-sheet'));
  const chart = document.getElementById('skChart'), cap = r(document.querySelector('#skSvg .sk-cap'));
  cap.left - r(chart).left > chart.clientWidth - 40 && (chart.scrollLeft = cap.left - r(chart).left - 20);   // a phone scrolls to it
  const c = r(document.querySelector('#skSvg .sk-cap')), box = r(chart), side = document.getElementById('skSide');
  return { sheet: [sheet.top, sheet.bottom, innerHeight], page: document.scrollingElement.scrollHeight <= innerHeight,
    caption: c.top >= box.top && c.bottom <= box.bottom && c.bottom <= innerHeight && c.left >= box.left && c.right <= box.right,
    inside: getComputedStyle(side).overflowY === 'auto' || getComputedStyle(document.querySelector('.sk-body')).overflowY === 'auto' };
})()"""


def test_the_sheet_fits_the_viewport_and_its_caption_shows(deck: Deck) -> None:
    """The sheet ends inside the viewport and scrolls within itself; the reasons caption under the chart is in view."""
    open_screen(deck, "home:sample-training")
    got = deck.page.evaluate(SHEET_FIT)
    deck.shot("sheet-bottom")
    close(deck)
    top, bottom, height = got["sheet"]
    assert 0 <= top and bottom <= height - (0 if deck.name == "narrow" else 12), got   # a desktop sheet shows its edge
    assert got["page"] and got["inside"] and got["caption"], got


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
        page.evaluate("new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
    page.evaluate("(c => { c.scrollLeft = c.scrollWidth; })(document.getElementById('skChart'))")   # the rest of the labels
    page.evaluate("new Promise(done => requestAnimationFrame(done))")
    deck.shot("sankey-end")
    in_view = page.evaluate("""(() => { const c = document.getElementById('skChart').getBoundingClientRect();
      return [...document.querySelectorAll('#skSvg .sk-node')].filter(g => g.getBoundingClientRect().left >= c.left)
        .map(g => [g.dataset.node, g.getBoundingClientRect().right <= c.right + 0.5]); })()""")
    close(deck)
    assert {node for node, _ in in_view} >= {"null cell", "sum mismatch", "profile disagree", "low support"}
    assert all(inside for _, inside in in_view), in_view


def test_items_not_yet_gone_on_are_waiting(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    """Mid-run, what a middle column's nodes that are not declared ends hold beyond what has left them is waiting: a
    stub and 'N waiting' per node."""
    page = deck.page
    open_screen(deck, "home:sample-training")
    run = fixture_pipelines["pipeline_reports"][0]["run"]
    middle = [n for n in run["nodes"][2] if n not in run["ends"]]
    gap = sum(run["counts"][n] for n in middle) - sum(c for s, _, c in run["edges"] if s in middle)
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


SHOW_REPORT = """(async report => {   // what fleetd would stream next for the open pipeline
  const { pipelineByKey, applyPipeline } = await import('/js/pipelines.js');
  const p = pipelineByKey('home:sample-training');
  applyPipeline({ ...p, ...report, seq: (p.seq || 0) + 1 });
})"""
SIDE_AND_WAITS = """[[...document.querySelectorAll('#skSide tbody tr')].map(r => r.dataset.node),
  [...document.querySelectorAll('#skSvg .sk-node')].filter(g => / waiting$/.test(g.querySelector('text').textContent))
    .map(g => g.dataset.node)]"""


LABEL_NEAR = """(() => {   // node with no band leaving it → pixels between its label and it (with its stub)
  const svg = document.getElementById('skSvg'), out = {};
  const leaving = new Set([...svg.querySelectorAll('.sk-band')].map(b => b.dataset.band.split('→')[0]));
  for (const g of svg.querySelectorAll('.sk-node')) {
    if (leaving.has(g.dataset.node)) continue;
    const t = g.querySelector('text').getBoundingClientRect(), r = g.querySelector('rect:not(.sk-chip)').getBoundingClientRect();
    const stub = [...svg.querySelectorAll('.sk-wait')].map(s => s.getBoundingClientRect())
      .find(s => Math.abs(s.left - r.right) < 1 && s.top >= r.top - 1 && s.bottom <= r.bottom + 1);
    const right = stub ? stub.right : r.right;
    out[g.dataset.node] = Math.max(0, t.left - right, r.left - t.right, t.top - r.bottom, r.top - t.bottom);
  }
  return out;
})()"""


BAND_COVER = """(names => {   // node → [band area under its label, under a label level with it left of the node], in px²
  const svg = document.getElementById('skSvg'), bands = [...svg.querySelectorAll('.sk-band')], out = {};
  const area = r => bands.reduce((sum, b) => {   // each band's own shape, sampled every px: overlapping bands add up
    const inv = b.getScreenCTM().inverse();
    let n = 0;
    for (let x = r.left + 0.5; x < r.right; x++) for (let y = r.top + 0.5; y < r.bottom; y++)
      n += b.isPointInFill(new DOMPoint(x, y).matrixTransform(inv));
    return sum + n;
  }, 0);
  for (const g of svg.querySelectorAll('.sk-node')) {
    if (!names.includes(g.dataset.node)) continue;
    const t = g.querySelector('text').getBoundingClientRect(), r = g.querySelector('rect:not(.sk-chip)').getBoundingClientRect();
    const top = r.top + r.height / 2 - t.height / 2, left = r.left - 4 - t.width;
    out[g.dataset.node] = [area(t), area({ left, right: left + t.width, top, bottom: top + t.height })];
  }
  return out;
})(%s)"""


RIGHT_OF_STUB = """[...document.querySelectorAll('#skSvg .sk-node')].filter(g => {   // nodes whose label starts past their stub
  const r = g.querySelector('rect:not(.sk-chip)').getBoundingClientRect(), t = g.querySelector('text').getBoundingClientRect();
  const stub = [...document.querySelectorAll('#skSvg .sk-wait')].map(s => s.getBoundingClientRect())
    .find(s => Math.abs(s.left - r.right) < 1 && s.top >= r.top - 1 && s.bottom <= r.bottom + 1);
  return stub && t.left >= stub.right;
}).map(g => g.dataset.node)"""


def test_mid_run_nodes_waiting_for_a_gate_burst_are_not_ends(deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    """A run with no finished run before it, part-way, whose gates run as a burst at the end: nothing has left agree,
    disagree or profile yet, but alone and unsettled beside them pass items on. The run line's `ends` says they are
    waiting; without it the deck can only guess, and takes them for ends as it always has."""
    generator = runpy.run_path(str(FIXTURE.parent / "make_pipelines.py"))
    page = deck.page
    open_screen(deck, "home:sample-training")
    try:
        got = {}
        for declared in (None, generator["ENDS"]):
            report = generator["gate_burst"](declared)
            burst, run = set(generator["BURST"]), report["run"]
            assert all(source not in burst for source, _, _ in run["edges"]) and report["baseline"] is None
            page.evaluate(f"{SHOW_REPORT}({json.dumps(report)})")
            expect(page.locator("#skMeta")).to_contain_text("gates at the end")
            got[bool(declared)] = page.evaluate(SIDE_AND_WAITS)
        deck.shot("gate-burst-mid-run")
        # a node nothing leaves keeps its label beside it, off every band, as any label
        page.evaluate("(c => { c.scrollLeft = 0; })(document.getElementById('skChart'))")
        near, placed = page.evaluate(LABEL_NEAR), page.evaluate(LABELS_ON_BANDS)
        assert [hit for hit in placed["hits"] if not hit[2] or hit[1] == "stub"] == [] and not placed["crowded"], placed
        assert burst <= set(near) and all(gap <= 8 for gap in near.values()), near
        if deck.name == "desktop":   # no free spot beside them: they cover less band than the chip left of the node did
            cover = page.evaluate(BAND_COVER % json.dumps(sorted(burst)))
            assert all(now < old for now, old in cover.values()), cover
            assert set(page.evaluate(RIGHT_OF_STUB)) >= burst, cover
        ends, waits = got[True]
        last = run["nodes"][-1]
        assert set(ends) == {n for n in [*generator["ENDS"], *last] if run["counts"].get(n)}
        assert burst <= set(waits) and not burst & set(ends)
        assert burst <= set(got[False][0])   # undeclared: today's guess
        page.evaluate(f"{SHOW_REPORT}({json.dumps(generator['gate_burst'](generator['ENDS'], 'done'))})")
        expect(page.locator("#skMeta")).to_contain_text("done")
        ends, waits = page.evaluate(SIDE_AND_WAITS)
        deck.shot("gate-burst-done")
        assert waits == [] and not burst & set(ends)   # the burst passed every item on
    finally:
        page.evaluate(f"{SHOW_REPORT}({json.dumps(fixture_pipelines['pipeline_reports'][0])})")
        close(deck)
    assert deck.errors == []


def test_a_waiting_nodes_label_goes_right_of_its_stub_where_the_gap_is_empty(
        deck: Deck, fixture_pipelines: dict[str, Any]) -> None:
    """Before a gate burst that takes a whole column, nothing crosses the gap after it: each of that column's labels
    sits right of its node's waiting stub, on no band at all (not even the incoming ones), overlapping no label."""
    generator = runpy.run_path(str(FIXTURE.parent / "make_pipelines.py"))
    page = deck.page
    report = generator["gate_burst"](generator["ENDS"], burst=tuple(generator["NODES"][2]))
    column = set(report["run"]["nodes"][2])
    assert not any(source in column for source, _, _ in report["run"]["edges"])
    open_screen(deck, "home:sample-training")
    try:
        page.evaluate(f"{SHOW_REPORT}({json.dumps(report)})")
        expect(page.locator("#skMeta")).to_contain_text("gates at the end")
        page.evaluate("(c => { c.scrollLeft = 0; })(document.getElementById('skChart'))")
        deck.shot("gate-burst-whole-column")
        placed, right, near = page.evaluate(LABELS_ON_BANDS), set(page.evaluate(RIGHT_OF_STUB)), page.evaluate(LABEL_NEAR)
    finally:
        page.evaluate(f"{SHOW_REPORT}({json.dumps(fixture_pipelines['pipeline_reports'][0])})")
        close(deck)
    assert placed["crowded"] == [] and all(near[n] <= 8 for n in column), (placed, near)
    if deck.name == "desktop":   # a phone's narrow gap may have no room: then the labels fall back as elsewhere
        assert [hit for hit in placed["hits"] if hit[0] in column] == [], placed
        assert right >= column - set(generator["ENDS"]), right   # a declared end has no stub: nothing waits in it
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


def test_declared_ends_settle_which_nodes_end_items_until_the_run_is_done(deck: Deck) -> None:
    got = deck.page.evaluate("""import('/js/sankey.js').then(({ terminals, waiting }) => {
      const run = (status, ends) => ({ status, ends, nodes: [['in'], ['a', 'b', 'c'], ['x', 'y']],
        counts: { in: 10, a: 5, b: 3, c: 2, x: 5, y: 0 }, edges: [['in', 'a', 5], ['in', 'b', 3], ['in', 'c', 2], ['a', 'x', 5]] });
      return ['running', 'failed', 'done'].map(s => { const r = run(s, ['c']), ends = terminals(r, null);
        return [[...ends].sort(), Object.fromEntries(waiting(r, ends))]; });
    })""")
    # b has nothing leaving it and its neighbour a passes items on, but only c is declared: b's items wait
    assert got == [[["c", "x"], {"b": 3}], [["c", "x"], {"b": 3}], [["b", "c", "x"], {}]]


def test_a_finished_run_shows_its_change_on_the_baseline(deck: Deck) -> None:
    got = deck.page.evaluate("""import('/js/sankey.js').then(({ versus }) => {
      const base = { counts: { confident: 100, review: 60 } }, nodes = [['confident', 'review']];
      return [versus({ status: 'running', nodes }, base, 'confident', 90), versus({ status: 'done', nodes }, base, 'confident', 90),
              versus({ status: 'done', nodes }, base, 'confident', 112), versus({ status: 'done', nodes }, null, 'confident', 1)]
        .map(v => v && v.text);
    })""")
    assert got == ["prev 62.5%", "−10", "+12", None]


ALL_IN_VIEW = """(() => {
  const c = document.getElementById('skChart').getBoundingClientRect(), out = [], onNodes = [];
  const nodes = [...document.querySelectorAll('#skSvg .sk-node')];
  const rects = nodes.map(g => [g.dataset.node, g.querySelector('rect:not(.sk-chip)').getBoundingClientRect()]);
  for (const g of nodes) {
    const t = g.querySelector('text').getBoundingClientRect();
    for (const [what, r] of [['node', rects.find(([n]) => n === g.dataset.node)[1]], ['label', t]])
      if (r.top < c.top - 0.5 || r.bottom > c.bottom + 0.5 || r.left < c.left - 0.5 || r.right > c.right + 0.5) out.push([g.dataset.node, what]);
    for (const [n, r] of rects)
      if (t.left < r.right && r.left < t.right && t.top < r.bottom && r.top < t.bottom) onNodes.push([g.dataset.node, n]);
  }
  return { out, onNodes };
})()"""


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
    glows = [page.evaluate(glow), page.evaluate("fleetDeck.advanceTime(0.4)"), page.evaluate(glow)][::2]
    assert (glows[0] != glows[1]) == (motion == "no-preference")   # a live run's frame pulses, unless motion is reduced
    open_screen(deck, "node-a:demo-training")
    expect(page.locator("#skMeta")).to_contain_text("demo run 1 (synthetic)")
    first = int(page.locator('#skSvg .sk-node[data-node="items"] text .ct').first.text_content().replace(",", ""))
    assert "waiting" not in page.locator('#skSvg .sk-node[data-node="items"] text').text_content()
    advance_until(page, f"""Number(document.querySelector('#skSvg .sk-node[data-node="items"] text .ct')
        .textContent.replace(/,/g, '')) > {first}""")
    page.evaluate("fleetDeck.advanceTime(0.25)")   # bands easing, dots on their way
    page.evaluate("new Promise(done => requestAnimationFrame(done))")
    deck.shot("sankey-live")
    # every node and label is in the chart's view, off every node and band, and no two labels overlap: now, mid-way
    # through an update, and again after the next
    count = lambda: int(page.locator('#skSvg .sk-node[data-node="items"] text .ct').first.text_content().replace(",", ""))
    for moment in range(2):
        if moment:
            advance_until(page, f"""Number(document.querySelector('#skSvg .sk-node[data-node="items"] text .ct')
                .textContent.replace(/,/g, '')) > {count()}""")
        fit, placed = page.evaluate(ALL_IN_VIEW), page.evaluate(LABELS_ON_BANDS)
        assert fit == {"out": [], "onNodes": []} and placed["crowded"] == [], (moment, fit, placed)
        assert [hit for hit in placed["hits"] if not hit[2] or hit[1] == "stub"] == [], (moment, placed)
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
