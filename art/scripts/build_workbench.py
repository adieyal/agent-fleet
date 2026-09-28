"""Build the l2 workbench scene (docs/images/concept/l2.png).

    blender -b -P art/scripts/build_workbench.py

One long back wall with pilasters and a top cap carries, left to right: the lift with its indicator
lights, the question desk under the lantern, the plan wall with its row of criteria lights, a pinned
diagram poster, and a recess with a shelf of binders. The bench of three desks stands parallel to it;
robots sit on the far side (loaded at runtime at the `seat_*` anchors), empty chairs on the near side.
A second desk sits in the near-left corner.

Units are metres, Z up; the back wall's face is at y = WALL_Y.
Warm light groups: desk1..desk3 (desk lamps) and criteria0..criteria4 (the lights above the plan wall).
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
X0, X1 = -1.0, 12.5          # extent of floor and back wall
Y0, WALL_Y = -3.5, 6.0       # near floor edge, back wall face
WALL_H, WALL_T, CAP_H = 3.2, 0.3, 0.26
TILE = 0.6
DESK_W, DESK_D, DESK_H, TOP_T = 1.8, 0.8, 0.74, 0.03  # l2's bench is long next to the wall features
BENCH_X0, BENCH_Y = 3.8, 4.35  # left end and centre line of the bench: close to the plan wall, as in l2
SEAT_H = 0.47                  # shared with the robot rig
PLAN = dict(x0=5.2, z0=0.8, cols=9, rows=6, pitch=0.3)
PILASTERS = (2.25, 4.55, 8.45, 10.05)


def materials() -> dict:
    p = A.paths(SCENE)
    return {
        # sampled against l2: its open floor is a cool, light lilac-grey (~#d2d1de)
        'floor': A.material('floor_tile', '#c6cbe6', rough=0.3, texture=floor_tile_texture(p['textures'])),
        'wall': A.material('wall_plaster', '#aaa3a5', rough=0.9,
                           texture=A.source('ambientcg', 'PaintedPlaster017', 'PaintedPlaster017_1K-JPG_Color.jpg')),
        'cap': A.material('wall_cap', '#d2cfd6', rough=0.8),
        'pilaster': A.material('pilaster', '#9d989e', rough=0.8),
        'oak': A.material('oak', '#f3dcc0', rough=0.45, texture=pale_wood_texture(p['textures'])),
        'steel': A.material('steel_grey', '#6f6f77', rough=0.45, metal=0.3),
        'bezel': A.material('plan_bezel', '#c3c0c6', rough=0.6),
        'frame': A.material('frame_dark', '#4a474e', rough=0.4, metal=0.4),
        'black': A.material('chair_black', '#262528', rough=0.6),
        'lift': A.material('lift_steel', '#7f7d86', rough=0.3, metal=0.6),
        'screen': A.material('screen', '#141519', rough=0.2),
        'paper': A.material('paper', '#e6e0d5', rough=0.9),
        'ink': A.material('ink', '#3a3a3e', rough=0.8),
        'pot': A.material('pot', '#b8b3b0', rough=0.85),
        'mug': A.material('mug', '#6c6c72', rough=0.35),
        'pencil': A.material('pencil', '#e8b04a', rough=0.5),
        'brass': A.material('brass', '#c89b52', rough=0.3, metal=0.8),
        'tile': A.material('plan_tile', '#ecebee', rough=0.55),
        'bulb': A.material('bulb', '#fefddd', rough=0.3, emission='#ffcf8a'),
        'indicator': A.material('indicator', '#fff6e0', rough=0.3, emission='#ffe6b0'),
        'lantern': A.material('lantern', '#b0189f', rough=0.25, emission='#f25cf0'),
        'poster': A.material('poster', '#ffffff', rough=0.85, texture=poster_texture(p['textures'])),
        'sketch': A.material('sketch', '#e8e3da', rough=0.9, texture=sketch_texture(p['textures'])),
    }


def floor_tile_texture(out: Path) -> Path:
    """One 0.6 m tile: the Concrete034 photo, flattened, with a 3 mm grout line."""
    src = bpy.data.images.load(str(A.source('ambientcg', 'Concrete034', 'Concrete034_1K-JPG_Color.jpg')))
    w, h = src.size
    px = np.empty(w * h * 4, dtype=np.float32)
    src.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    rgb = px[..., :3]
    rgb[:] = 0.82 + (rgb - rgb.mean()) * 0.3
    g = max(2, round(w * 0.003 / TILE))
    for sl in (np.s_[:g, :], np.s_[-g:, :], np.s_[:, :g], np.s_[:, -g:]):
        rgb[sl] *= 0.8
    bpy.data.images.remove(src)
    return save_png(out, 'floor_tile', px)


def pale_wood_texture(out: Path) -> Path:
    """Wood095 with most of its orange taken out: l2's desks are pale, and warmth comes from the lamps."""
    src = bpy.data.images.load(str(A.source('ambientcg', 'Wood095', 'Wood095_1K-JPG_Color.jpg')))
    w, h = src.size
    px = np.empty(w * h * 4, dtype=np.float32)
    src.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    rgb = px[..., :3]
    grey = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    rgb[:] = grey[..., None] + (rgb - grey[..., None]) * 0.35  # keep a third of the saturation
    rgb[:] = 0.66 + (rgb - rgb.mean()) * 0.8                    # a pale wood, keeping the grain
    bpy.data.images.remove(src)
    return save_png(out, 'pale_wood', px)


