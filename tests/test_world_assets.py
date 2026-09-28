"""Baked world scenes built by art/build.sh: the committed output is complete and small, and the deck's vendored
three.js loads it (WebP textures, lightmap UVs on baked meshes, tags in userData)."""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from conftest import FIXTURE, serve_fixture

WORLD = Path(__file__).parent.parent / "fleet" / "web" / "assets" / "world"
# baked 3D scenes; sprite kits (a manifest with "sprites") are tested in test_world_kit.py
SCENES = sorted(p.name for p in WORLD.iterdir()
                if (p / "manifest.json").exists() and "glb" in json.loads((p / "manifest.json").read_text()))
BUDGET = 15 * 1000 * 1000


def manifest(scene: str) -> dict:
    return json.loads((WORLD / scene / "manifest.json").read_text())


def test_there_are_the_workbench_and_the_robot() -> None:
    assert {"workbench", "robot"} <= set(SCENES)


@pytest.mark.parametrize("scene", SCENES)
def test_output_matches_its_manifest_and_budget(scene: str) -> None:
    m = manifest(scene)
    folder = WORLD / scene
    files = [f for f in folder.iterdir() if f.is_file()]  # the robot's sprites/ are built separately (below)
    on_disk = {f.name for f in files} - {"manifest.json"}
    assert on_disk == set(m["files"])
    for name, meta in m["files"].items():
        data = (folder / name).read_bytes()
        assert len(data) == meta["bytes"] and hashlib.sha256(data).hexdigest() == meta["sha256"], name
    assert sum(f.stat().st_size for f in files) < BUDGET
    if m["lightmap"] is None:  # a character: nothing baked, animated instead
        assert m["actions"]
        return
    layers = m["lightmap"]["layers"]
    assert "base" in layers and set(layers) - {"base"} == set(m["warm_groups"])
    assert all(layer["scale"] > 0 for layer in layers.values())


def test_the_robot_sprites_are_complete_and_small() -> None:
    """art/scripts/build_robot_sprites.py: every page the manifest names exists and nothing else does; the sets
    loaded up front (1x, 2x and the manifest) stay under 8 MB; every frame of every clip has a body."""
    folder = WORLD / "robot" / "sprites"
    m = json.loads((folder / "sprites.json").read_text())
    named = {p[k] for r in m["resolutions"].values() for p in r["pages"] for k in ("color", "mask")}
    named |= {p["image"] for r in m["resolutions"].values() for p in r["shadow_pages"]}
    on_disk = {str(f.relative_to(folder)) for f in folder.rglob("*") if f.is_file()} - {"sprites.json"}
    assert on_disk == named
    eager = [f for r, meta in m["resolutions"].items() if meta["load"] == "eager" for f in (folder / r).iterdir()]
    assert sum(f.stat().st_size for f in eager) + (folder / "sprites.json").stat().st_size < 8 * 1000 * 1000
    for clip, c in m["clips"].items():
        assert c["dirs"], clip
        for d, dd in c["dirs"].items():
            assert len(dd["frames"]) == c["frames"], (clip, d)
            for f in dd["frames"]:
                layers = f["layers"]
                assert "body" in layers or {"body_low", "body_high"} <= set(layers), (clip, d)
                assert all(len(entry) == len(m["resolutions"]) for entry in layers.values())


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with serve_fixture(FIXTURE) as u:
        yield u


LOAD = """async ([scene, glb, lightmaps]) => {
  const THREE = await import('three');
  const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
  const gltf = await new GLTFLoader().loadAsync(`/assets/world/${scene}/${glb}`);
  const baked = [], dynamic = [], maps = new Set();  // dynamic: every mesh not lit by the lightmap
  gltf.scene.traverse(o => {
    if (!o.isMesh) return;
    (o.userData.fleet === 'baked' ? baked : dynamic).push(o);
    if (o.material.map) maps.add(o.material.map);
  });
  const lm = await Promise.all(lightmaps.map(f => new THREE.TextureLoader().loadAsync(`/assets/world/${scene}/${f}`)));
  return {
    baked: baked.length,
    bakedWithoutUv1: baked.filter(o => !o.geometry.attributes.uv1).map(o => o.name),
    warm: dynamic.filter(o => o.userData.warm).map(o => [o.name, o.userData.warm]),
    mapsDecoded: [...maps].every(t => t.image && t.image.width > 0),
    lightmapSizes: lm.map(t => t.image.width),
  };
}"""


BAKED = [s for s in SCENES if manifest(s)["lightmap"]]


@pytest.mark.parametrize("scene", BAKED)
def test_three_loads_the_scene(browser: Browser, url: str, scene: str) -> None:
    m = manifest(scene)
    layers = m["lightmap"]["layers"]
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "/")
    got = page.evaluate(LOAD, [scene, m["glb"], [layer["file"] for layer in layers.values()]])
    page.close()
    assert errors == []
    assert got["baked"] > 0 and got["bakedWithoutUv1"] == []
    assert got["mapsDecoded"]
    assert got["lightmapSizes"] == [layer["size"] for layer in layers.values()]
    warm = {}
    for name, group in got["warm"]:
        warm.setdefault(group, set()).add(name)
    assert warm == {g: set(names) for g, names in m["warm_groups"].items()}
