"""art/scripts/artlib.py canonical_camera: Blender renders one metre along X, Y and Z where canonical_projection says,
so every asset drawn with it shares the concepts' oblique projection. Runs Blender (BLENDER, default
~/.local/bin/blender); skipped where there is none."""

import math
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).parent.parent
BLENDER = Path(os.environ.get('BLENDER', Path.home() / '.local' / 'bin' / 'blender'))
PX_PER_M = 100.0
MARKERS = {'origin': ((0, 0, 0), '#ffffff'), 'x': ((1, 0, 0), '#ff0000'), 'y': ((0, 1, 0), '#00ff00'),
           'z': ((0, 0, 1), '#0000ff')}

SCENE = f"""
import sys
sys.path.insert(0, {str(ROOT / 'art' / 'scripts')!r})
import bpy
import artlib as A
A.reset()
sc = bpy.context.scene
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.light = 'FLAT'
sc.display.shading.color_type = 'OBJECT'
sc.display.render_aa = 'OFF'
sc.view_settings.view_transform = 'Standard'
sc.render.resolution_x, sc.render.resolution_y = 400, 400
A.canonical_camera(sc, (0, 0, 0), {PX_PER_M})
for name, (loc, col) in {MARKERS!r}.items():
    ob = A.ball(name, 0.05, loc, A.material(name, col))
    ob.color = A.srgb(col)
sc.render.filepath = sys.argv[-1]
bpy.ops.render.render(write_still=True)
print('projection', A.canonical_projection({PX_PER_M}))
"""


@pytest.fixture(scope='module')
def render(tmp_path_factory: pytest.TempPathFactory) -> tuple[np.ndarray, str]:
    if not BLENDER.exists():
        pytest.skip(f'no Blender at {BLENDER}')
    d = tmp_path_factory.mktemp('canon')
    script, png = d / 'scene.py', d / 'axes.png'
    script.write_text(SCENE)
    out = subprocess.run([str(BLENDER), '-b', '--factory-startup', '-P', str(script), '--', str(png)],
                         capture_output=True, text=True, timeout=300)
    assert png.exists(), out.stdout[-2000:] + out.stderr[-2000:]
    line = next(l for l in out.stdout.splitlines() if l.startswith('projection'))
    return np.asarray(Image.open(png).convert('RGB')).astype(int), line


def centroid(img: np.ndarray, colour: str) -> np.ndarray:
    want = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)])
    ys, xs = np.nonzero(np.abs(img - want).max(axis=2) < 40)
    assert len(xs) > 20, f'marker {colour} not found'
    return np.array([xs.mean() + 0.5, ys.mean() + 0.5])


def test_axes_land_where_the_canonical_projection_says(render) -> None:
    img, line = render
    o = centroid(img, MARKERS['origin'][1])
    assert np.allclose(o, (200, 200), atol=0.5)
    y, t = math.radians(30), 0.5
    want = {'x': (math.cos(y), math.sin(y) * t), 'y': (math.sin(y), -math.cos(y) * t), 'z': (0, -1)}
    for axis, (dx, dy) in want.items():
        got = centroid(img, MARKERS[axis][1]) - o
        assert np.allclose(got, np.array([dx, dy]) * PX_PER_M, atol=0.5), (axis, got)
    printed = np.array(eval(line.removeprefix('projection')))  # canonical_projection, as Blender computed it
    assert np.allclose(printed, np.array([want[a] for a in 'xyz']) * PX_PER_M)


def test_screen_directions_match_the_concepts(render) -> None:
    """Ground X descends right at 16.1 deg, ground Y rises right at 40.9 deg, verticals are vertical and full length."""
    img, _ = render
    o = centroid(img, MARKERS['origin'][1])
    angle = {a: math.degrees(math.atan2(-(p := centroid(img, MARKERS[a][1]) - o)[1], p[0])) for a in 'xyz'}
    assert angle['x'] == pytest.approx(-math.degrees(math.atan(math.tan(math.radians(30)) / 2)), abs=0.3)
    assert angle['x'] == pytest.approx(-16.10, abs=0.3)
    assert angle['y'] == pytest.approx(40.89, abs=0.3)
    assert angle['z'] == pytest.approx(90.0, abs=0.3)
    assert np.linalg.norm(centroid(img, MARKERS['z'][1]) - o) == pytest.approx(PX_PER_M, abs=0.5)


FRAMED = {'a': ((1.2, -0.7, 0.0), '#ff0000'), 'b': ((-0.9, 0.8, 1.6), '#00ff00'), 'c': ((0.3, 0.2, 0.4), '#0000ff')}

FRAME_SCENE = f"""
import sys, json
sys.path.insert(0, {str(ROOT / 'art' / 'scripts')!r})
import bpy
from mathutils import Vector
import artlib as A
A.reset()
sc = bpy.context.scene
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.light = 'FLAT'
sc.display.shading.color_type = 'OBJECT'
sc.display.render_aa = 'OFF'
sc.view_settings.view_transform = 'Standard'
pts = {{k: Vector(p) for k, (p, _) in {FRAMED!r}.items()}}
for name, (loc, col) in {FRAMED!r}.items():
    ob = A.ball(name, 0.05, loc, A.material(name, col))
    ob.color = A.srgb(col)
f = A.canonical_frame(sc, list(pts.values()), {PX_PER_M}, 0.3, 'cam', grid=8)
right, down, _ = A.canonical_axes()
u0, v0 = f['origin']
want = {{k: [(p.dot(right) - u0) * {PX_PER_M}, (p.dot(down) - v0) * {PX_PER_M}] for k, p in pts.items()}}
sc.render.filepath = sys.argv[-1]
bpy.ops.render.render(write_still=True)
print('framed', json.dumps({{'size': f['size'], 'want': want}}))
"""


def test_a_framing_puts_points_where_its_origin_says() -> None:
    """canonical_frame (the kit, props and robot sprites' framing): a world point lands at its screen metres minus the
    returned origin, times px/m, in an image of the returned size (a multiple of the grid)."""
    if not BLENDER.exists():
        pytest.skip(f'no Blender at {BLENDER}')
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        script, png = Path(d) / 'scene.py', Path(d) / 'framed.png'
        script.write_text(FRAME_SCENE)
        out = subprocess.run([str(BLENDER), '-b', '--factory-startup', '-P', str(script), '--', str(png)],
                             capture_output=True, text=True, timeout=300)
        assert png.exists(), out.stdout[-2000:] + out.stderr[-2000:]
        img = np.asarray(Image.open(png).convert('RGB')).astype(int)
    info = json.loads(next(l for l in out.stdout.splitlines() if l.startswith('framed')).removeprefix('framed'))
    assert [img.shape[1], img.shape[0]] == info['size'] and all(v % 8 == 0 for v in info['size'])
    for name, (_, colour) in FRAMED.items():
        assert np.allclose(centroid(img, colour), info['want'][name], atol=0.5), name