def poster_texture(out: Path) -> Path:
    """The pinned diagram: a circle over two linked boxes, a small bar chart and a few text dashes."""
    w, h = 384, 512
    img = np.ones((h, w, 4), dtype=np.float32)
    img[..., :3] = (0.96, 0.95, 0.93)
    yy, xx = np.mgrid[0:h, 0:w]
    ink = (0.33, 0.33, 0.36)

    def stroke(mask):
        img[mask, :3] = ink

    def rect(x0, y0, x1, y1, t=4):
        stroke(((xx >= x0) & (xx <= x1) & (yy >= y0) & (yy <= y1))
               & ~((xx > x0 + t) & (xx < x1 - t) & (yy > y0 + t) & (yy < y1 - t)))

    r = np.hypot(xx - 110, yy - 400)
    stroke((r < 48) & (r > 43))
    stroke((abs(xx - 110) < 3) & (yy < 352) & (yy > 300))
    rect(70, 220, 150, 300)
    rect(210, 290, 300, 370)
    stroke((abs(yy - 330) < 3) & (xx > 150) & (xx < 210))
    stroke((abs(xx - 110) < 3) & (yy < 220) & (yy > 160))
    rect(70, 80, 150, 160)
    for i, bh in enumerate((30, 55, 80, 110)):
        stroke((xx > 230 + i * 22) & (xx < 244 + i * 22) & (yy > 60) & (yy < 60 + bh))
    for i, ln in enumerate((40, 26, 34)):
        stroke((abs(yy - (90 - i * 16)) < 3) & (xx > 170) & (xx < 170 + ln))
    return save_png(out, 'poster', img)


def sketch_texture(out: Path) -> Path:
    """A plan sheet: four framed panels with a few pencil lines, like the drawings on l2's desks."""
    w, h = 256, 362
    img = np.ones((h, w, 4), dtype=np.float32)
    img[..., :3] = (0.93, 0.91, 0.87)
    yy, xx = np.mgrid[0:h, 0:w]
    ink = (0.3, 0.3, 0.34)
    # strokes are thick: a sheet is only ~40 px wide on screen at the l2 zoom
    for (x0, y0) in ((24, 30), (134, 30), (24, 196), (134, 196)):
        x1, y1 = x0 + 98, y0 + 136
        frame = ((xx >= x0) & (xx <= x1) & (yy >= y0) & (yy <= y1)) & \
            ~((xx > x0 + 8) & (xx < x1 - 8) & (yy > y0 + 8) & (yy < y1 - 8))
        img[frame, :3] = ink
        img[(abs((yy - y0) - (xx - x0) * 1.2) < 5) & (xx > x0 + 14) & (xx < x1 - 30), :3] = ink
        img[(abs(yy - (y0 + 100)) < 5) & (xx > x0 + 18) & (xx < x1 - 18), :3] = ink
    return save_png(out, 'sketch', img)


def save_png(out: Path, name: str, px: np.ndarray) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    h, w = px.shape[:2]
    img = bpy.data.images.new(name, w, h)
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = str(out / f'{name}.png')
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    return out / f'{name}.png'


# --- architecture --------------------------------------------------------------

