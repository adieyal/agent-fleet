"""The building view's rendered pieces (art/scripts/building_pieces.py, building_finish.py) keep the stacking contract:
every file listed is on disk at its size, anchors are whole pixels at every tier, the states of a thing swap in place,
and every piece and slot the view needs is in the manifest."""

import json
from pathlib import Path

from PIL import Image

FOLDER = Path(__file__).parent.parent / "fleet" / "web" / "assets" / "world" / "building"
MANIFEST = json.loads((FOLDER / "manifest.json").read_text())
PIECES = MANIFEST["pieces"]


def test_every_tier_is_on_disk_at_its_size_with_a_whole_pixel_anchor() -> None:
    listed = set()
    for name, piece in PIECES.items():
        for tier in piece["tiers"].values():
            listed.add(tier["file"])
            with Image.open(FOLDER / tier["file"]) as im:
                assert list(im.size) == tier["size"], tier["file"]
            assert all(isinstance(v, int) for v in tier["anchor_px"]), tier["file"]
    assert listed == {f.name for f in FOLDER.glob("*.webp")}


def test_states_swap_in_place() -> None:
    groups = [[n for n in PIECES if n.startswith("floor-") and "-top" not in n],
              [n for n in PIECES if n.startswith("floor-") and "-top" in n],
              [n for n in PIECES if n.startswith("annex-")],
              ["lift", "lift-open"], ["lift-lobby", "lift-open-lobby"]]
    for group in groups:
        assert len(group) > 1
        for tier in ("1", "2", "4"):
            shapes = {(tuple(PIECES[n]["tiers"][tier]["size"]), tuple(PIECES[n]["tiers"][tier]["anchor_px"])) for n in group}
            assert len(shapes) == 1, group


def test_the_view_needs_no_magic_numbers() -> None:
    for tier in MANIFEST["tiers"].values():
        assert isinstance(tier["step_px"], int) and isinstance(tier["lobby_step_px"], int)
    assert MANIFEST["backdrop"].startswith("#")
    floors = {f"floor-{k}{t}-{s}" for k in ("open", "lab", "glass") for t in ("", "-top") for s in ("lit", "unlit")}
    needed = floors | {"floor-free", "floor-free-top", "lobby", "roof", "lantern-bracket", "spine", "spine-lobby",
                       "spine-cap", "lift", "lift-lobby", "lift-cap", "lift-open", "lift-open-lobby"}
    needed |= {f"annex-{n}" for n in range(7)} | {f"plinth-{n}" for n in range(1, 11)}
    assert needed <= set(PIECES)
    slots = {name: set(PIECES[name]["tiers"]["1"]["slots"]) for name in ("spine", "floor-open-lit", "floor-free", "lobby", "annex-0",
                                                                      "lantern-bracket")}
    assert {"plate", "ring", "focus_switch", "lantern_bracket"} <= slots["spine"]
    assert {"shutter_handle", "focus_hit"} <= slots["floor-open-lit"] and "to_let_card" in slots["floor-free"]
    assert {"front_desk", "desk_lantern", "host_key", "kiosk"} <= slots["lobby"]
    assert {"storehouse_door", "door_lantern"} <= slots["annex-0"] and "lantern" in slots["lantern-bracket"]
