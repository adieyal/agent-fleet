"""The floor kit's Blender pieces: architecture, the lift bay, the plan wall and its tile states.

    blender -b --factory-startup -P art/scripts/build_kit.py -- OUT_DIR

Each piece is modelled at real size and rendered in Cycles with the sprite world's camera (orthographic, pitch
28 deg, yaw 33 deg: the image model's, so AI furniture matches) at 85.75, 171.5 and 343 px/m, on transparent film with shadow catchers for its contact
shadows. OUT_DIR gets <piece>@<ppm>.png and pieces.json (per piece: tiers with size and the pixel its anchor
lands on, footprint, slots). art/kit/finish.py turns them into the kit. Only Blender 4.2+ and the GPU are
needed: no downloaded sources.

World coordinates (docs/design/sprite-world.md): x along the back wall, y towards it, z up, metres. The back
wall's room-side face is the plane y = 0 for wall pieces; the floor is z = 0.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402
import bake as B  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

# The world camera: the image model's own (measured from the AI furniture's silhouettes, and l1's and l2's), so
# rendered architecture and generated furniture share one projection (floor review 1, point 1)
PITCH, YAW = 28.0, 33.0
PPM_1X = 941 / 5.486            # l2's framing: 171.528 px/m
TIERS = (0.5, 1, 2)
WALL_H, WALL_T = 3.2, 0.45      # back and left walls: l1's thick cut-away walls
BAY = 3.6                       # structural bay: pilaster spacing, and the length of repeating pieces
SLAB, RIM = 0.9, 0.14           # floor slab thickness shown at the cut edges, and its pale rim (l1's plinth)
TILE, PITCH_T, COLS, ROWS = 0.27, 0.3, 10, 6   # plan-wall tiles: l2's 10 x 6 grid, sized to its bench

PAL = {  # docs/design/art-direction.md, rendered targets; albedo a little lower
    # wall and slab: albedo that renders near the wall texture's mean (#c7c2bf), so wall ends meet the texture
    'wall': '#a39e9b', 'cap': '#dcd2c8', 'pilaster': '#a39897', 'cut': '#b9b0ab', 'slab': '#9d989a',
    'steel': '#6f6d74', 'door': '#75737a', 'frame': '#8d8a90', 'dark': '#1d1f24', 'bezel': '#d8d6da',
    'backing': '#9e9aa0', 'tile': '#e2dfe2', 'ink': '#55535a', 'fail': '#a4473f', 'amber': '#fcb957',
    'lamp_off': '#4a4a50', 'lamp_on': '#fed9a1', 'car': '#3a3a40',
}


# --- scene ----------------------------------------------------------------------------------

def axes():
    p, y = math.radians(PITCH), math.radians(YAW)
    back = Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))
    right = (-back).cross(Vector((0, 0, 1))).normalized()
    up = right.cross(-back).normalized()
    return right, up, back


def studio() -> None:
    """Soft high-key daylight: a pale sky and a large soft key from the upper left, as l2."""
    s = bpy.context.scene
    world = bpy.data.worlds.new('sky')
    world.use_nodes = True
    bg = world.node_tree.nodes['Background']
    bg.inputs['Color'].default_value = A.srgb('#d7ecfd')
    bg.inputs['Strength'].default_value = 0.9
    s.world = world
    right, up, back = axes()
    key = A.light('key', 'AREA', Vector((-6, -5, 9)), 1500, '#fff6ec', shape='DISK', size=5.0)
    A.aim(key, Vector((0, 0, 0.5)))
    B.gpu()
    B.setup(s)
    s.cycles.samples = 160
    s.cycles.use_denoising = True
    s.view_settings.view_transform = 'Standard'
    s.view_settings.look = 'None'
    s.render.film_transparent = True
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'


def clear() -> None:
    for o in list(bpy.data.objects):
        # (a piece's own lights go too: the alcove's lamp; the studio's key stays)
        if o.type == 'MESH' or o.name.startswith('catcher') or o.name == 'kit_cam' or (o.type == 'LIGHT' and o.name != 'key'):
            bpy.data.objects.remove(o)


def catcher(name, loc, rot, size=30) -> None:
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size / 2)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location, ob.rotation_euler = loc, rot
    ob.is_shadow_catcher = True


def floor_catcher(z=0.0):
    catcher('catcher_floor', (0, 0, z), (0, 0, 0))


def wall_catcher(y=0.0):
    catcher('catcher_wall', (0, y + 0.001, 0), (math.pi / 2, 0, 0))


def M(key, **kw):
    return A.material(f'kit_{key}_{kw.get("rough", 0.6)}_{kw.get("metal", 0.0)}', PAL[key], **kw)


def emissive(key, strength):
    m = A.material('kit_' + key + '_glow', PAL[key])
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Emission Color'].default_value = A.srgb(PAL[key])
    b.inputs['Emission Strength'].default_value = strength
    return m


# --- rendering --------------------------------------------------------------------------------

def points(objs) -> list:
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for o in objs:
        e = o.evaluated_get(dg)
        out += [e.matrix_world @ v.co for v in e.data.vertices]
    return out


def render(name: str, out: Path, anchor: Vector, extra: list, margin: float, frames=None) -> list:
    """Render the scene's meshes at every tier; `frames` is a list of callables that pose frame i (a sheet)."""
    objs = [o for o in bpy.data.objects if o.type == 'MESH' and not o.is_shadow_catcher and not o.hide_render]
    pts = points(objs) + extra
    right, up, back = axes()
    us, vs = [p.dot(right) for p in pts], [p.dot(up) for p in pts]
    tiers = []
    for mult in TIERS:
        ppm = PPM_1X * mult
        u0, v1 = min(us) - margin, max(vs) + margin
        w, h = math.ceil((max(us) + margin - u0) * ppm), math.ceil((v1 - min(vs) + margin) * ppm)
        u1, v0 = u0 + w / ppm, v1 - h / ppm
        cam = bpy.data.cameras.get('kit_cam') or bpy.data.cameras.new('kit_cam')
        cam.type, cam.clip_end, cam.ortho_scale = 'ORTHO', 200, max(u1 - u0, v1 - v0)
        ob = bpy.data.objects.get('kit_cam') or bpy.data.objects.new('kit_cam', cam)
        if ob.name not in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.link(ob)
        centre = right * (u0 + u1) / 2 + up * (v0 + v1) / 2
        ob.location = centre + back * 60
        A.aim(ob, centre)
        s = bpy.context.scene
        s.camera = ob
        s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = w, h, 100
        paths = []
        for i, pose in enumerate(frames or [None]):
            if pose:
                pose()
            p = out / (f'{name}@{ppm:g}.png' if not frames else f'{name}@{ppm:g}.f{i}.png')
            s.render.filepath = str(p)
            bpy.ops.render.render(write_still=True)
            soften(p)
            paths.append(p)
        file = paths[0] if not frames else sheet(paths, out / f'{name}@{ppm:g}.png')
        tiers.append({'ppm': round(ppm, 3), 'file': file.name, 'size': [w, h],
                      'anchor_px': [round((anchor.dot(right) - u0) * ppm, 2), round((v1 - anchor.dot(up)) * ppm, 2)],
                      **({'frames': len(frames)} if frames else {})})
    return tiers