def shell(m) -> None:
    w, cx = X1 - X0, (X0 + X1) / 2
    A.box('floor_slab', (w, WALL_Y + WALL_T - Y0, 0.3), (cx, (Y0 + WALL_Y + WALL_T) / 2, -0.3), m['cap'], bevel=0.01,
          drop=('+z', '+y'))
    A.box('floor', (w, WALL_Y - Y0, 0.004), (cx, (Y0 + WALL_Y) / 2, 0.0), m['floor'], bevel=0, tile=TILE)
    A.box('wall_back', (w, WALL_T, WALL_H), (cx, WALL_Y + WALL_T / 2, 0), m['wall'], bevel=0.01, tile=1.5,
          drop=('+y', '+z'))
    # the thick cut edge on top of the wall, and a heavier block capping each pilaster, as in l2
    A.box('wall_cap', (w + 0.04, WALL_T + 0.2, CAP_H), (cx, WALL_Y + WALL_T / 2 - 0.1, WALL_H), m['cap'],
          bevel=0.02, drop=('+y',))
    for i, x in enumerate(PILASTERS):
        # deep, heavy columns: l2's pilasters stand well proud of the wall
        A.box(f'pilaster_{i}', (0.6, 0.45, WALL_H), (x, WALL_Y - 0.225, 0), m['pilaster'], bevel=0.015, drop=('+z',))
        A.box(f'pilaster_cap_{i}', (0.7, 0.55, CAP_H + 0.06), (x, WALL_Y - 0.225, WALL_H), m['cap'], bevel=0.02)
    A.box('skirting', (w, 0.015, 0.08), (cx, WALL_Y - 0.0075, 0), m['pilaster'], bevel=0.003)


def lift(m) -> None:
    x = 0.95
    A.box('lift_surround', (1.7, 0.1, 2.45), (x, WALL_Y - 0.05, 0), m['pilaster'], bevel=0.012)
    A.box('lift_recess', (1.5, 0.02, 2.3), (x, WALL_Y - 0.105, 0), m['frame'], bevel=0.004)
    for i, dx in enumerate((-0.36, 0.36)):
        A.box(f'lift_door_{i}', (0.7, 0.04, 2.25), (x + dx, WALL_Y - 0.13, 0), m['lift'], bevel=0.004)
    A.box('lift_panel', (0.14, 0.03, 0.42), (-0.25, WALL_Y - 0.015, 1.05), m['lift'], bevel=0.006)
    for i in range(3):
        A.cylinder(f'lift_indicator_{i}', 0.03, 0.012, (-0.25, WALL_Y - 0.03, 1.33 - i * 0.11), m['indicator'],
                   kind='dynamic', rot=(math.pi / 2, 0, 0))


def plan_wall(m) -> None:
    c, r, pitch = PLAN['cols'], PLAN['rows'], PLAN['pitch']
    w, h = c * pitch + 0.14, r * pitch + 0.14
    x0, z0, y = PLAN['x0'], PLAN['z0'], WALL_Y
    # a thick light-grey bezel, as in l2, rather than dark steel
    A.box('plan_frame', (w + 0.08, 0.1, h + 0.08), (x0 + w / 2, y - 0.05, z0 - 0.04), m['bezel'], bevel=0.02)
    A.box('plan_back', (w - 0.1, 0.02, h - 0.1), (x0 + w / 2, y - 0.1, z0 + 0.05), m['frame'], bevel=0.004)
    for row in range(r):
        for col in range(c):
            A.box(f'plan_tile_r{row}_c{col}', (pitch - 0.04, 0.035, pitch - 0.04),
                  (x0 + 0.07 + (col + 0.5) * pitch, y - 0.13, z0 + 0.07 + row * pitch + 0.02),
                  m['tile'], kind='dynamic', bevel=0.012)
    # criteria lights: a row of round lamps on the wall above the plan wall; each is its own warm group
    for i in range(5):
        x = x0 + 0.45 + i * 0.5
        z = z0 + h + 0.3
        A.cylinder(f'criteria_housing_{i}', 0.13, 0.06, (x, y, z), m['frame'], bevel=0.012, rot=(math.pi / 2, 0, 0))
        bulb = A.cylinder(f'criteria_light_{i}', 0.105, 0.01, (x, y - 0.06, z), m['bulb'], kind='dynamic',
                          rot=(math.pi / 2, 0, 0))
        bulb['warm'] = f'criteria{i}'
        spot = A.light(f'criteria_spot_{i}', 'SPOT', (x, y - 0.3, z + 0.02), 18, '#ffc98a', warm=f'criteria{i}',
                       spot_size=math.radians(80), spot_blend=0.9, shadow_soft_size=0.06)
        A.aim(spot, (x, y, z - 0.9))
        A.light(f'criteria_glow_{i}', 'POINT', (x, y - 0.14, z), 4, '#ffcf8a', warm=f'criteria{i}',
                shadow_soft_size=0.08)


