"""Proxies of l0, l1 and l2 at each candidate projection, and the canonical camera's shadow proof.

    blender -b --factory-startup -P art/scripts/shoot_projection.py [-- TAG ...]

Reads art/build/projection/measure.json (measure_projection.py) and renders, for every image and candidate, a box
proxy sized by that candidate's landmark fit, framed so the fit's origin lands on its pixel: render/<image>_<candidate>.png
(1672 x 941, transparent, edges in Freestyle). TAG (e.g. l1_recommended) renders just those. Then the shadow scene:
a block and a floating slab under a sun, once with canonical_camera and once with a plain orthographic camera at the
same pitch, for measure_projection.py --overlays to compare.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

OUT = A.BUILD / 'projection'
W, H = 1672, 941
BLUE, DARK, PALE, JOINT = ('blue', '#7fb2e5'), ('dark', '#3d5a80'), ('pale', '#a8d0f0'), ('joint', '#1d3557')


def studio() -> bpy.types.Scene:
    A.reset()
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE_NEXT'
    sc.eevee.taa_render_samples = 16
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.film_transparent = True
    sc.view_settings.view_transform = 'Standard'
    sc.render.use_freestyle = True
    sc.render.line_thickness_mode = 'ABSOLUTE'
    sc.render.line_thickness = 1.6
    fs = sc.view_layers[0].freestyle_settings
    lines = fs.linesets[0] if len(fs.linesets) else fs.linesets.new('edges')
    lines.linestyle = lines.linestyle or bpy.data.linestyles.new('edges')
    lines.linestyle.color = (0.75, 0.0, 0.55)
    w = bpy.data.worlds.new('World')
    sc.world = w
    w.use_nodes = True
    w.node_tree.nodes['Background'].inputs['Color'].default_value = (0.8, 0.85, 0.95, 1)
    w.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.6
    sun = A.light('sun', 'SUN', (0, 0, 10), 3.0, rot=(math.radians(40), 0, math.radians(-50)))
    sun.data.angle = math.radians(2)
    return sc


_mats: dict = {}


def block(name, x0, x1, y0, y1, z0, z1, m) -> None:
    if m[0] not in _mats:
        _mats[m[0]] = A.material(*m, rough=0.8)
    A.box(name, (abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)), ((x0 + x1) / 2, (y0 + y1) / 2, min(z0, z1)),
          _mats[m[0]], bevel=0)


def joints(x0, x1, y0, y1, z, step) -> None:
    """A floor tile grid: dark strips every `step` metres."""
    for i in range(1, int((x1 - x0) / step) + 1):
        if (x := x0 + i * step) < x1:
            block(f'jx{i}', x - 0.06, x + 0.06, y0, y1, z, z + 0.005, JOINT)
    for j in range(1, int((y1 - y0) / step) + 1):
        if (y := y0 + j * step) < y1:
            block(f'jy{j}', x0, x1, y - 0.06, y + 0.06, z, z + 0.005, JOINT)


def proxy(name: str, f: dict) -> float:
    """Build the proxy in metres from the fit's per-axis sizes; returns pixels per metre."""
    sx, sy, sz = f['scale']
    fr = f['free']
    if name == 'l0':   # units: slab width, depth, storey (4 m)
        ppm = sz / 4.0
        X, Y, Z = (lambda u: u * sx / ppm), (lambda v: v * sy / ppm), (lambda k: k * 4.0)
        pz = fr['pz']
        block('plinth', X(fr['px0']), X(fr['px1']), Y(fr['py0']), Y(fr['py1']), Z(pz) - 0.8, Z(pz), BLUE)
        joints(X(fr['px0']), X(fr['px1']), Y(fr['py0']), Y(fr['py1']), Z(pz), 3.0)
        block('spine', X(fr['xs']), 0, 0, Y(1), Z(pz), Z(7), BLUE)
        for k in range(1, 7):
            block(f'slab{k}', 0, X(1 if k >= 5 else fr['xg']), 0, Y(1), Z(k) - 0.45, Z(k), PALE)
        block('lift', X(1), X(fr['xr']), Y(fr['yl']), Y(1), Z(pz), Z(7), DARK)
        block('roof_back', X(fr['xs']), X(fr['xr']), Y(1) - 1.0, Y(1), Z(7) - 0.6, Z(7), PALE)
        block('roof_left', X(fr['xs']), 0, 0, Y(1), Z(7) - 0.6, Z(7), PALE)
    elif name == 'l1':  # units: plate width, depth, wall height (3 m)
        ppm = sz / 3.0
        w, d = sx / ppm, sy / ppm
        block('plate', 0, w, 0, d, -0.4, 0, BLUE)
        joints(0, w, 0, d, 0, 1.5)
        block('wall_back', 0, w, d - 0.3, d, 0, 3.0, PALE)
        block('wall_left', 0, 0.3, 0, d, 0, 3.0, PALE)
    else:  # l2: the workbench scene's metres, per-axis scale
        ppm = sz
        kx, ky = sx / ppm, sy / ppm
        block('floor', 0, 12 * kx, 0, 5.9 * ky, -0.1, 0, BLUE)
        joints(0, 12 * kx, 0, 5.9 * ky, 0, 1.2)
        block('wall', 0, 12 * kx, 5.9 * ky, 6.1 * ky, 0, 3.2, PALE)
        block('calendar', 5.16 * kx, 8.08 * kx, 5.86 * ky, 5.9 * ky, 0.76, 2.78, DARK)
        block('bench', 3.8 * kx, 9.2 * kx, 3.95 * ky, 4.85 * ky, 0, 0.74, PALE)
        block('question_desk', 3.0 * kx, 4.3 * kx, 5.22 * ky, 5.8 * ky, 0, 0.755, PALE)
    return ppm