def soften(path: Path) -> None:
    """Shadow-only pixels (black, part transparent) lose the catcher's faint wide occlusion, so the sprite's
    edge doesn't cut a grey field (as the bake-off's B1)."""
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    shadow = (px[:, 3] < 0.98) & (px[:, :3].max(1) < 0.02)
    px[shadow, 3] = np.clip((px[shadow, 3] - 0.1) / 0.9, 0, 1)
    img.pixels.foreach_set(px.ravel())
    img.save()
    bpy.data.images.remove(img)


def sheet(paths: list[Path], dest: Path) -> Path:
    imgs = [bpy.data.images.load(str(p)) for p in paths]
    w, h = imgs[0].size
    arr = np.zeros((h, w * len(imgs), 4), dtype=np.float32)
    for i, im in enumerate(imgs):
        px = np.empty(w * h * 4, dtype=np.float32)
        im.pixels.foreach_get(px)
        arr[:, i * w:(i + 1) * w] = px.reshape(h, w, 4)
    out = bpy.data.images.new(dest.stem, w * len(imgs), h, alpha=True)
    out.colorspace_settings.name = imgs[0].colorspace_settings.name
    out.pixels.foreach_set(arr.ravel())
    out.filepath_raw, out.file_format = str(dest), 'PNG'
    out.save()
    for im in imgs:
        bpy.data.images.remove(im)
    for p in paths:
        p.unlink()
    return dest