def poster(m) -> None:
    x, z = 9.25, 1.05
    A.panel('poster', 0.72, 0.96, (x, WALL_Y - 0.004, z), m['poster'])
    for i, (dx, dz) in enumerate(((-0.32, 0.9), (0.32, 0.9), (-0.32, 0.06), (0.32, 0.06))):
        A.cylinder(f'poster_pin_{i}', 0.012, 0.01, (x + dx, WALL_Y - 0.005, z + dz), m['brass'], segments=10,
                   rot=(math.pi / 2, 0, 0))


def shelf(m, props: dict) -> None:
    """Shelf in the recess right of the last pilaster: binders, a box and a plant."""
    x0, y, w = 10.45, WALL_Y - 0.22, 1.5
    for side in (0, 1):
        A.box(f'shelf_side_{side}', (0.03, 0.4, 1.9), (x0 + side * w, y, 0), m['steel'], bevel=0.004)
    for k, z in enumerate((0.06, 0.66, 1.26, 1.86)):
        A.box(f'shelf_board_{k}', (w, 0.4, 0.025), (x0 + w / 2, y, z), m['steel'], bevel=0.004)
    A.box('shelf_back', (w, 0.02, 1.9), (x0 + w / 2, y + 0.19, 0), m['steel'], bevel=0.003)
    spines = ('#8a9bb4', '#5f6f86', '#b9b2a6', '#7f8a78', '#9a8f86', '#5f6f86')  # muted, like l2's shelf
    for k in range(6):
        A.tint(A.duplicate(props['binder'], f'shelf_binder_{k}', (x0 + 0.12 + k * 0.045, y, 0.685)), spines[k])
    for k in range(4):
        A.tint(A.duplicate(props['binder'], f'shelf_binder_hi_{k}', (x0 + 0.9 + k * 0.045, y, 1.285)),
               spines[(k + 2) % 6])
    A.duplicate(props['box'], 'shelf_box', (x0 + 0.95, y, 0.685))
    potted(m, props, 'shelf_plant', x0 + 0.35, y, 1.885, 0.7, pot=0.16)


# --- furniture -------------------------------------------------------------------

def chair(m, name, x, y, facing: float) -> None:
    """Task chair centred at (x, y), its seat front facing angle `facing` (0 faces -Y)."""
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
    A.box(f'{name}_back', (0.46, 0.06, 0.5), (x - sx * 0.24, y - sy * 0.24, SEAT_H + 0.08), m['black'],
          rot=(math.radians(-8), 0, rz), bevel=0.025)
    for side in (-1, 1):
        ax, ay = math.cos(rz) * side * 0.26, math.sin(rz) * side * 0.26
        A.box(f'{name}_arm_{side:+d}', (0.05, 0.3, 0.035), (x + ax, y + ay, SEAT_H + 0.18), m['black'],
              rot=(0, 0, rz), bevel=0.012)
        A.box(f'{name}_armpost_{side:+d}', (0.03, 0.04, 0.18), (x + ax, y + ay, SEAT_H), m['frame'], rot=(0, 0, rz),
              bevel=0.006)


def desk_lamp(m, name, group, x, y, flip: int) -> None:
    """Arm lamp standing at (x, y); its head reaches towards -Y*flip. Bulb and light belong to `group`."""
    A.cylinder(f'{name}_base', 0.07, 0.02, (x, y, DESK_H), m['frame'], bevel=0.005)
    A.box(f'{name}_arm1', (0.016, 0.016, 0.36), (x, y, DESK_H + 0.02), m['frame'],
          rot=(math.radians(12) * flip, 0, 0), bevel=0.004)
    hx, hy, hz = x, y - 0.2 * flip, DESK_H + 0.42
    A.box(f'{name}_arm2', (0.016, 0.24, 0.016), (x, y - 0.1 * flip, DESK_H + 0.36), m['frame'],
          rot=(math.radians(18) * flip, 0, 0), bevel=0.004)
    A.cylinder(f'{name}_shade', 0.035, 0.12, (hx, hy, hz - 0.08), m['frame'], radius2=0.075, segments=20, bevel=0.004,
               rot=(0, math.pi, 0))
    bulb = A.cylinder(f'{name}_bulb', 0.03, 0.004, (hx, hy, hz - 0.205), m['bulb'], kind='dynamic', segments=16)
    bulb['warm'] = group
    # just below the shade's mouth: the shade is a closed solid and would swallow the light
    spot = A.light(f'{name}_light', 'SPOT', (hx, hy, hz - 0.215), 1.5, '#ff9c45', warm=group,
                   spot_size=math.radians(140), spot_blend=1.0, shadow_soft_size=0.06)
    A.aim(spot, (hx, hy - 0.1 * flip, DESK_H))
    # the broad warm wash over the desk that l2 shows around a working lamp (a hot spot alone reads as glare)
    A.light(f'{name}_wash', 'AREA', (x + 0.6, y - 0.25 * flip, DESK_H + 0.9), 12, '#ff9a40', warm=group,
            shape='DISK', size=1.1)


