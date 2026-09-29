"""The building view's rendered pieces (art/scripts/building_pieces.py, building_finish.py) keep the stacking contract:
every file listed is on disk, anchors are whole pixels at every tier, and the floor states are interchangeable."""

import json
from pathlib import Path

from PIL import Image

FOLDER = Path(__file__).parent.parent / "fleet" / "web" / "assets" / "world" / "building"
MANIFEST = json.loads((FOLDER / "manifest.json").read_text())
FLOORS = ["floor-open-lit", "floor-open-unlit", "floor-glass-lit", "floor-glass-unlit"]


def test_every_tier_is_on_disk_at_its_size_with_a_whole_pixel_anchor() -> None:
    listed = set()
    for name, piece in MANIFEST["pieces"].items():
        for tier in piece["tiers"]:
            listed.add(tier["file"])
            with Image.open(FOLDER / tier["file"]) as im:
                assert list(im.size) == tier["size"], tier["file"]
            assert all(float(v).is_integer() for v in tier["anchor_px"]), tier["file"]
    assert listed == {f.name for f in FOLDER.glob("*.webp")}


def test_floor_states_swap_in_place_and_levels_are_whole_pixels_apart() -> None:
    for i in range(3):
        tiers = [MANIFEST["pieces"][f]["tiers"][i] for f in FLOORS]
        assert len({(tuple(t["size"]), tuple(t["anchor_px"])) for t in tiers}) == 1
    assert isinstance(MANIFEST["step_px"], int) and isinstance(MANIFEST["lobby_step_px"], int)
    assert {"spine", "spine-lobby", "lift", "lift-lobby", "lobby", "roof", "plinth"} <= set(MANIFEST["pieces"])
