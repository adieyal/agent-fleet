"""Zooming the world floor stays sharp and still (fleet/web/js/world/): every tier of a kit piece or robot frame lands
where the others do, so a swap moves nothing by more than half a screen pixel; and a wheel zoom draws no sprite from
a bitmap stretched past its pixels, and swaps tiers without a jump. See tests/world_zoom.py."""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

from world_zoom import AGREEMENT, LONG_TASKS, LONG_TASKS_READ, PROBE, blur, jumps, long_task_summary, rest, zoom, zoom_bursts

# The longest main-thread task a zoom may cause. Before the ground and sprite copies were painted in a worker, a zoom
# out stalled for 0.4-2.7 s at a time (here 458 ms; scripts/world_longtask_bench.py); after, the longest here was
# 111-138 ms (the frame the zoom settles in, which sizes a software canvas back to full pixel ratio). The bound leaves
# room for a loaded machine rather than holding the frame budget itself.
LONGEST_TASK_MS = 200

# Tier pairs whose content differs at the edges though it is centred alike (centroids within 0.5 px): soft shadows,
# glows and occlusion whose faint tails were cut or smoothed differently per size, and small props rendered sharper
# or softer per size. Placement data can't fix this; the pieces need their tiers made again from one render.
EDGES_DIFFER = {"podium-shadow", "plant-tall-shadow", "glow-floor-spill", "terminal-desk", "plant-bush-shadow",
                "crate-stack-shadow", "crate-shelf-shadow", "lamp", "ao-wall-y", "wall-light-shadow", "ao-wall-x",
                "whiteboard-shadow", "laptop", "mug", "slab-side", "paper-stack", "chair-back-shadow", "pen-pot"}
# ...and two soft shadows whose mass sits differently per tier (0.79 and 0.59 px)
CENTRE_DIFFERS = {"crate-shelf-shadow", "whiteboard-shadow"}
# Tiers loaded as ImageBitmaps (decoded once, never in a frame) are downscaled a little differently from <img>s: the
# faint edge of these moves up to 0.82 px between tiers (0.5 px or less from an <img>); their centres don't move
BITMAP_EDGES = {"shelf-shadow", "bench-right", "bench-mid", "bench-left", "librarian-desk", "crate-shadow", "ao-floor-x",
                "floor-sheen", "monitor"}


@pytest.fixture
def world(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?view=world")
    page.wait_for_function("window.fleetWorld && (fleetWorld.ready || fleetWorld.error)", timeout=60_000)
    assert page.evaluate("fleetWorld.error") is None
    rest(page)
    yield page
    page.close()
    assert errors == []


def test_every_kit_tier_lands_where_the_others_do(world: Page) -> None:
    ids = world.evaluate("[...fleetWorld.engine.sprites.values()].filter(s => !s.compose && s.tiers.length > 1).map(s => s.id)")
    pairs = world.evaluate(AGREEMENT, {"ids": ids, "frames": 0})
    assert len(pairs) > 150
    assert {p["id"] for p in pairs if p["centroid"] > 0.5} <= CENTRE_DIFFERS
    assert {p["id"] for p in pairs if p["px"] > 0.5} <= EDGES_DIFFER | BITMAP_EDGES
    assert [p for p in pairs if p["id"] in BITMAP_EDGES - EDGES_DIFFER and p["px"] > 1.0] == []


def test_every_robot_tier_lands_where_the_others_do(world: Page) -> None:
    ids = world.evaluate("""(() => { const R = fleetWorld.robots, look = { host: '#6b8cff', kit: 'crest', agent: 'codex' };
      return ['Walking', 'Idle', 'Typing'].filter(c => R.man.clips[c]).flatMap(c =>
        Object.keys(R.man.clips[c].dirs).flatMap(d => ['all', 'high'].map(part => R.sprite(look, c, d, part)))); })()""")
    pairs = world.evaluate(AGREEMENT, {"ids": ids, "frames": 2})
    assert len(pairs) >= 16
    assert [p for p in pairs if p["px"] > 0.5] == []


def test_a_wheel_zoom_stays_sharp_and_no_swap_moves_anything(world: Page) -> None:
    world.evaluate(PROBE)
    zoom(world, 1)
    rest(world)
    frames = world.evaluate("fleetWorld.engine.zoomProbe.frames")
    moving = [f for f in frames if f["moving"]]
    assert len(moving) > 10
    # at most the robots' on-demand 4x set's 5% (tiers.js LAZY)
    assert blur(moving)["sprite_up"] <= 1.05
    swaps = sum(len({d["tier"] for d in f["draws"] if d["id"] == i}) > 1 for f in frames for i in {d["id"] for d in f["draws"]})
    assert swaps > 0, "the zoom crossed no tier boundary"
    assert jumps(frames)["swap"]["px"] <= 0.5


def test_zooming_out_and_back_in_never_stalls_the_page(world: Page) -> None:
    zoom_bursts(world, 1, bursts=3, ticks=14)   # (from close in, where zooming out repainted the whole ground at once)
    rest(world)
    world.evaluate(LONG_TASKS)
    zoom_bursts(world, -1)
    zoom_bursts(world, 1)
    rest(world)
    world.wait_for_timeout(500)
    record = world.evaluate(LONG_TASKS_READ)
    summary = long_task_summary(record)
    assert summary["frames"] > 100, summary
    assert summary["longest_task_ms"] <= LONGEST_TASK_MS, summary


TIER_MEMORY = """(() => { const w = fleetWorld.engine; let total = 0, busy = 0;
  for (const s of w.sprites.values()) {
    if (s.compose) continue;
    const inUse = s.used ? [s.want, s.shown, s.prev, s.want + 1] : [];
    s.img.forEach((img, i) => { if (!img) return; const b = img.width * img.height * 4 + (s.alpha[i] ? s.alpha[i].width * s.alpha[i].height * 4 : 0);
      total += b; if (inUse.includes(i)) busy += b; });
  }
  return { total, busy, released: w.stats.released || 0, missing: w.stats.missing }; })()"""


def test_tier_bitmaps_past_the_budget_are_released_and_loaded_again(world: Page) -> None:
    budget = 64 * 2 ** 20
    world.evaluate(f"fleetWorld.engine.tierBytes = {budget}")
    zoom_bursts(world, 1, bursts=4, ticks=10)
    rest(world)
    zoom_bursts(world, -1, bursts=4, ticks=10)
    rest(world)
    out = world.evaluate(TIER_MEMORY)
    assert out["released"] > 0
    # (tiers in use are kept whatever the total)
    assert out["total"] <= max(budget, out["busy"])
    # back in, the released close tiers load again (rest waits for every wanted tier)
    zoom_bursts(world, 1, bursts=4, ticks=10)
    rest(world)
    assert world.evaluate(TIER_MEMORY)["missing"] == []