def pen_pot(m, name, x, y, rng) -> None:
    A.cylinder(f'{name}', 0.038, 0.1, (x, y, DESK_H), m['mug'], bevel=0.004)
    for k in range(6):
        a = k * 2 * math.pi / 6
        A.cylinder(f'{name}_pencil_{k}', 0.004, 0.16, (x + 0.018 * math.cos(a), y + 0.018 * math.sin(a), DESK_H + 0.02),
                   (m['pencil'], m['ink'], m['brass'])[k % 3], segments=6,
                   rot=(rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), 0))


def potted(m, props, name, x, y, z, rz, plant='desk_plant', pot=0.13) -> None:
    """A small plant in its own square pot."""
    A.box(f'{name}_pot', (pot, pot, pot * 0.8), (x, y, z), m['pot'], bevel=0.008)
    A.duplicate(props[plant], name, (x, y, z + pot * 0.72), rz)


def sketch(m, name, x, y, rz) -> None:
    """A sheet with a drawn plan, lying on the desk."""
    A.panel(name, 0.21, 0.297, (x, y, DESK_H + 0.0015), m['sketch'], rot=(-math.pi / 2, 0, rz))


def papers(m, name, x, y, rng, n) -> None:
    for k in range(n):
        A.box(f'{name}_{k}', (0.21, 0.297, 0.002), (x + rng.uniform(-0.12, 0.12), y + rng.uniform(-0.06, 0.06),
                                                  DESK_H + 0.001 * (k + 1)),
              m['paper'], bevel=0, rot=(0, 0, rng.uniform(-0.5, 0.5)), tile=None)


def report_tray(m, name, x, y) -> None:
    """Two stacked letter trays on posts, with paper in each."""
    for level in range(2):
        z = DESK_H + 0.005 + level * 0.075
        A.box(f'{name}_base_{level}', (0.26, 0.33, 0.008), (x, y, z), m['frame'], bevel=0.002)
        for side in (-1, 1):
            A.box(f'{name}_rail_{level}_{side:+d}', (0.008, 0.33, 0.04), (x + side * 0.126, y, z), m['frame'], bevel=0.002)
        A.box(f'{name}_back_{level}', (0.26, 0.008, 0.05), (x, y + 0.161, z), m['frame'], bevel=0.002)
        A.box(f'{name}_paper_{level}', (0.21, 0.29, 0.012), (x, y - 0.01, z + 0.008), m['paper'], bevel=0.001, tile=None)
    for sx in (-1, 1):
        for sy in (-1, 1):
            A.cylinder(f'{name}_post_{sx:+d}{sy:+d}', 0.005, 0.08, (x + sx * 0.12, y + sy * 0.15, DESK_H + 0.005),
                       m['frame'], segments=8)


def book_stack(m, name, x, y, rng, n=2) -> None:
    for k in range(n):
        A.box(f'{name}_{k}', (0.24, 0.17, 0.028), (x, y, DESK_H + k * 0.028),
              A.material(f'book_{k}', ['#5d7792', '#3f5a6e', '#c9b99a'][k % 3], rough=0.7), bevel=0.003,
              rot=(0, 0, rng.uniform(-0.15, 0.15)))


def laptop(m, name, x, y, facing: float, closed=False) -> None:
    """Laptop at (x, y); open ones face `facing` (0 = user sits at -Y)."""
    A.box(f'{name}_base', (0.34, 0.24, 0.016), (x, y, DESK_H), m['steel'], bevel=0.004, rot=(0, 0, facing))
    if closed:
        A.box(f'{name}_lid', (0.34, 0.24, 0.008), (x, y, DESK_H + 0.016), m['steel'], bevel=0.003, rot=(0, 0, facing))
        return
    dx, dy = -math.sin(facing) * 0.12, math.cos(facing) * 0.12
    A.box(f'{name}_lid', (0.34, 0.012, 0.23), (x + dx, y + dy, DESK_H + 0.012), m['steel'], bevel=0.004,
          rot=(math.radians(-18), 0, facing))