# --- pieces -----------------------------------------------------------------------------------
# Each returns (anchor world point, footprint box relative to the anchor, slots, extra framing points, margin).

def pilaster():
    """A pilaster on the back wall's face, with its capping block; anchor: base centre on the wall face."""
    A.box('pilaster', (0.45, 0.18, WALL_H - 0.12), (0, -0.09, 0), M('pilaster', rough=0.7), bevel=0.012)
    A.box('pilaster_cap', (0.53, 0.24, 0.12), (0, -0.12, WALL_H - 0.12), M('cap', rough=0.6), bevel=0.01)
    floor_catcher(); wall_catcher()
    return Vector((0, 0, 0)), [-0.27, -0.24, 0, 0.27, 0, WALL_H], {}, [], 0.35


def cap_x():
    """The back wall's top for one bay: cap face and its front lip, repeatable along x; anchor: the bay's
    left end on the wall face at floor level."""
    # (no end faces: bays butt into one continuous cap)
    A.box('cap', (BAY, WALL_T + 0.04, 0.1), (BAY / 2, WALL_T / 2 - 0.02, WALL_H - 0.02), M('cap', rough=0.6), bevel=0.0, drop=('-x', '+x'))
    return Vector((0, 0, 0)), [0, -0.02, WALL_H - 0.02, BAY, WALL_T + 0.02, WALL_H + 0.08], {}, [], 0.02


def cap_y():
    """The left wall's top for one bay along y (the wall's room face is x = 0); anchor: its front end at the
    face, floor level."""
    A.box('cap', (WALL_T + 0.04, BAY, 0.1), (-WALL_T / 2 + 0.02, BAY / 2, WALL_H - 0.02), M('cap', rough=0.6), bevel=0.0, drop=('-y', '+y'))
    return Vector((0, 0, 0)), [-WALL_T - 0.02, 0, WALL_H - 0.02, 0.02, BAY, WALL_H + 0.08], {}, [], 0.02


def corner():
    """Where the back and left walls meet: the corner post of the cap and a corner pilaster; anchor: the inner
    corner at floor level."""
    A.box('corner_cap', (WALL_T + 0.04, WALL_T + 0.04, 0.1), (-WALL_T / 2 + 0.02, WALL_T / 2 - 0.02, WALL_H - 0.02),
          M('cap', rough=0.6), bevel=0.0)
    A.box('corner_post', (0.2, 0.2, WALL_H - 0.02), (0.1, -0.1, 0), M('pilaster', rough=0.7), bevel=0.01)
    floor_catcher(); wall_catcher()
    return Vector((0, 0, 0)), [-WALL_T, -0.2, 0, 0.2, WALL_T, WALL_H + 0.08], {}, [], 0.3


def wall_end_back():
    """The back wall's cut-away right end: the last 30 cm of wall, its cut face and cap; anchor: the end of the
    wall's room face at floor level."""
    A.box('end', (0.3, WALL_T, WALL_H), (-0.15, WALL_T / 2, -SLAB), M('wall', rough=0.8), bevel=0.004)
    A.box('end_cap', (0.3, WALL_T + 0.04, 0.1), (-0.15, WALL_T / 2 - 0.02, WALL_H - 0.02), M('cap', rough=0.6), bevel=0.0)
    return Vector((0, 0, 0)), [-0.3, 0, -SLAB, 0, WALL_T, WALL_H + 0.08], {}, [], 0.05


