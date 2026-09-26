"""Build the l2 workbench scene: a room corner with the plan wall, one bench of three desks,
the question desk under the lantern, a shelf and plants.

    blender -b -P art/scripts/build_workbench.py

Units are metres, Z up. The back wall runs along +X at y = ROOM_D; the lift wall along +Y at x = 0.
Warm light groups: desk1..desk3 (desk lamps) and planwall (the washers above the plan wall).
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

SCENE = 'workbench'
ROOM_W, ROOM_D, WALL_H, WALL_T = 10.0, 7.0, 3.2, 0.3
TILE = 0.6
DESK_W, DESK_D, DESK_H, TOP_T = 1.6, 0.8, 0.74, 0.03
BENCH_X0, BENCH_Y = 2.6, 3.6  # left end and centre line of the bench
SEAT_H = 0.47  # shared with the robot rig
PLAN = dict(x0=3.4, z0=0.75, cols=9, rows=6, pitch=0.38)


def materials() -> dict:
    p = A.paths(SCENE)
    return {
        'floor': A.material('floor_tile', '#e4e0e8', rough=0.35, texture=floor_tile_texture(p['textures'])),
        'wall': A.material('wall_plaster', '#cfc9cc', rough=0.9,
                           texture=A.source('ambientcg', 'PaintedPlaster017', 'PaintedPlaster017_1K-JPG_Color.jpg')),
        'cap': A.material('wall_cap', '#e6dad0', rough=0.8),
        'pilaster': A.material('pilaster', '#bdb3b2', rough=0.8),
        'oak': A.material('oak', '#fff4e6', rough=0.45,
                          texture=A.source('ambientcg', 'Wood095', 'Wood095_1K-JPG_Color.jpg')),
        'steel': A.material('steel_grey', '#7a7a82', rough=0.5, metal=0.2),
        'frame': A.material('frame_dark', '#56525a', rough=0.45, metal=0.4),
        'black': A.material('chair_black', '#2a292c', rough=0.6),
        'lift': A.material('lift_steel', '#8a8890', rough=0.3, metal=0.6),
        'screen': A.material('screen', '#15161a', rough=0.2),
        'paper': A.material('paper', '#fbf3e6', rough=0.9),
        'pot': A.material('pot', '#b9aeab', rough=0.85),
        'mug': A.material('mug', '#e9e4de', rough=0.3),
        'pencil': A.material('pencil', '#e8b04a', rough=0.5),
        'tile': A.material('plan_tile', '#e8e4e6', rough=0.6),
        'bulb': A.material('bulb', '#fefddd', rough=0.3, emission='#ffd9a0'),
        'lantern': A.material('lantern', '#a60e9b', rough=0.25, emission='#fa9ffa'),
        'button': A.material('button', '#e6e2da', rough=0.4, emission='#fff1d6'),
    }


def floor_tile_texture(out: Path) -> Path:
    """One 0.6 m tile: the Concrete034 photo with a 3 mm grout line, written for the floor material."""
    src = bpy.data.images.load(str(A.source('ambientcg', 'Concrete034', 'Concrete034_1K-JPG_Color.jpg')))
    w, h = src.size
    px = np.empty(w * h * 4, dtype=np.float32)
    src.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    rgb = px[..., :3]
    rgb[:] = 0.82 + (rgb - rgb.mean()) * 0.35  # keep the stone's variation, lose most of its tone
    g = max(2, round(w * 0.003 / TILE))
    for sl in (np.s_[:g, :], np.s_[-g:, :], np.s_[:, :g], np.s_[:, -g:]):
        rgb[sl] *= 0.8
    out.mkdir(parents=True, exist_ok=True)
    img = bpy.data.images.new('floor_tile', w, h)
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = str(out / 'floor_tile.png')
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    bpy.data.images.remove(src)
    return out / 'floor_tile.png'


def shell(m) -> None:
    A.box('floor_slab', (ROOM_W + WALL_T, ROOM_D + WALL_T, 0.3), ((ROOM_W - WALL_T) / 2, (ROOM_D + WALL_T) / 2, -0.3),
          m['cap'], bevel=0.01, drop=('+z', '+y', '-x'))
    A.box('floor', (ROOM_W, ROOM_D, 0.004), (ROOM_W / 2, ROOM_D / 2, 0.0), m['floor'], bevel=0, tile=TILE)
    # the camera never sees the outside of the two standing walls, nor their tops under the caps
    A.box('wall_back', (ROOM_W + WALL_T, WALL_T, WALL_H), ((ROOM_W - WALL_T) / 2, ROOM_D + WALL_T / 2, 0), m['wall'],
          bevel=0.01, tile=1.5, drop=('+y', '+z'))
    A.box('wall_lift', (WALL_T, ROOM_D, WALL_H), (-WALL_T / 2, ROOM_D / 2, 0), m['wall'], bevel=0.01, tile=1.5,
          drop=('-x', '+z'))
    A.box('cap_back', (ROOM_W + WALL_T + 0.02, WALL_T + 0.02, 0.03), ((ROOM_W - WALL_T) / 2, ROOM_D + WALL_T / 2, WALL_H),
          m['cap'], drop=('-z', '+y'))
    A.box('cap_lift', (WALL_T + 0.02, ROOM_D, 0.03), (-WALL_T / 2, ROOM_D / 2, WALL_H), m['cap'], drop=('-z', '-x'))
    for i, x in enumerate((2.7, 7.9)):
        A.box(f'pilaster_{i}', (0.5, 0.16, WALL_H), (x, ROOM_D - 0.08, 0), m['pilaster'], bevel=0.01)
    A.box('skirting', (ROOM_W, 0.015, 0.08), (ROOM_W / 2, ROOM_D - 0.0075, 0), m['pilaster'], bevel=0.003)
    # lift: recessed frame, two doors, call buttons
    A.box('lift_frame', (0.1, 1.5, 2.35), (0.05, 5.2, 0), m['pilaster'], bevel=0.01)
    for i, y in enumerate((4.87, 5.53)):
        A.box(f'lift_door_{i}', (0.04, 0.64, 2.2), (0.1, y, 0), m['lift'], bevel=0.004)
    A.box('lift_buttons', (0.03, 0.12, 0.34), (0.03, 4.25, 1.05), m['lift'], bevel=0.004)
    for i in range(3):
        A.cylinder(f'lift_button_{i}', 0.025, 0.012, (0.045, 4.25, 1.13 + i * 0.09), m['button'], kind='dynamic',
                   rot=(0, math.pi / 2, 0))


def plan_wall(m) -> None:
    c, r, pitch = PLAN['cols'], PLAN['rows'], PLAN['pitch']
    w, h = c * pitch + 0.12, r * pitch + 0.12
    x0, z0, y = PLAN['x0'], PLAN['z0'], ROOM_D - 0.04
    A.box('plan_frame', (w, 0.08, h), (x0 + w / 2, y, z0), m['steel'], bevel=0.01)
    for row in range(r):
        for col in range(c):
            A.box(f'plan_tile_r{row}_c{col}', (pitch - 0.035, 0.03, pitch - 0.035),
                  (x0 + 0.06 + (col + 0.5) * pitch, y - 0.055, z0 + 0.06 + row * pitch + 0.0175),
                  m['tile'], kind='dynamic', bevel=0.008)
    for i in range(5):
        x = x0 + w * (i + 0.5) / 5
        A.cylinder(f'washer_{i}', 0.09, 0.07, (x, ROOM_D - 0.1, z0 + h + 0.25), m['frame'], bevel=0.01,
                   rot=(math.pi / 2, 0, 0))
        A.cylinder(f'washer_bulb_{i}', 0.07, 0.005, (x, ROOM_D - 0.175, z0 + h + 0.25), m['bulb'], kind='dynamic',
                   rot=(math.pi / 2, 0, 0))['warm'] = 'planwall'
        spot = A.light(f'washer_light_{i}', 'SPOT', (x, ROOM_D - 0.3, z0 + h + 0.28), 60, '#ffc98a',
                       warm='planwall', spot_size=math.radians(70), spot_blend=0.8, shadow_soft_size=0.05)
        A.aim(spot, (x, ROOM_D, z0 + h * 0.35))


def chair(m, name, x, y, facing: float) -> None:
    """Task chair centred at (x, y), seat front facing angle `facing` (0 = -Y)."""
    rz = facing
    for i in range(5):
        a = rz + i * 2 * math.pi / 5
        dx, dy = math.sin(a) * 0.15, -math.cos(a) * 0.15
        A.box(f'{name}_leg_{i}', (0.05, 0.3, 0.035), (x + dx, y + dy, 0.055), m['black'], rot=(0, 0, a), bevel=0.012)
        A.cylinder(f'{name}_caster_{i}', 0.028, 0.025, (x + dx * 2, y + dy * 2, 0.028), m['black'], segments=12,
                   rot=(0, math.pi / 2, a))
    A.cylinder(f'{name}_column', 0.025, SEAT_H - 0.14, (x, y, 0.09), m['frame'], segments=16)
    sx, sy = math.sin(rz), -math.cos(rz)
    A.box(f'{name}_seat', (0.5, 0.48, 0.08), (x, y, SEAT_H - 0.08), m['black'], rot=(0, 0, rz), bevel=0.03)
    A.box(f'{name}_back', (0.46, 0.06, 0.52), (x - sx * 0.24, y - sy * 0.24, SEAT_H + 0.06), m['black'],
          rot=(math.radians(-8), 0, rz), bevel=0.025)
    for side in (-1, 1):
        ax, ay = math.cos(rz) * side * 0.26, math.sin(rz) * side * 0.26
        A.box(f'{name}_arm_{side:+d}', (0.05, 0.3, 0.035), (x + ax, y + ay, SEAT_H + 0.18), m['black'],
              rot=(0, 0, rz), bevel=0.012)
        A.box(f'{name}_armpost_{side:+d}', (0.03, 0.04, 0.18), (x + ax, y + ay, SEAT_H), m['frame'], rot=(0, 0, rz),
              bevel=0.006)


def desk_lamp(m, name, group, x, y, flip: int) -> None:
    A.cylinder(f'{name}_base', 0.07, 0.02, (x, y, DESK_H), m['frame'], bevel=0.005)
    A.box(f'{name}_arm1', (0.015, 0.015, 0.34), (x, y, DESK_H + 0.02), m['frame'], rot=(math.radians(-15) * flip, 0, 0),
          bevel=0.004)
    hx, hy, hz = x, y + 0.2 * flip, DESK_H + 0.4
    A.box(f'{name}_arm2', (0.015, 0.22, 0.015), (x, y + 0.1 * flip, DESK_H + 0.34), m['frame'],
          rot=(math.radians(-20) * flip, 0, 0), bevel=0.004)
    A.cylinder(f'{name}_shade', 0.035, 0.12, (hx, hy, hz - 0.08), m['frame'], radius2=0.075, segments=20, bevel=0.004,
               rot=(0, math.pi, 0))
    A.cylinder(f'{name}_bulb', 0.03, 0.004, (hx, hy, hz - 0.205), m['bulb'], kind='dynamic', segments=16)['warm'] = group
    # just below the shade's mouth: the shade is a closed solid and would swallow the light
    spot = A.light(f'{name}_light', 'SPOT', (hx, hy, hz - 0.215), 25, '#ffc27a', warm=group,
                   spot_size=math.radians(100), spot_blend=0.9, shadow_soft_size=0.03)
    A.aim(spot, (hx, hy + 0.1 * flip, DESK_H))


def desk(m, i: int, rng: random.Random, props: dict) -> None:
    """Desk i of the bench: top, legs, pedestal, two chairs and a set of props that differs per desk."""
    x0 = BENCH_X0 + i * DESK_W
    cx, name = x0 + DESK_W / 2, f'desk{i + 1}'
    A.box(f'{name}_top', (DESK_W - 0.006, DESK_D, TOP_T), (cx, BENCH_Y, DESK_H - TOP_T), m['oak'], bevel=0.006, tile=1.2)
    for lx in (x0 + 0.05, x0 + DESK_W - 0.05):
        for ly in (BENCH_Y - DESK_D / 2 + 0.05, BENCH_Y + DESK_D / 2 - 0.05):
            A.box(f'{name}_leg_{lx:.2f}_{ly:.2f}', (0.045, 0.045, DESK_H - TOP_T), (lx, ly, 0), m['frame'], bevel=0.006)
    A.box(f'{name}_rail', (DESK_W - 0.1, 0.03, 0.06), (cx, BENCH_Y, DESK_H - TOP_T - 0.06), m['frame'], bevel=0.005)
    A.box(f'{name}_pedestal', (0.42, 0.55, 0.6), (x0 + DESK_W - 0.3, BENCH_Y - DESK_D / 2 + 0.3, 0.02), m['steel'],
          bevel=0.012)
    for d in range(3):
        A.box(f'{name}_drawer_{d}', (0.36, 0.008, 0.16), (x0 + DESK_W - 0.3, BENCH_Y - DESK_D / 2 + 0.024, 0.07 + d * 0.19),
              m['steel'], bevel=0.003)
    chair(m, f'{name}_chair_far', cx - 0.15, BENCH_Y + DESK_D / 2 + 0.35, math.pi)
    chair(m, f'{name}_chair_near', cx - 0.25, BENCH_Y - DESK_D / 2 - 0.45, 0.0)
    top = DESK_H
    # far side is the robot's working side; lamp on its left
    desk_lamp(m, f'{name}_lamp', name, x0 + 0.2, BENCH_Y + 0.25, -1)
    if i == 2:
        A.box(f'{name}_monitor_stand', (0.2, 0.16, 0.02), (cx, BENCH_Y + 0.1, top), m['frame'])
        A.box(f'{name}_monitor_neck', (0.04, 0.03, 0.2), (cx, BENCH_Y + 0.14, top), m['frame'])
        A.box(f'{name}_monitor', (0.56, 0.03, 0.34), (cx, BENCH_Y + 0.12, top + 0.13), m['screen'], bevel=0.008,
              rot=(math.radians(-5), 0, math.pi))
    else:
        lx, ly = cx - 0.1 + rng.uniform(-0.05, 0.05), BENCH_Y + 0.1
        A.box(f'{name}_laptop_base', (0.34, 0.24, 0.016), (lx, ly, top), m['frame'], bevel=0.004)
        A.box(f'{name}_laptop_lid', (0.34, 0.012, 0.23), (lx, ly - 0.12, top + 0.012), m['frame'], bevel=0.004,
              rot=(math.radians(-15), 0, 0))
    px, py = cx + 0.35, BENCH_Y + 0.2
    A.cylinder(f'{name}_penpot', 0.035, 0.1, (px, py, top), m['steel'], bevel=0.004)
    for k in range(3):
        a = rng.uniform(-0.25, 0.25)
        A.cylinder(f'{name}_pencil_{k}', 0.004, 0.16, (px + 0.01 * (k - 1), py + 0.008 * k, top + 0.02), m['pencil'],
                   segments=6, rot=(a, rng.uniform(-0.25, 0.25), 0))
    for k in range(rng.randint(2, 4)):
        A.box(f'{name}_paper_{k}', (0.21, 0.297, 0.002),
              (cx + rng.uniform(-0.55, 0.45), BENCH_Y + rng.uniform(-0.3, 0.05), top + 0.001 * (k + 1)),
              m['paper'], bevel=0, rot=(0, 0, rng.uniform(-0.5, 0.5)), tile=None)
    if i != 1:
        mx, my = cx + rng.uniform(0.1, 0.5), BENCH_Y - 0.15
        A.cylinder(f'{name}_mug', 0.04, 0.095, (mx, my, top), m['mug'], bevel=0.004)
        A.box(f'{name}_mug_handle', (0.012, 0.05, 0.06), (mx + 0.045, my, top + 0.02), m['mug'], bevel=0.005)
    if i == 0:
        A.duplicate(props['anthurium'], f'{name}_plant', (x0 + 0.55, BENCH_Y + 0.25, top), rng.uniform(0, 6.28))
    if i == 1:
        for k in range(3):
            A.box(f'{name}_book_{k}', (0.24, 0.17, 0.025), (x0 + 0.5, BENCH_Y - 0.2, top + k * 0.025),
                  A.material(f'book_{k}', ['#6a7f95', '#c9b99a', '#8d6e63'][k], rough=0.7), bevel=0.003,
                  rot=(0, 0, rng.uniform(-0.2, 0.2)))


def question_desk(m) -> None:
    x, y = 1.9, ROOM_D - 0.55
    A.box('qdesk_body', (1.0, 0.6, 0.72), (x, y, 0), m['steel'], bevel=0.012)
    A.box('qdesk_top', (1.1, 0.66, 0.035), (x, y, 0.72), m['oak'], bevel=0.006, tile=1.2)
    A.box('qdesk_card', (0.16, 0.004, 0.12), (x - 0.1, y - 0.05, 0.755), m['paper'], rot=(math.radians(-20), 0, 0),
          kind='dynamic', bevel=0)
    A.cylinder('lantern_cord', 0.004, 1.0, (x - 0.1, y, WALL_H - 1.0), m['frame'], segments=6)
    A.octahedron('lantern', 0.12, 0.32, (x - 0.1, y, WALL_H - 1.18), m['lantern'])


def shelf(m, props: dict, rng: random.Random) -> None:
    x0, y, w = 8.4, ROOM_D - 0.22, 1.4
    for side in (0, 1):
        A.box(f'shelf_side_{side}', (0.03, 0.4, 1.8), (x0 + side * w, y, 0), m['steel'], bevel=0.004)
    for k, z in enumerate((0.05, 0.62, 1.2, 1.77)):
        A.box(f'shelf_board_{k}', (w, 0.4, 0.025), (x0 + w / 2, y, z), m['steel'], bevel=0.004)
    for k in range(7):
        A.duplicate(props['binder'], f'shelf_binder_{k}', (x0 + 0.15 + k * 0.05, y, 0.645))
    A.duplicate(props['box'], 'shelf_box', (x0 + 1.05, y, 1.225), rng.uniform(-0.2, 0.2))


def planters(m, props: dict) -> None:
    for i, (x, y) in enumerate(((2.15, ROOM_D - 1.3), (7.3, ROOM_D - 0.4), (9.4, 1.0))):
        A.box(f'planter_{i}', (0.36, 0.36, 0.42), (x, y, 0), m['pot'], bevel=0.015)
        A.duplicate(props['calathea'], f'planter_{i}_plant', (x, y, 0.4), i * 1.3)


def lights() -> None:
    A.world_hdri(A.source('polyhaven', 'white_studio_06', 'white_studio_06_1k.hdr'), strength=1.0,
                 rotation=math.radians(120))
    sun = A.light('sun', 'SUN', (0, 0, 10), 2.0, '#fff4e5', angle=math.radians(8))
    sun.rotation_euler = (math.radians(50), 0, math.radians(-35))
    for i, x in enumerate((2.5, 5.0, 7.5)):
        A.light(f'fill_{i}', 'AREA', (x, ROOM_D / 2, WALL_H), 120, '#f4f1ec', shape='RECTANGLE', size=2.2, size_y=5.0)


def props() -> dict:
    ph = lambda i: A.source('polyhaven', i, f'{i}_1k.gltf')  # noqa: E731
    return {'anthurium': A.import_gltf(ph('anthurium_botany_01'), 'anthurium_botany_05_e', 'proto_anthurium'),
            'calathea': A.import_gltf(ph('calathea_orbifolia_01'), 'calathea_orbifolia_01_a', 'proto_calathea'),
            # stand the closed binder on its edge, thickness along the shelf
            'binder': A.import_gltf(ph('binder_notebook'), 'binder_notebook_closed', 'proto_binder',
                                    rot=(0, math.pi / 2, 0)),
            'box': A.import_gltf(ph('cardboard_box_01'), 'cardboard_box_01', 'proto_box', scale=0.6)}


def main() -> None:
    A.require_blender()
    A.reset()
    rng = random.Random(2)
    m = materials()
    p = props()
    shell(m)
    plan_wall(m)
    for i in range(3):
        desk(m, i, rng, p)
    question_desk(m)
    shelf(m, p, rng)
    planters(m, p)
    for proto in p.values():
        bpy.data.objects.remove(proto)
    lights()
    A.ortho_camera('camera_l2', (5.0, 4.2, 0.9), pitch_deg=30, yaw_deg=22, scale=8.5)
    info = A.lightmap_uvs(texel=0.015)
    info['warm_groups'] = sorted({o['warm'] for o in bpy.data.objects if 'warm' in o})
    info['seat_height'] = SEAT_H
    A.save_blend(SCENE, info)
    print('BUILD', SCENE, info)


main()