def monitor(m, name, x, y, facing: float) -> None:
    A.box(f'{name}_stand', (0.22, 0.16, 0.012), (x, y, DESK_H), m['frame'], rot=(0, 0, facing), bevel=0.003)
    A.box(f'{name}_neck', (0.04, 0.03, 0.2), (x, y, DESK_H), m['frame'], rot=(0, 0, facing))
    A.box(f'{name}_screen', (0.58, 0.035, 0.36), (x, y, DESK_H + 0.14), m['screen'], bevel=0.008,
          rot=(math.radians(-5), 0, facing))


def desk_frame(m, name, x0, y) -> None:
    cx = x0 + DESK_W / 2
    A.box(f'{name}_top', (DESK_W - 0.006, DESK_D, TOP_T), (cx, y, DESK_H - TOP_T), m['oak'], bevel=0.008, tile=1.2)
    for lx in (x0 + 0.05, x0 + DESK_W - 0.05):
        for ly in (y - DESK_D / 2 + 0.05, y + DESK_D / 2 - 0.05):
            A.box(f'{name}_leg_{lx:.2f}_{ly:.2f}', (0.045, 0.045, DESK_H - TOP_T), (lx, ly, 0), m['frame'], bevel=0.006)
    A.box(f'{name}_rail', (DESK_W - 0.1, 0.03, 0.06), (cx, y, DESK_H - TOP_T - 0.06), m['frame'], bevel=0.005)


def pedestal(m, name, x, y) -> None:
    A.box(name, (0.42, 0.55, 0.6), (x, y, 0.02), m['steel'], bevel=0.014)
    for d in range(3):
        A.box(f'{name}_drawer_{d}', (0.37, 0.008, 0.17), (x, y - 0.277, 0.06 + d * 0.19), m['steel'], bevel=0.003)
        A.box(f'{name}_handle_{d}', (0.12, 0.012, 0.012), (x, y - 0.285, 0.19 + d * 0.19), m['frame'], bevel=0.002)


def bench(m, rng: random.Random, props: dict) -> None:
    """Three desks; far side (robots) faces the room, near side has the empty chairs and pedestals."""
    far, near = BENCH_Y + 0.12, BENCH_Y - 0.15
    for i in range(3):
        x0 = BENCH_X0 + i * DESK_W
        cx, name = x0 + DESK_W / 2, f'desk{i + 1}'
        desk_frame(m, name, x0, BENCH_Y)
        pedestal(m, f'{name}_pedestal', x0 + DESK_W - 0.3, BENCH_Y - DESK_D / 2 + 0.29)
        seat_x, seat_y = cx - 0.25, BENCH_Y + DESK_D / 2 + 0.34
        # far-side chairs face the desk (-Y), near-side ones face the wall (+Y)
        chair(m, f'{name}_chair_far', seat_x, seat_y, 0.0)
        # the robot sits at the front of its chair, 0.14 m from the desk edge: its arms are short
        A.empty(f'seat_{name}', (seat_x, seat_y - 0.2, SEAT_H), (0, 0, 0), desk=name)
        chair(m, f'{name}_chair_near', cx - 0.3, BENCH_Y - DESK_D / 2 - 0.42, math.pi)
        desk_lamp(m, f'{name}_lamp', name, x0 + 0.18, far + 0.1, 1)
        # the warm spill on the floor in front of a working desk (l2's floor there samples ~#fce5d6)
        # low and in front of the bench, so it lights the floor and not the desk tops
        A.light(f'{name}_spill', 'AREA', (cx, BENCH_Y - DESK_D / 2 - 0.9, 0.55), 10, '#ff9a55', warm=name,
                shape='DISK', size=1.0)
        pen_pot(m, f'{name}_penpot', cx + 0.38, far - 0.02, rng)
    # desk 1 (teal, searching): open laptop, a potted plant, books, a sheet of notes
    laptop(m, 'desk1_laptop', BENCH_X0 + 0.85, far - 0.02, math.pi)
    papers(m, 'desk1_papers', BENCH_X0 + 0.55, near - 0.05, rng, 2)
    potted(m, props, 'desk1_plant', BENCH_X0 + 0.42, far - 0.12, DESK_H, 1.1)
    book_stack(m, 'desk1_books', BENCH_X0 + 1.25, near - 0.02, rng, 3)
    sketch(m, 'desk1_sketch', BENCH_X0 + 1.05, near - 0.12, 0.2)
    # desk 2 (blue, writing): plan sheets spread out in front of it, notes, a mug, a plant
    for k, (dx, rz) in enumerate(((0.45, -0.1), (0.72, 0.15), (1.0, -0.25))):
        sketch(m, f'desk2_sketch_{k}', BENCH_X0 + DESK_W + dx, BENCH_Y + 0.02 - 0.05 * k, rz)
    papers(m, 'desk2_notes', BENCH_X0 + DESK_W + 0.35, near - 0.1, rng, 2)
    A.cylinder('desk2_mug', 0.04, 0.095, (BENCH_X0 + DESK_W + 1.3, near, DESK_H), m['mug'], bevel=0.004)
    potted(m, props, 'desk2_plant', BENCH_X0 + DESK_W + 1.1, far - 0.08, DESK_H, 2.3)
    # desk 3 (olive, testing): monitor to the robot's right (screen left) so the robot and its flask
    # stay in view as in l2, closed laptop, report tray, a sketch
    monitor(m, 'desk3_monitor', BENCH_X0 + 2 * DESK_W - 0.05, far - 0.1, math.radians(-35))
    laptop(m, 'desk3_laptop_closed', BENCH_X0 + 2 * DESK_W + 0.55, near - 0.05, 0.3, closed=True)
    report_tray(m, 'desk3_tray', BENCH_X0 + 3 * DESK_W - 0.2, near - 0.02)
    sketch(m, 'desk3_sketch', BENCH_X0 + 2 * DESK_W + 1.0, near - 0.1, -0.2)
    A.cylinder('desk3_mug', 0.04, 0.095, (BENCH_X0 + 2 * DESK_W + 0.3, near - 0.05, DESK_H), m['mug'], bevel=0.004)
    potted(m, props, 'desk3_plant', BENCH_X0 + 2 * DESK_W + 0.75, far - 0.05, DESK_H, 0.6)