def wall_end_left():
    """The left wall's cut-away front end, as wall_end_back; anchor: its front end on the room face."""
    A.box('end', (WALL_T, 0.3, WALL_H + SLAB), (-WALL_T / 2, 0.15, -SLAB), M('wall', rough=0.8), bevel=0.004)
    A.box('end_cap', (WALL_T + 0.04, 0.3, 0.1), (-WALL_T / 2 + 0.02, 0.15, WALL_H - 0.02), M('cap', rough=0.6), bevel=0.0)
    return Vector((0, 0, 0)), [-WALL_T, 0, -SLAB, 0, 0.3, WALL_H + 0.08], {}, [], 0.05


def slab_front():
    """The floor slab's cut edge along the front for one bay; anchor: the bay's left end at the floor's edge."""
    # only the cut face, a thin plate (no top shows over the floor texture), under a pale rim that stands out a
    # little, as l1's plinth: the floor reads as a solid slab
    A.box('slab', (BAY, 0.01, SLAB - RIM), (BAY / 2, 0.005, -SLAB), M('slab', rough=0.7), bevel=0.0, drop=('-x', '+x'))
    A.box('rim', (BAY, 0.06, RIM), (BAY / 2, -0.02, -RIM), M('cap', rough=0.5), bevel=0.0, drop=('-x', '+x'))
    catcher('catcher_slab', (0, 0.01, 0), (math.pi / 2, 0, 0))
    return Vector((0, 0, 0)), [0, -0.05, -SLAB, BAY, 0.01, 0], {}, [], 0.03


def slab_side():
    """The slab's cut edge along the right side for one bay (the floor ends at x = 0 here); anchor: the bay's
    front end. Its front end meets slab-front's right end, so the corner needs no piece of its own."""
    A.box('slab', (0.01, BAY, SLAB - RIM), (-0.005, BAY / 2, -SLAB), M('slab', rough=0.7), bevel=0.0, drop=('-y', '+y'))
    A.box('rim', (0.06, BAY, RIM), (0.02, BAY / 2, -RIM), M('cap', rough=0.5), bevel=0.0, drop=('-y', '+y'))
    catcher('catcher_slab', (-0.01, 0, 0), (0, math.pi / 2, 0))
    return Vector((0, 0, 0)), [-0.01, 0, -SLAB, 0.05, BAY, 0], {}, [], 0.03


ALCOVE_W, ALCOVE_D = 2.4, 1.4