def frame(sc, f: dict, ppm: float) -> None:
    """The candidate's camera on the world origin, shifted so the origin lands on the fit's origin pixel."""
    cam = A.ortho_camera('camera', (0, 0, 0), f['angle'], f['yaw'], W / ppm, distance=200)
    cam.data.sensor_fit = 'HORIZONTAL'
    cam.data.clip_end = 1000
    k = 1 / math.cos(math.radians(f['angle'])) if f['kind'] == 'oblique' else 1.0
    sc.render.pixel_aspect_x = k   # the oblique stretch, as canonical_camera does
    ox, oy = f['origin']
    cam.data.shift_x = -(ox - W / 2) / W
    cam.data.shift_y = (oy - H / 2) / (W * k)


def shadow_scene() -> None:
    target, ppm, half = (0.5, 0.0, 0.5), 80.0, 6.0
    boxes = [((-0.5, -0.5, 0.0), (0.5, 0.5, 2.0)), ((1.2, -0.9, 1.125), (3.2, -0.3, 1.275))]
    for mode in ('oblique', 'ortho'):
        A.reset()
        _mats.clear()
        sc = bpy.context.scene
        sc.render.engine = 'CYCLES'
        sc.cycles.samples = 64
        sc.cycles.device = 'GPU'
        sc.view_settings.view_transform = 'Standard'
        w = bpy.data.worlds.new('World')
        sc.world = w
        w.use_nodes = True
        w.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.0
        block('ground', -half, half, -half, half, -0.1, 0, ('white', '#ffffff'))
        block('block', *(v for pair in zip(*boxes[0]) for v in pair), ('red', '#d02020'))
        block('slab', *(v for pair in zip(*boxes[1]) for v in pair), ('blue', '#2040d0'))
        sun = A.light('sun', 'SUN', (0, 0, 10), 4.0, rot=(math.radians(50), 0, math.radians(-40)))
        sun.data.angle = 0.0
        sc.render.resolution_x = 800
        if mode == 'oblique':
            sc.render.resolution_y = 559   # 500 / cos(atan(1/2)), so the two frames cover the same ground
            A.canonical_camera(sc, target, ppm)
        else:
            sc.render.resolution_y = 500
            cam = A.ortho_camera('camera', target, A.CANONICAL_DEPRESSION, A.CANONICAL_YAW, 800 / ppm)
            cam.data.sensor_fit = 'HORIZONTAL'
        sc.render.filepath = str(OUT / f'shadow_{mode}.png')
        bpy.ops.render.render(write_still=True)
    d = sun.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    (OUT / 'shadow.json').write_text(json.dumps({'sun': list(d), 'target': target, 'px_per_m': ppm,
                                                 'centre': [400, 559 / 2], 'boxes': boxes, 'ground_half': half}))


def main() -> None:
    A.require_blender()
    only = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    R = json.loads((OUT / 'measure.json').read_text())['fits']
    (OUT / 'render').mkdir(parents=True, exist_ok=True)
    for name, r in R.items():
        for cn, f in r['fits'].items():
            tag = f"{name}_{cn.split()[0]}"
            if only and tag not in only:
                continue
            sc = studio()
            _mats.clear()
            frame(sc, f, proxy(name, f))
            sc.render.filepath = str(OUT / 'render' / f'{tag}.png')
            bpy.ops.render.render(write_still=True)
    if not only:
        shadow_scene()


if __name__ == '__main__':
    main()