def corner_desk(m, rng: random.Random, props: dict) -> None:
    """The desk cut by the frame's near-left corner in l2: monitor, keyboard, lamp and a plant."""
    x0, y = 0.3, 0.9  # far enough into the near-left corner that only a corner shows, as in l2
    desk_frame(m, 'corner', x0, y)
    desk_frame(m, 'corner_b', x0 + DESK_W, y)
    monitor(m, 'corner_monitor', x0 + 0.9, y + 0.15, 0.0)
    A.box('corner_keyboard', (0.44, 0.14, 0.018), (x0 + 0.9, y - 0.15, DESK_H), m['frame'], bevel=0.004)
    desk_lamp(m, 'corner_lamp', 'corner', x0 + 0.25, y + 0.2, 1)
    papers(m, 'corner_papers', x0 + 0.35, y - 0.12, rng, 2)
    sketch(m, 'corner_sketch', x0 + 1.35, y - 0.1, 0.3)
    potted(m, props, 'corner_plant', x0 + DESK_W + 0.4, y + 0.1, DESK_H, 0.4)
    pen_pot(m, 'corner_penpot', x0 + 1.4, y + 0.2, rng)
    A.box('corner_box', (0.3, 0.22, 0.12), (x0 + DESK_W + 0.9, y + 0.05, DESK_H), m['cap'], bevel=0.01)


def question_desk(m, props: dict) -> None:
    x, y = 3.6, WALL_Y - 0.45
    A.box('qdesk_body', (1.1, 0.6, 0.7), (x, y, 0.02), m['steel'], bevel=0.012)
    A.box('qdesk_drawer', (0.5, 0.008, 0.16), (x - 0.18, y - 0.302, 0.5), m['steel'], bevel=0.003)
    A.box('qdesk_top', (1.2, 0.66, 0.035), (x, y, 0.72), m['oak'], bevel=0.008, tile=1.2)
    for sx in (-1, 1):
        for sy in (-1, 1):
            A.cylinder(f'qdesk_foot_{sx:+d}{sy:+d}', 0.015, 0.02, (x + sx * 0.5, y + sy * 0.25, 0), m['frame'], segments=8)
    # the "?" tent card: a card leaning back 15 degrees, the glyph standing just proud of its face
    cx, cy, top, lean = x - 0.15, y - 0.02, 0.755, math.radians(15)
    A.box('qdesk_card', (0.22, 0.004, 0.17), (cx, cy, top), m['paper'], bevel=0, rot=(-lean, 0, 0), tile=None)
    A.text_mesh('qdesk_card_glyph', '?', 0.13, (cx, cy + 0.085 * math.sin(lean) - 0.004, top + 0.085 * math.cos(lean)),
                m['ink'], rot=(math.pi / 2 - lean, 0, 0))
    potted(m, props, 'qdesk_plant', x + 0.38, y + 0.08, 0.755, 0.3, pot=0.15)
    # the lantern hangs over the desk from the cap; it is dynamic and driven by attention state
    A.cylinder('lantern_cord', 0.005, 0.95, (cx, y, WALL_H - 0.95), m['frame'], kind='dynamic', segments=6)
    # an elongated six-sided diamond, widest a little above its middle, like l2's lantern
    A.octahedron('lantern', 0.17, 0.6, (cx, y, WALL_H - 1.25), m['lantern'], sides=6, waist=0.12)
    A.empty('lantern_anchor', (cx, y, WALL_H), attention='question')