def alcove():
    """l1's alcove in the back-left corner: a wall fin projecting from the back wall, a lintel across the opening
    (both with caps) and a pendant lamp inside, lit. The left wall and back wall close it. Anchor: the inner corner
    of the back and left walls, at floor level (the room's back-left corner)."""
    fin = WALL_T
    A.box('fin', (fin, ALCOVE_D, WALL_H), (ALCOVE_W + fin / 2, -ALCOVE_D / 2, 0), M('wall', rough=0.8), bevel=0.004)
    A.box('fin_cap', (fin + 0.04, ALCOVE_D + 0.02, 0.1), (ALCOVE_W + fin / 2, -ALCOVE_D / 2, WALL_H - 0.02), M('cap', rough=0.6), bevel=0.0)
    A.box('lintel', (ALCOVE_W, 0.3, 0.5), (ALCOVE_W / 2, -ALCOVE_D + 0.15, WALL_H - 0.5), M('wall', rough=0.8), bevel=0.004)
    A.box('lintel_cap', (ALCOVE_W, 0.34, 0.1), (ALCOVE_W / 2, -ALCOVE_D + 0.15, WALL_H - 0.02), M('cap', rough=0.6), bevel=0.0)
    lamp = (ALCOVE_W / 2, -ALCOVE_D + 0.55, 2.05)   # low and forward enough to show under the lintel
    A.cylinder('cord', 0.006, WALL_H - lamp[2] - 0.12, (lamp[0], lamp[1], lamp[2] + 0.12), M('dark', rough=0.5))
    A.cylinder('shade', 0.16, 0.14, (lamp[0], lamp[1], lamp[2]), M('dark', rough=0.4), radius2=0.05, segments=32)
    A.ball('bulb', 0.045, (lamp[0], lamp[1], lamp[2] + 0.01), emissive('lamp_on', 8.0), kind='baked')
    lit = bpy.data.collections.new('alcove_lit')   # the lamp lights the alcove's own walls, not the shadow catchers:
    for n in ('fin', 'fin_cap', 'lintel', 'lintel_cap', 'shade'):   # light on a catcher lands in the sprite as a pale patch
        lit.objects.link(bpy.data.objects[n])
    lamp_ob = A.light('alcove_lamp', 'POINT', (lamp[0], lamp[1], lamp[2] - 0.05), 60, '#ffd9a0', shadow_soft_size=0.05, use_shadow=False)
    lamp_ob.light_linking.receiver_collection = lit
    floor_catcher(); wall_catcher()
    slots = {'lamp': list(lamp), 'inside': [ALCOVE_W / 2, -ALCOVE_D / 2, 0]}
    # (a wide margin: the fin's floor shadow reaches well to its right, and must fade out inside the sprite)
    return Vector((0, 0, 0)), [0, -ALCOVE_D, 0, ALCOVE_W + fin, 0, WALL_H + 0.08], slots, [], 1.0


FLOORS = 6   # the building's default floor count (PRD): one button each


def lift_panel():
    """The lift's floor-button column beside its doors, as l1: a box column with a round button per floor, faces
    blank (the numbers, the current floor and attention are drawn over it at runtime). Anchor: its base centre on
    the wall face."""
    A.box('column', (0.5, 0.32, WALL_H - 0.1), (0, -0.16, 0), M('pilaster', rough=0.6), bevel=0.012)
    A.box('column_cap', (0.58, 0.38, 0.1), (0, -0.19, WALL_H - 0.1), M('cap', rough=0.6), bevel=0.01)
    buttons = []
    for i in range(FLOORS):
        z = 0.95 + i * 0.26
        A.cylinder(f'ring_{i}', 0.09, 0.03, (0, -0.32, z), M('steel', rough=0.4, metal=0.5), rot=(math.pi / 2, 0, 0), segments=32)
        A.cylinder(f'face_{i}', 0.07, 0.03, (0, -0.335, z), M('lamp_off', rough=0.3), rot=(math.pi / 2, 0, 0), segments=32)
        buttons.append([0, -0.365, round(z, 3)])
    floor_catcher(); wall_catcher()
    return Vector((0, 0, 0)), [-0.29, -0.38, 0, 0.29, 0, WALL_H + 0.08], {'buttons': buttons}, [], 0.3


LIFT_W, LIFT_H, LEAF = 2.3, 2.75, 0.55


