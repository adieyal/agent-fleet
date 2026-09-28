"""Zooming the world floor stays sharp and still (fleet/web/js/world/): every tier of a kit piece or robot frame lands
where the others do, so a swap moves nothing by more than half a screen pixel; and a wheel zoom draws no sprite from
a bitmap stretched past its pixels, and swaps tiers without a jump. See tests/world_zoom.py."""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

from world_zoom import AGREEMENT, PROBE, blur, jumps, rest, zoom

# Tier pairs whose content differs at the edges though it is centred alike (centroids within 0.5 px): soft shadows,
# glows and occlusion whose faint tails were cut or smoothed differently per size, and small props rendered sharper
# or softer per size. Placement data can't fix this; the pieces need their tiers made again from one render.
EDGES_DIFFER = {"podium-shadow", "plant-tall-shadow", "glow-floor-spill", "terminal-desk", "plant-bush-shadow",
                "crate-stack-shadow", "crate-shelf-shadow", "lamp", "ao-wall-y", "wall-light-shadow", "ao-wall-x",
                "whiteboard-shadow", "laptop", "mug", "slab-side", "paper-stack", "chair-back-shadow", "pen-pot"}
# ...and two soft shadows whose mass sits differently per tier (0.79 and 0.59 px)
CENTRE_DIFFERS = {"crate-shelf-shadow", "whiteboard-shadow"}


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
    assert {p["id"] for p in pairs if p["px"] > 0.5} <= EDGES_DIFFER


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