def planters(m, props: dict) -> None:
    """Square concrete planters with tall leafy plants, as l2 has by each pilaster and at the bench's end."""
    spots = ((-0.55, WALL_Y - 0.45, 0.0), (8.45, WALL_Y - 0.85, 1.3), (10.05, WALL_Y - 0.85, 2.6),
             (12.1, 3.9, 3.9), (-0.5, 2.6, 5.2), (10.0, 3.9, 0.8))
    for i, (x, y, rz) in enumerate(spots):
        A.box(f'planter_{i}', (0.42, 0.42, 0.5), (x, y, 0), m['pot'], bevel=0.02)
        plant = props['calathea'] if i == 4 else props['tall']
        A.duplicate(plant, f'planter_{i}_plant', (x, y, 0.48), rz)


def footprints(m) -> None:
    """Anchors along the path from the lift to the bench; the runtime lays footprints on them."""
    for i in range(8):
        t = i / 7
        A.empty(f'footprint_{i}', (0.9 + t * 3.0, WALL_Y - 0.8 - t * 1.4, 0.005), (0, 0, math.radians(-60)), foot=i % 2)


def lights() -> None:
    A.world_hdri(A.source('polyhaven', 'white_studio_06', 'white_studio_06_1k.hdr'), strength=0.7,
                 rotation=math.radians(120))
    # daylight is cool and neutral; the warm light in the room comes from lamps and wall washers only
    sun = A.light('sun', 'SUN', (0, 0, 10), 1.6, '#fbf8f4', angle=math.radians(10))
    sun.rotation_euler = (math.radians(48), 0, math.radians(-30))
    for i, x in enumerate((1.5, 5.0, 8.5, 12.0)):
        A.light(f'fill_{i}', 'AREA', (x, 2.5, WALL_H + 0.4), 90, '#e6ecf6', shape='RECTANGLE', size=3.0, size_y=6.0)
    # warm downlights grazing each wall bay: the scallops of light on the wall in l2
    for i, x in enumerate((0.95, 3.4, 5.8, 7.4, 9.25, 11.2)):
        spot = A.light(f'wallwash_{i}', 'SPOT', (x, WALL_Y - 0.28, WALL_H - 0.08), 60, '#ffbe7a',
                       spot_size=math.radians(65), spot_blend=1.0, shadow_soft_size=0.05)
        A.aim(spot, (x, WALL_Y, 1.1))


def props() -> dict:
    ph = lambda i: A.source('polyhaven', i, f'{i}_1k.gltf')  # noqa: E731
    return {'tall': A.import_gltf(ph('anthurium_botany_01'), 'anthurium_botany_01_a', 'proto_tall', scale=0.75),
            'calathea': A.import_gltf(ph('calathea_orbifolia_01'), 'calathea_orbifolia_01_a', 'proto_calathea'),
            'desk_plant': A.import_gltf(ph('anthurium_botany_01'), 'anthurium_botany_05_e', 'proto_desk_plant', scale=0.9),
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
    lift(m)
    question_desk(m, p)
    plan_wall(m)
    poster(m)
    shelf(m, p)
    bench(m, rng, p)
    corner_desk(m, rng, p)
    planters(m, p)
    footprints(m)
    for proto in p.values():
        bpy.data.objects.remove(proto)
    lights()
    A.ortho_camera('camera_l2', (6.0, 3.4, 0.9), pitch_deg=30, yaw_deg=20, scale=9.0)
    info = A.lightmap_uvs(texel=0.015)
    info['warm_groups'] = sorted({o['warm'] for o in bpy.data.objects if 'warm' in o})
    info['seat_height'] = SEAT_H
    A.save_blend(SCENE, info)
    print('BUILD', SCENE, info)


if __name__ == '__main__':  # importable by art/scripts/bakeoff.py
    main()