def lift():
    """The lift bay on the back wall: surround, two sliding steel leaves over a dark car, a floor indicator
    above (its display left blank: the floor number is drawn at runtime) and a call-button plate. A sheet of
    five frames from shut to open. Anchor: the bay's centre on the wall face at floor level."""
    A.box('surround_l', (0.22, 0.12, LIFT_H), (-(LEAF * 2 + 0.22) / 2 - 0.0, -0.06, 0), M('frame', rough=0.4, metal=0.3))
    A.box('surround_r', (0.22, 0.12, LIFT_H), ((LEAF * 2 + 0.22) / 2, -0.06, 0), M('frame', rough=0.4, metal=0.3))
    A.box('surround_t', (LEAF * 2 + 0.66, 0.12, 0.3), (0, -0.06, LIFT_H), M('frame', rough=0.4, metal=0.3))
    A.box('car', (LEAF * 2, 0.05, LIFT_H - 0.02), (0, 0.02, 0), M('car', rough=0.8), bevel=0.0)
    left = A.box('leaf_l', (LEAF, 0.04, LIFT_H - 0.04), (-LEAF / 2, -0.03, 0.01), M('door', rough=0.35, metal=0.6), bevel=0.004)
    right_ = A.box('leaf_r', (LEAF, 0.04, LIFT_H - 0.04), (LEAF / 2, -0.03, 0.01), M('door', rough=0.35, metal=0.6), bevel=0.004)
    A.box('indicator', (0.5, 0.05, 0.2), (0, -0.14, LIFT_H + 0.08), M('steel', rough=0.4, metal=0.5), bevel=0.006)
    A.box('display', (0.36, 0.01, 0.12), (0, -0.165, LIFT_H + 0.12), M('dark', rough=0.2), bevel=0.0)
    A.box('call_plate', (0.14, 0.03, 0.34), (LEAF + 0.42, -0.015, 1.0), M('steel', rough=0.4, metal=0.5), bevel=0.006)
    for i, z in enumerate((1.08, 1.22)):
        A.cylinder(f'call_{i}', 0.03, 0.02, (LEAF + 0.42, -0.03, z), M('bezel', rough=0.3), rot=(math.pi / 2, 0, 0))
    floor_catcher(); wall_catcher()

    def opener(k):
        def pose():
            left.location.x = -LEAF / 2 - k * LEAF * 0.95
            right_.location.x = LEAF / 2 + k * LEAF * 0.95
        return pose
    frames = [opener(k) for k in (0, 0.25, 0.5, 0.75, 1.0)]
    slots = {'indicator': [0, -0.17, LIFT_H + 0.18], 'threshold': [0, -0.4, 0]}
    return Vector((0, 0, 0)), [-1.1, -0.2, 0, 1.1, 0.05, LIFT_H + 0.3], slots, [], 0.3, frames


def plan_wall():
    """The plan wall's board: a light bezel around a recessed grey backing for COLS x ROWS tiles, and a strip
    of five criteria-light housings above it. Tiles and lights are separate sprites, placed on the slots.
    Anchor: the foot of the wall below the board's centre (the board's bottom edge is 0.75 m up)."""
    gw, gh = COLS * PITCH_T + 0.06, ROWS * PITCH_T + 0.06
    z0 = 0.75   # the light strip then tops out at 3.1 m, under the 3.2 m wall's cap
    A.box('bezel', (gw + 0.16, 0.06, gh + 0.16), (0, -0.03, z0), M('bezel', rough=0.5), bevel=0.012)
    A.box('backing', (gw, 0.02, gh), (0, -0.065, z0 + 0.08), M('backing', rough=0.8), bevel=0.0)
    A.box('lights_strip', (1.3, 0.05, 0.24), (0, -0.025, z0 + gh + 0.26), M('bezel', rough=0.5), bevel=0.01)
    floor_catcher(); wall_catcher()
    x0 = -COLS * PITCH_T / 2 + PITCH_T / 2
    slots = {
        'tiles': {'first': [round(x0, 4), -0.075, round(z0 + 0.08 + 0.03 + PITCH_T / 2, 4)],  # centre of the bottom-left tile
                  'col': [PITCH_T, 0, 0], 'row': [0, 0, PITCH_T], 'cols': COLS, 'rows': ROWS},
        'lights': [[round(-0.48 + i * 0.24, 3), -0.05, round(z0 + gh + 0.38, 3)] for i in range(5)],
    }
    return Vector((0, 0, 0)), [-(gw + 0.16) / 2, -0.07, z0, (gw + 0.16) / 2, 0, z0 + gh + 0.5], slots, [], 0.25


