"""The floor kit (fleet/web/assets/world/kit/, built by art/kit/finish.py): the manifest is complete and matches its
files, every piece asked for is there, tiers agree on scale, the scale is recorded against l1 and l2, and the kit
scene at /prototype/kit draws without missing files."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page

REPO = Path(__file__).parent.parent
KIT = REPO / "fleet" / "web" / "assets" / "world" / "kit"
MANIFEST = json.loads((KIT / "manifest.json").read_text())
SPRITES: dict[str, dict[str, Any]] = MANIFEST["sprites"]
BUDGET = 6 * 1000 * 1000

REQUIRED = [
    "bench", "lamp", "glow-desk-pool", "glow-shade", "glow-wall-wash", "glow-floor-spill", "glow-lantern-halo",
    "laptop", "pen-pot", "paper-stack", "sketch", "mug", "desk-plant", "books", "paper-tray",
    "terminal-desk", "chair-back", "chair-front", "plant-tall", "plant-bush", "plant-small", "shelf",
    "book-cart", "librarian-desk", "podium", "whiteboard", "plan-wall", "tile-blank", "tile-done", "tile-running",
    "tile-failed", "criteria-on", "criteria-off", "question-desk", "crate", "lantern", "lift",
    "pilaster", "wall-cap-x", "wall-cap-y", "wall-corner", "wall-end-back", "wall-end-left", "slab-front", "slab-side",
    *[f"footprints-{a:03d}" for a in range(0, 360, 45)],
]


def test_every_piece_asked_for_is_in_the_kit() -> None:
    assert [name for name in REQUIRED if name not in SPRITES] == []
    assert {"floor-tile", "wall-tile"} <= set(MANIFEST["textures"])


def test_it_is_made_for_the_sprite_worlds_camera_and_records_its_scale() -> None:
    assert MANIFEST["camera"] == {"pitch": 44.5, "yaw": 21.25}
    scale = MANIFEST["scale"]
    assert scale["px_per_m_1x"] == pytest.approx(941 / 5.486, abs=0.01)
    assert {"l1", "l2"} <= set(scale["measured"])
    assert scale["measured"]["l2"]["px_per_m"] == pytest.approx(scale["px_per_m_1x"], rel=0.01)


@pytest.mark.parametrize("name", sorted(SPRITES))
def test_each_sprite_matches_its_files(name: str) -> None:
    s = SPRITES[name]
    assert s["source"] in ("ai", "blender", "procedural")
    assert len(s["footprint"]) == 6 and all(a <= b for a, b in zip(s["footprint"][:3], s["footprint"][3:]))
    ppms = [t["ppm"] for t in s["tiers"]]
    assert ppms == sorted(ppms) and len(ppms) >= 2
    for t in s["tiers"]:
        with Image.open(KIT / t["file"]) as im:
            assert (im.width // t.get("frames", 1), im.height) == tuple(t["size"])
            assert im.mode == "RGBA"
    # every tier covers the same world area: sizes and anchors scale with density (to a couple of pixels)
    first = s["tiers"][0]
    for t in s["tiers"][1:]:
        k = t["ppm"] / first["ppm"]
        assert t["size"][0] == pytest.approx(first["size"][0] * k, abs=2 + 0.02 * t["size"][0])
        assert t["anchor_px"][1] == pytest.approx(first["anchor_px"][1] * k, abs=2 + 0.02 * t["size"][1])


@pytest.mark.parametrize("name", sorted(n for n, s in SPRITES.items() if s["source"] == "ai"))
def test_ai_sprites_name_a_logged_generation(name: str) -> None:
    raw = re.match(r"art/kit/raw/([\w-]+)\.png", SPRITES[name]["from"])
    assert raw
    sidecar = json.loads((REPO / "art" / "kit" / "raw" / f"{raw.group(1)}.json").read_text())
    assert sidecar["references"]
    assert raw.group(1) in (REPO / "art" / "kit" / "generations.log").read_text()


def test_the_lift_has_door_states_and_the_lantern_a_glyph_slot() -> None:
    lift = SPRITES["lift"]
    assert all(t["frames"] == len(lift["cells"]) == 5 for t in lift["tiers"])
    assert "indicator" in lift["slots"]
    assert "glyph" in SPRITES["lantern"]["slots"]
    tiles = SPRITES["plan-wall"]["slots"]["tiles"]
    assert (tiles["cols"], tiles["rows"]) == (10, 6)  # l2's grid
    assert len(SPRITES["plan-wall"]["slots"]["lights"]) == 5
    assert all(SPRITES[g]["layer"] == "light" for g in SPRITES if g.startswith("glow-"))


def test_the_kit_is_within_budget() -> None:
    assert sum(f.stat().st_size for f in KIT.iterdir()) < BUDGET


@pytest.fixture(scope="module")
def kit(browser: Browser, base_url: str) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1000, "height": 600})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/prototype/kit?shot")
    page.wait_for_function("window.kit && (window.kit.ready || window.kit.error)", timeout=60_000)
    yield page
    page.close()
    assert errors == []


def test_the_kit_scene_draws_every_piece_it_places(kit: Page) -> None:
    assert kit.evaluate("window.kit.error") is None
    assert kit.evaluate("kit.engine.stats.missing") == []
    placed = kit.evaluate("[...new Set([...kit.engine.items.values()].map(it => it.sprite))]")
    assert set(REQUIRED) - {"chair-front", "footprints-000", "footprints-045", "footprints-090", "footprints-135",
                            "footprints-180", "footprints-225", "footprints-270"} <= set(placed)


def test_the_lift_doors_open_by_cell(kit: Page) -> None:
    frames = kit.evaluate("""(async () => { const e = kit.engine, out = [];
      for (const cell of [0, 2, 4]) { e.set('lift', { cell }); await new Promise(r => setTimeout(r, 100)); out.push(e.items.get('lift').frame); }
      return out; })()""")
    assert frames == [0, 2, 4]


def test_the_lantern_is_clickable_and_glow_is_not(kit: Page) -> None:
    hit = kit.evaluate("""import('/js/world/projection.js').then(p => {
      const e = kit.engine, [x, y] = p.toScreen(e.camera.view, e.items.get('lantern').at); return e.pick(x, y); })""")
    assert hit["id"] == "lantern"
