"""The sprite engine's pure parts (fleet/web/js/world/): depth sorting by footprint, hit testing and level-of-detail
selection, run in the browser against the modules the deck serves."""

import math
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page


@pytest.fixture(scope="module")
def page(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page()
    page.goto(base_url + "/api/state")
    yield page
    page.close()


def run(page: Page, module: str, body: str, arg: Any = None) -> Any:
    """Evaluate `body` (a function of the module `m` and `arg`) against /js/world/<module>.js."""
    return page.evaluate(f"([arg]) => import('/js/world/{module}.js').then(m => ({body})(m, arg))", [arg])


# --- depth sorting ---------------------------------------------------------------------------------------------------

SORT = """(m, entries) => {
  return import('/js/world/projection.js').then(p => m.sortEntries(entries.map(e => ({ ...e, rect: p.boxRect(e.box) }))).map(e => e.id));
}"""

BENCH = [3.8, 3.9, 0, 9.2, 4.8, 0.95]  # a 5.4 m bench


def order(page: Page, entries: list[dict[str, Any]]) -> list[str]:
    return run(page, "sort", SORT, entries)


def test_a_box_further_back_left_or_lower_is_drawn_first(page: Page) -> None:
    cases = run(page, "sort", """m => [
      m.behind([0, 5, 0, 1, 6, 1], [0, 0, 0, 1, 1, 1]),      // further back
      m.behind([0, 0, 0, 1, 1, 1], [0, 5, 0, 1, 6, 1]),
      m.behind([0, 0, 0, 1, 1, 1], [2, 0, 0, 3, 1, 1]),      // further left
      m.behind([2, 0, 0, 3, 1, 1], [0, 0, 0, 1, 1, 1]),
      m.behind([0, 0, 0, 1, 1, 1], [0, 0, 1, 1, 1, 2]),      // below
      m.behind([0, 0, 1, 1, 1, 2], [0, 0, 0, 1, 1, 1]),
    ]""")
    assert cases == [True, False, True, False, True, False]


def test_a_long_bench_sorts_against_what_stands_at_either_end(page: Page) -> None:
    # a single depth per sprite puts both plants on the same side of the bench; footprints put the one behind its far
    # edge first and the one in front of its near edge last
    entries = [
        {"id": "bench", "box": BENCH},
        {"id": "plant-behind", "box": [8.8, 4.9, 0, 9.4, 5.5, 1.4]},
        {"id": "plant-front", "box": [3.5, 3.2, 0, 4.1, 3.8, 1.4]},
    ]
    assert order(page, entries) == ["plant-behind", "bench", "plant-front"]
    assert order(page, entries[::-1]) == ["plant-behind", "bench", "plant-front"]


def test_boxes_that_do_not_overlap_on_screen_still_come_out_far_to_near(page: Page) -> None:
    entries = [{"id": f"p{i}", "box": [i * 3, 10 - i * 3, 0, i * 3 + 0.5, 10.5 - i * 3, 1]} for i in range(4)]
    assert order(page, entries[::-1]) == ["p0", "p1", "p2", "p3"]


def test_seated_layers_go_under_and_over_their_desk(page: Page) -> None:
    seat = [4.5, 4.7, 0, 5.1, 5.3, 1.4]
    entries = [
        {"id": "robot:over", "box": seat, "attach": {"to": "bench", "order": 1}},
        {"id": "front", "box": seat, "attach": {"to": "bench", "order": 2}},
        {"id": "bench", "box": BENCH},
        {"id": "robot:under", "box": seat, "attach": {"to": "bench", "order": -1}},
        {"id": "plant-behind", "box": [8.8, 4.9, 0, 9.4, 5.5, 1.4]},
    ]
    assert order(page, entries) == ["plant-behind", "robot:under", "bench", "robot:over", "front"]


def test_intersecting_boxes_fall_back_to_depth_without_losing_any(page: Page) -> None:
    entries = [{"id": "a", "box": [0, 0, 0, 2, 2, 1]}, {"id": "b", "box": [1, 1, 0, 3, 3, 1]}, {"id": "c", "box": [0.5, 0.5, 0, 2.5, 2.5, 1]}]
    out = order(page, entries)
    assert sorted(out) == ["a", "b", "c"]
    assert out == ["b", "c", "a"]  # b's centre is furthest back, a's nearest


# --- hit testing -----------------------------------------------------------------------------------------------------

PICK = """(m, points) => {
  // two 10×10 sprites overlapping by half: the back one solid, the front one solid only in its left half
  const solid = { w: 2, h: 1, bits: new Uint8Array([1, 1]) }, left = { w: 2, h: 1, bits: new Uint8Array([1, 0]) };
  const entries = [
    { item: 'back', rect: { x: 0, y: 0, w: 10, h: 10 }, mask: solid, hit: 'alpha' },
    { item: 'front', rect: { x: 5, y: 0, w: 10, h: 10 }, mask: left, hit: 'alpha' },
    { item: 'box', rect: { x: 30, y: 0, w: 10, h: 10 }, hit: 'box' },
    { item: 'cut', rect: { x: 50, y: 0, w: 10, h: 10 }, mask: solid, hit: 'alpha', keep: (x, y) => y < 5 },
  ];
  return points.map(([x, y]) => m.pickAt(entries, x, y));
}"""


def test_a_click_hits_the_front_most_solid_pixel(page: Page) -> None:
    hits = run(page, "hit", PICK, [[7, 5], [3, 5], [12, 5], [16, 5], [35, 5], [55, 2], [55, 8], [25, 5]])
    assert hits == ["front", "back", None, None, "box", "cut", None, None]


def test_masks_come_from_alpha(page: Page) -> None:
    out = run(page, "hit", """m => {
      const px = new Uint8ClampedArray([0,0,0,255, 0,0,0,10, 0,0,0,0, 0,0,0,200]);
      const mask = m.maskFromRGBA(px, 2, 2);
      return [[...mask.bits], m.solidAt(mask, 0.1, 0.1), m.solidAt(mask, 0.9, 0.1), m.solidAt(mask, 0.9, 0.9),
              m.solidAt(mask, 1.2, 0.5), m.maskSize(1395, 789), m.maskSize(60, 40)];
    }""")
    assert out == [[1, 0, 0, 1], True, False, True, False, [128, 72], [60, 40]]


def test_a_tint_stays_inside_its_sprite(page: Page) -> None:
    # a sprite opaque on the left, transparent on the right, under a mask that is (wrongly) white everywhere: the host
    # colour must not show where the sprite isn't (floor review 2: faint rectangles round the seated robots)
    out = run(page, "paint", """m => {
      const img = m.canvas(2, 1), g = img.getContext('2d');
      g.fillStyle = 'rgb(128,128,128)'; g.fillRect(0, 0, 1, 1);
      const mask = m.canvas(2, 1), mg = mask.getContext('2d');
      mg.fillStyle = '#000'; mg.fillRect(0, 0, 2, 1);
      const t = m.tinted(img, mask, '#20a0c0'), d = t.getContext('2d').getImageData(0, 0, 2, 1).data;
      return [d[3], d[7]];
    }""")
    assert out == [255, 0]


# --- level of detail -------------------------------------------------------------------------------------------------

TIERS = [{"ppm": 85.75}, {"ppm": 171.5}, {"ppm": 343}]


@pytest.mark.parametrize(("need", "current", "want"), [
    (40, -1, 0),     # the smallest tier at least as dense as the screen
    (86, -1, 1),
    (171.5, -1, 1),
    (200, -1, 2),
    (900, -1, 2),    # past the finest: the finest
    (200, 1, 2),     # going finer is immediate
    (160, 2, 2),     # going coarser waits for a margin below the coarser tier's density...
    (140, 2, 1),     # ...and then goes
    (80, 2, 0),
])
def test_pick_tier(page: Page, need: float, current: int, want: int) -> None:
    assert run(page, "tiers", "(m, a) => m.pickTier(a.tiers, a.need, a.current)", {"tiers": TIERS, "need": need, "current": current}) == want


def test_a_zoom_resting_on_a_tier_boundary_does_not_flap(page: Page) -> None:
    # wobbling about the tier-1 density: finer as soon as it is needed (never stretched), then it stays
    picked = run(page, "tiers", """(m, a) => { let cur = -1; return a.needs.map(n => (cur = m.pickTier(a.tiers, n, cur))); }""",
                 {"tiers": TIERS, "needs": [170, 173, 170, 173, 168, 172]})
    assert picked == [1, 2, 2, 2, 2, 2]


@pytest.mark.parametrize(("need", "want"), [
    (360, 1),   # l2 at pixel ratio 2: 5% past the eager tier, which serves (the robots' 4x set decodes to ~940 MB)
    (361, 2),   # any further and the on-demand tier: nothing is drawn stretched more than that
    (430, 2),
])
def test_an_on_demand_tier_is_wanted_only_well_past_the_tier_below(page: Page, need: float, want: int) -> None:
    tiers = [{"ppm": 171.5}, {"ppm": 343}, {"ppm": 686, "lazy": True}]
    assert run(page, "tiers", "(m, a) => m.pickTier(a.tiers, a.need)", {"tiers": tiers, "need": need}) == want


@pytest.mark.parametrize(("want", "loaded", "draw"), [
    (1, [1], 1),
    (1, [0, 2], 2),   # a finer tier stands in (sharp, just more to scale)...
    (1, [0], 0),      # ...else a coarser one
    (2, [], -1),
])
def test_while_loading_the_nearest_loaded_tier_is_drawn(page: Page, want: int, loaded: list[int], draw: int) -> None:
    got = run(page, "tiers", "(m, a) => m.drawableTier(a.tiers, a.want, new Set(a.loaded))", {"tiers": TIERS, "want": want, "loaded": loaded})
    assert got == draw


def test_crossfade_runs_about_120_ms(page: Page) -> None:
    assert run(page, "tiers", "m => [m.fadeAlpha(10, 10), m.fadeAlpha(10, 10.06), m.fadeAlpha(10, 10.12), m.fadeAlpha(-Infinity, 0)]") == pytest.approx([0, 0.5, 1, 1])


@pytest.mark.parametrize(("want", "ahead"), [
    (0, 1),    # at rest the next finer tier loads ahead of a zoom in
    (1, -1),   # but not an on-demand one: only a zoom heading there loads that
    (2, -1),
])
def test_the_next_finer_tier_loads_ahead_unless_on_demand(page: Page, want: int, ahead: int) -> None:
    tiers = [{"ppm": 171.5}, {"ppm": 343}, {"ppm": 686, "lazy": True}]
    assert run(page, "tiers", "(m, a) => m.nextTier(a.tiers, a.want)", {"tiers": tiers, "want": want}) == ahead


# --- projection ------------------------------------------------------------------------------------------------------

def test_projection_round_trips_and_matches_the_l2_camera(page: Page) -> None:
    out = run(page, "projection", """m => {
      const p = [3.2, -1.7, 0], [u, v] = m.plane(p), back = m.unplane(u, v, 0);
      const x = m.plane([1, 0, 0]), y = m.plane([0, 1, 0]), z = m.plane([0, 0, 1]);
      return { back, slope: x[1] / x[0], y, z, nearer: m.depth([0, -1, 0]) > m.depth([0, 0, 0]) };
    }""")
    assert out["back"] == pytest.approx([3.2, -1.7, 0])
    # pitch 28, yaw 33: the image model's camera (floor review 1)
    assert out["slope"] == pytest.approx(math.sin(math.radians(28)) * math.tan(math.radians(33)), abs=1e-4)
    assert out["y"] == pytest.approx([math.sin(math.radians(33)), -math.sin(math.radians(28)) * math.cos(math.radians(33))], abs=1e-4)
    assert out["z"] == pytest.approx([0, -math.cos(math.radians(28))], abs=1e-4)
    assert out["nearer"]