def tile(state):
    """One plan tile on the wall (facing -y), anchor at its centre on the backing: blank, done (a grey tick),
    running (amber, lit from within) or failed (a muted red cross)."""
    face = emissive('amber', 0.7) if state == 'running' else M('tile', rough=0.5)
    A.box('tile', (TILE, 0.03, TILE), (0, -0.015, -TILE / 2), face, bevel=0.01, rot=(0, 0, 0))
    ink = M('ink', rough=0.6) if state != 'failed' else M('fail', rough=0.6)
    k = TILE / 0.34   # the marks were drawn for a 34 cm tile

    def stroke(n, length, cx, cz, angle):
        A.box(n, (length * k, 0.006, 0.026 * k), (cx * k, -0.033, (cz - 0.013) * k), ink, bevel=0.003, rot=(0, angle, 0))
    if state in ('done', 'running'):
        # (a positive turn about y lowers a stroke's +x end): down to the heel, then up to the right
        stroke('tick_a', 0.1, -0.06, -0.02, math.radians(45))
        stroke('tick_b', 0.2, 0.035, 0.01, math.radians(-52))
    if state == 'failed':
        stroke('cross_a', 0.2, 0, 0, math.radians(45))
        stroke('cross_b', 0.2, 0, 0, math.radians(-45))
    wall_catcher(0.0)
    return Vector((0, 0, 0)), [-TILE / 2, -0.04, -TILE / 2, TILE / 2, 0, TILE / 2], {}, [], 0.05


def criteria(on):
    """A criteria light in its housing, facing -y: warm and lit, or dark glass. Anchor: its centre on the strip."""
    A.cylinder('rim', 0.09, 0.03, (0, 0, 0), M('steel', rough=0.4, metal=0.5), rot=(math.pi / 2, 0, 0), segments=32)
    A.cylinder('lens', 0.072, 0.03, (0, -0.012, 0), emissive('lamp_on', 1.2) if on else M('lamp_off', rough=0.15),
               rot=(math.pi / 2, 0, 0), segments=32)
    wall_catcher(0.0)
    return Vector((0, 0, 0)), [-0.09, -0.045, -0.09, 0.09, 0, 0.09], {}, [], 0.06


PIECES = {
    'pilaster': pilaster, 'wall-cap-x': cap_x, 'wall-cap-y': cap_y, 'wall-corner': corner,
    'wall-end-back': wall_end_back, 'wall-end-left': wall_end_left,
    'slab-front': slab_front, 'slab-side': slab_side,
    'lift': lift, 'lift-panel': lift_panel, 'alcove': alcove, 'plan-wall': plan_wall,
    'tile-blank': lambda: tile('blank'), 'tile-done': lambda: tile('done'),
    'tile-running': lambda: tile('running'), 'tile-failed': lambda: tile('failed'),
    'criteria-off': lambda: criteria(False), 'criteria-on': lambda: criteria(True),
}


def main() -> None:
    A.require_blender()
    out = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    only = sys.argv[sys.argv.index('--') + 2:]
    A.reset()
    studio()
    info = {'camera': {'pitch': PITCH, 'yaw': YAW}, 'blender': bpy.app.version_string, 'pieces': {}}
    if only and (out / 'pieces.json').exists():  # re-rendering some pieces keeps the others
        info['pieces'] = json.loads((out / 'pieces.json').read_text())['pieces']
    info['pieces'] = {k: v for k, v in info['pieces'].items() if k in PIECES}
    for name, build in PIECES.items():
        if only and name not in only:
            continue
        clear()
        made = build()
        anchor, footprint, slots, extra, margin = made[:5]
        frames = made[5] if len(made) > 5 else None
        tiers = render(name, out, anchor, extra, margin, frames)
        info['pieces'][name] = {'tiers': tiers, 'footprint': [round(v, 4) for v in footprint], 'slots': slots,
                                **({'doc': build.__doc__.split('\n')[0].strip()} if build.__doc__ else {})}
        print('KIT', name, [t['size'] for t in tiers], flush=True)
    (out / 'pieces.json').write_text(json.dumps(info, indent=2))


if __name__ == '__main__':   # (build_walker.py imports the studio and renderer)
    main()
