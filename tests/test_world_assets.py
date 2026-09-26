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
SCENES = sorted(p.name for p in WORLD.iterdir() if (p / "manifest.json").exists())
BUDGET = 15 * 1000 * 1000


def manifest(scene: str) -> dict:
    return json.loads((WORLD / scene / "manifest.json").read_text())


def test_there_are_the_workbench_and_the_robot() -> None:
    assert {"workbench", "robot"} <= set(SCENES)


@pytest.mark.parametrize("scene", SCENES)
def test_output_matches_its_manifest_and_budget(scene: str) -> None:
    m = manifest(scene)
    folder = WORLD / scene
    on_disk = {f.name for f in folder.iterdir()} - {"manifest.json"}
    assert on_disk == set(m["files"])
    for name, meta in m["files"].items():
        data = (folder / name).read_bytes()
        assert len(data) == meta["bytes"] and hashlib.sha256(data).hexdigest() == meta["sha256"], name
    assert sum(f.stat().st_size for f in folder.iterdir()) < BUDGET
    if m["lightmap"] is None:  # a character: nothing baked, animated instead
        assert m["actions"]
        return
    layers = m["lightmap"]["layers"]
    assert "base" in layers and set(layers) - {"base"} == set(m["warm_groups"])
    assert all(layer["scale"] > 0 for layer in layers.values())


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
