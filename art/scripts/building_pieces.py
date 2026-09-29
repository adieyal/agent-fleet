"""The building view's (L0) Blender pieces: floors, the lobby, spine and lift segments, the roof, the plinth and the
storehouse annex, rendered as stackable sprites.

    blender -b --factory-startup -P art/scripts/building_pieces.py -- OUT_DIR [--mult 2] [--samples 32] [piece ...]

Every piece is modelled at real size in one building frame and rendered with one orthographic camera (pitch 30 deg,
yaw 20 deg: docs/design/art-direction.md) at PPM_1X x mult px/m, on transparent film. A piece is rendered in context:
the floor above (its slab is this floor's ceiling), the spine and the lift are present but invisible to the camera,
so they still cast shadows and bounce light. OUT_DIR gets <piece>@<ppm>.png and pieces.json (per piece: size, the
pixel its anchor lands on, and DOM slots as pixel offsets from that anchor). building_finish.py makes the WebP tiers.

Stacking: every piece's anchor is the point (0, 0, z) of its level (the front-left corner of the floor's top, x along
the front, y into the building, z up), and a floor-to-floor step is exactly STEP_PX screen pixels at tier 1, so the
browser places floor k at origin - (0, LOBBY_STEP + k * STEP_PX) and never moves or resizes it.
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402
import bake as B  # noqa: E402
import render_props as R  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

MODELS = Path.home() / 'Development' / 'agent-fleet-assets' / 'models'
PITCH, YAW = 30.0, 20.0
F2F, SLAB = 4.0, 0.85              # floor to floor; slab thickness shown at the cut edge (l0's heavy bands)
SKY, SUN, EXPOSURE = 0.8, 6.0, -0.7    # the sun well over a blue sky: l0's blue ground shadow, fronts still pale
SUN_FROM = Vector((-1.0, -0.35, 0.75))  # towards the sun: front left, so fronts are lit and the shadow runs off right
GLASS_SETBACK = 0.45               # glazing stands behind the slab edge, which reads as a ledge (l0)
LOBBY_H = 5.5                      # 1.375 floors: its step is a whole pixel too
W, D = 30.0, 10.0                  # a floor's width along the front and depth (deep, as l0's open floors read)
STEP_PX = 88                       # l0's floor-to-floor on screen at tier 1
PPM_1X = STEP_PX / (F2F * math.cos(math.radians(PITCH)))   # 25.403 px/m
SPINE_W, SPINE_FRONT = 5.0, 1.2    # the spine's width, and how far it stands proud of the floors' front edge
LIFT_W = 3.2
FLOORS = 5                         # the mock building above the lobby (for context and the ground shadow)
FACE_CAP = 6000                    # decimate models to this: a chair is ~25 px tall at tier 1

PAL = {
    'shell': '#fbf1e6', 'shell_edge': '#e2d9d0', 'floor': '#b4b3bb', 'wall': '#d2d4dc', 'wall_warm': '#ddd2c8',
    'oak': '#b98f6c', 'frame': '#45434a', 'cabinet': '#6b6b73', 'mullion': '#26272c', 'blind': '#9a9ca3',
    'walnut': '#8f5e3a', 'plinth': '#cdbfb2', 'grout': '#a39588', 'ground': '#eef6ff', 'door': '#75737a', 'lip': '#9c8878',
    'lift_glass': '#6d7682', 'lobby_glaze': '#c4d6ea', 'bulb': '#fff3d0', 'warm': '#ffbf78', 'kiosk': '#d9dade', 'screen': '#20242a',
    'dot1': '#41ced1', 'dot2': '#4082f3', 'dot3': '#878638', 'crate': '#8a6a52',
}


# --- scene ------------------------------------------------------------------------------------------

def axes():
    p, y = math.radians(PITCH), math.radians(YAW)
    back = Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))
    right = (-back).cross(Vector((0, 0, 1))).normalized()
    up = right.cross(-back).normalized()
    return right, up, back


def studio(samples: int) -> None:
    """High-key daylight: a pale sky, a soft sun from the front left whose shadow runs off to the right."""
    s = bpy.context.scene
    world = bpy.data.worlds.new('sky')
    world.use_nodes = True
    bg = world.node_tree.nodes['Background']
    bg.inputs['Color'].default_value = A.srgb('#bcd6f4')
    bg.inputs['Strength'].default_value = SKY
    s.world = world
    sun = A.light('sun', 'SUN', (0, 0, 30), SUN, '#fff4e6', angle=math.radians(9))
    sun.rotation_euler = SUN_FROM.to_track_quat('Z', 'Y').to_euler()
    B.gpu()
    B.setup(s)
    s.cycles.samples = samples
    s.cycles.use_denoising = True
    s.cycles.max_bounces = 6
    s.view_settings.view_transform = 'Standard'
    s.view_settings.look = 'None'
    s.view_settings.exposure = EXPOSURE
    s.render.film_transparent = True
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'


def clear() -> None:
    for o in list(bpy.data.objects):
        if o.name != 'sun' and not o.get('proto'):
            bpy.data.objects.remove(o)


def M(key, rough=0.6, metal=0.0):
    return A.material(f'b_{key}_{rough}_{metal}', PAL[key], rough=rough, metal=metal)


def glow(key, strength):
    m = A.material(f'b_{key}_glow{strength}', PAL[key])
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Emission Color'].default_value = A.srgb(PAL[key])
    b.inputs['Emission Strength'].default_value = strength
    return m


def glass(name, tint, gloss):
    """Thin glazing: a tinted see-through layer under a sky reflection (no refraction: panes are single planes)."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    tr.inputs['Color'].default_value = A.srgb(tint)
    gl = nt.nodes.new('ShaderNodeBsdfGlossy')
    gl.inputs['Roughness'].default_value = 0.08
    gl.inputs['Color'].default_value = A.srgb('#dfe8f2')
    lw = nt.nodes.new('ShaderNodeLayerWeight')
    lw.inputs['Blend'].default_value = 0.35
    add = nt.nodes.new('ShaderNodeMath')
    add.operation, add.use_clamp = 'ADD', True
    add.inputs[1].default_value = gloss
    nt.links.new(lw.outputs['Fresnel'], add.inputs[0])
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(add.outputs[0], mix.inputs['Fac'])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(gl.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs['Surface'])
    return m


def tiles(name, base, grout, size):
    """Square tiles `size` m on a side with thin grout, box-mapped in world metres (artlib's UVMap)."""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = A.material(name, base, rough=0.45)
    nt = m.node_tree
    bsdf = nt.nodes['Principled BSDF']
    uv = nt.nodes.new('ShaderNodeUVMap')
    uv.uv_map = 'UVMap'
    br = nt.nodes.new('ShaderNodeTexBrick')
    br.offset = 0.0
    br.inputs['Color1'].default_value = A.srgb(base)
    br.inputs['Color2'].default_value = A.srgb(base)
    br.inputs['Mortar'].default_value = A.srgb(grout)
    br.inputs['Scale'].default_value = 1.0 / size
    br.inputs['Mortar Size'].default_value = 0.012
    br.inputs['Brick Width'].default_value = 1.0
    br.inputs['Row Height'].default_value = 1.0
    nt.links.new(uv.outputs['UV'], br.inputs['Vector'])
    nt.links.new(br.outputs['Color'], bsdf.inputs['Base Color'])
    return m


# --- geometry helpers --------------------------------------------------------------------------------

def prism(name, size, loc, mat, radius, segments=6, drop=()):
    """A box whose vertical edges are rounded by `radius` (horizontal edges stay sharp, so stacked segments meet
    without a seam). Base centre at `loc`."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((0, 0, size[2] / 2)), verts=bm.verts)
    vert = [e for e in bm.edges if abs((e.verts[0].co - e.verts[1].co).normalized().z) > 0.99]
    bmesh.ops.bevel(bm, geom=vert, offset=radius, segments=segments, profile=0.5, affect='EDGES', clamp_overlap=True)
    if drop:
        bm.normal_update()
        dirs = [Vector(A.AXES[d]) for d in drop]
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if any(f.normal.dot(d) > 0.999 for d in dirs)], context='FACES')
    return A._finish(name, bm, mat, 'baked', loc, (0, 0, 0), 1.0, None)


def span(name, x0, y0, z0, x1, y1, z1, mat, bevel=0.02, **kw):
    """A box from corner (x0, y0, z0) to (x1, y1, z1)."""
    return A.box(name, (x1 - x0, y1 - y0, z1 - z0), ((x0 + x1) / 2, (y0 + y1) / 2, z0), mat, bevel=bevel, **kw)


PROTOS: dict[str, bpy.types.Object] = {}


def proto(key, glb, size, **kw):
    """Import a model once, decimated, as a hidden prototype; `put` places linked copies."""
    if key in PROTOS:
        return PROTOS[key]
    ob = R.place(MODELS, R.part(glb, size, **kw), 'proto_' + key)
    if R.faces(ob) > FACE_CAP:
        mod = ob.modifiers.new('decimate', 'DECIMATE')
        mod.ratio = FACE_CAP / R.faces(ob)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.modifier_apply(modifier=mod.name)
    ob['proto'] = True
    ob.hide_render = True
    ob.location.z = -200
    PROTOS[key] = ob
    return ob


def put(key, loc, rot_z=0.0, scale=1.0):
    p = PROTOS[key]
    d = p.copy()
    del d['proto']
    d.name = key
    bpy.context.scene.collection.objects.link(d)
    d.location = loc
    d.rotation_euler = (0, 0, rot_z)
    d.scale = (scale,) * 3
    d.hide_render = False
    return d


def load_protos() -> None:
    proto('chair', 'objects/chair', ('z', 1.0), backrest='+y')    # faces -y (the viewer) as placed
    proto('monitor', 'objects/monitor', ('x', 0.55))
    proto('lamp', 'objects/desk lamp', ('z', 0.45))
    proto('plant_tall', 'plants and rugs/plant in square pot', ('z', 1.4))
    proto('plant_bush', 'plants and rugs/plant in round pot', ('z', 1.0))
    proto('plant_small', 'objects/pot plant', ('z', 0.35))
    A.tint(proto('crate', 'garage/crate', ('x', 0.9)), '#a88a7c')   # the model's orange to l0's crate brown
    proto('kiosk', 'lobby3/search kiosk', ('z', 1.5))
    proto('projector', 'lobby3/film projector', ('z', 0.55))
    proto('pendant', 'waiting area/ceiling lamp', ('z', 0.5))


# --- furniture --------------------------------------------------------------------------------------

DESK_W, DESK_D, DESK_Z = 1.6, 0.8, 0.74
LIGHTS: list[bpy.types.Object] = []


def desk(x, y, facing, rnd, lit):
    """One desk, oak top and dark frame; its chair and monitor on the side `facing` (+1: the user sits at +y and
    faces the viewer, -1: sits nearer the viewer)."""
    top = M('oak', rough=0.45)
    frame = M('frame', rough=0.4, metal=0.5)
    span('top', x, y, DESK_Z - 0.035, x + DESK_W, y + DESK_D, DESK_Z, top, bevel=0.008)
    for lx in (x + 0.04, x + DESK_W - 0.08):
        span('leg', lx, y + 0.05, 0, lx + 0.04, y + DESK_D - 0.05, 0.06, frame, bevel=0.005)
        span('leg', lx, y + DESK_D / 2 - 0.02, 0, lx + 0.04, y + DESK_D / 2 + 0.02, DESK_Z - 0.035, frame, bevel=0.005)
    cx = x + DESK_W / 2 + rnd.uniform(-0.2, 0.2)
    seat_y = y + DESK_D + 0.35 if facing > 0 else y - 0.35
    put('chair', (cx, seat_y, 0), 0.0 if facing > 0 else math.pi)
    mon_y = y + 0.18 if facing > 0 else y + DESK_D - 0.18
    put('monitor', (cx + rnd.uniform(-0.1, 0.1), mon_y, DESK_Z), math.pi if facing > 0 else 0.0)
    lx = x + (0.2 if rnd.random() < 0.5 else DESK_W - 0.2)
    ly = y + DESK_D / 2
    put('lamp', (lx, ly, DESK_Z), rnd.uniform(0, 2 * math.pi))
    if rnd.random() < 0.3:
        put('plant_small', (x + DESK_W - 0.25, y + DESK_D / 2 + 0.1 * facing, DESK_Z))
    if lit:
        A.ball('bulb', 0.05, (lx, ly, DESK_Z + 0.36), glow('bulb', 12.0), kind='baked')
        LIGHTS.append(A.light('desk_lamp', 'POINT', (lx, ly, DESK_Z + 0.33), 25, PAL['warm'], shadow_soft_size=0.08))


def bench(x, y, n, rnd, lit):
    """Two rows of n desks facing each other across a low screen; (x, y) is the near-left corner."""
    for i in range(n):
        desk(x + i * DESK_W, y, -1, rnd, lit)
        desk(x + i * DESK_W, y + DESK_D, 1, rnd, lit)
    span('screen', x + 0.05, y + DESK_D - 0.015, DESK_Z, x + n * DESK_W - 0.05, y + DESK_D + 0.015, DESK_Z + 0.3,
         M('blind', rough=0.8), bevel=0.01)
    if lit:   # a warm pendant wash over the bench
        # (a narrow spread keeps the warmth a pool round the bench, the rest of the floor in daylight)
        l = A.light('bench_wash', 'AREA', (x + n * DESK_W / 2, y + DESK_D, F2F - SLAB - 0.3), 35 * n, PAL['warm'],
                    shape='RECTANGLE', size=n * DESK_W, size_y=2 * DESK_D, spread=math.radians(70))
        LIGHTS.append(l)


def cabinets(x, n, y=D - 0.45):
    for i in range(n):
        span('cabinet', x + i * 0.9, y, 0, x + i * 0.9 + 0.88, y + 0.45, 0.8, M('cabinet', rough=0.5), bevel=0.012)


# --- floors -----------------------------------------------------------------------------------------

def slab(z=0.0, name='slab'):
    # (it runs under the back wall, so the wall's top never shows between stacked floors)
    span(name, 0, -0.2, z - SLAB, W, D + 0.3, z - 0.01, M('shell', rough=0.35), bevel=0.03)
    # the satin floor inside a pale rim along the cut edge
    span(name + '_top', 0, 0.0, z - 0.02, W, D, z, M('floor', rough=0.28), bevel=0.0)


def back_wall(z=0.0, warm=False):
    span('back_wall', 0, D, z, W, D + 0.3, z + F2F - SLAB, M('wall_warm' if warm else 'wall', rough=0.8), bevel=0.01)


def context(z0: float, z1: float, spine=True) -> None:
    """Camera-invisible surroundings of the storey z0..z1: the ceiling slab, the floor below, the spine and the lift
    (for a spine segment, only the spine above and below it)."""
    before = set(bpy.data.objects)
    span('ctx_ceiling', 0, -0.2, z1 - SLAB, W, D + 0.3, z1 + 0.5, M('shell'), bevel=0.0)
    span('ctx_below', 0, -0.2, z0 - F2F, W, D + 0.3, z0 - SLAB, M('shell'), bevel=0.0)
    if spine:
        span('ctx_spine', -SPINE_W, -SPINE_FRONT, z0 - F2F, 0, D + 0.3, z1 + F2F, M('shell'), bevel=0.0)
    else:
        span('ctx_spine', -SPINE_W, -SPINE_FRONT, z0 - F2F, 0, D + 0.3, z0 - 0.001, M('shell'), bevel=0.0)
        span('ctx_spine', -SPINE_W, -SPINE_FRONT, z1 + 0.001, 0, D + 0.3, z1 + F2F, M('shell'), bevel=0.0)
    span('ctx_lift', W, 0, z0 - F2F, W + LIFT_W, D, z1 + F2F, M('shell'), bevel=0.0)
    for o in set(bpy.data.objects) - before:
        o.visible_camera = False
        o['ctx'] = True


def ceiling_fill(z_top, energy):
    """The neutral fill under a ceiling: interiors are not lit only through the cut-away front."""
    A.light('fill', 'AREA', (W / 2, D / 2, z_top - 0.05), energy, '#f4f6ff', shape='RECTANGLE', size=W, size_y=D)


def open_floor(lit: bool):
    """A priority floor: no front wall, oak benches with chairs, monitors and lamps, cabinets and plants; lit means
    working: desk lamps and a warm wash over each bench."""
    rnd = random.Random(7)
    slab()
    back_wall(warm=lit)
    bench(2.2, 5.6, 3, rnd, lit)
    bench(10.5, 2.2, 4, rnd, lit)
    bench(21.0, 5.2, 3, rnd, lit)
    bench(3.0, 1.2, 2, rnd, lit)
    cabinets(6.0, 3)
    cabinets(24.5, 2)
    for x, y, k in ((0.8, D - 0.6, 'plant_tall'), (19.2, D - 0.6, 'plant_bush'), (W - 0.8, D - 0.7, 'plant_tall'),
                    (W - 0.9, 0.6, 'plant_bush'), (0.9, 0.5, 'plant_bush')):
        put(k, (x, y, 0), rnd.uniform(0, 6.28))
    ceiling_fill(F2F - SLAB, 350)
    context(0, F2F)
    return {'glass': False}


def curtain_wall(blinds_from: float):
    """The glazed front: mullions every 1.5 m, a sill and a head, panes of glass; blinds half down from x >= blinds_from."""
    h = F2F - SLAB
    g = GLASS_SETBACK
    m = M('mullion', rough=0.35, metal=0.4)
    span('sill', 0, g - 0.12, 0, W, g, 0.12, m, bevel=0.01)
    span('head', 0, g - 0.12, h - 0.12, W, g, h, m, bevel=0.01)
    n = int(W / 1.5)
    for i in range(n + 1):
        x = i * W / n
        span('mullion', x - 0.04, g - 0.13, 0, x + 0.04, g, h, m, bevel=0.01)
    span('transom', 0, g - 0.1, 2.6, W, g, 2.66, m, bevel=0.005)
    A.box('pane', (W, 0.01, h - 0.24), (W / 2, g - 0.06, 0.12), glass('b_glass', '#8d96a0', 0.06), bevel=0.0)
    for i in range(n):
        x0 = i * W / n
        if x0 >= blinds_from:
            drop = 1.3 if i % 3 else 2.0
            for k in range(int(drop / 0.09)):
                z = h - 0.14 - k * 0.09
                span('slat', x0 + 0.05, g + 0.05, z - 0.05, x0 + W / n - 0.05, g + 0.08, z, M('blind', rough=0.7), bevel=0.0)


def glass_floor(lit: bool):
    """A background floor behind glass: the same room, fewer props; lit means working: a warm glow behind the panes."""
    rnd = random.Random(11)
    slab()
    back_wall(warm=lit)
    bench(3.0, 1.8, 3, rnd, False)
    bench(11.5, 1.8, 4, rnd, False)
    cabinets(21.0, 4)
    for x, k in ((1.0, 'plant_tall'), (W - 1.0, 'plant_tall'), (19.5, 'plant_bush')):
        put(k, (x, 0.7, 0), rnd.uniform(0, 6.28))
    curtain_wall(blinds_from=W * 0.62)
    if lit:
        for cx in (7.0, 15.5):
            LIGHTS.append(A.light('glow', 'AREA', (cx, D / 2, F2F - SLAB - 0.2), 4500, PAL['warm'], shape='RECTANGLE',
                                  size=8.0, size_y=D - 1))
    ceiling_fill(F2F - SLAB, 150 if lit else 60)
    context(0, F2F)
    return {'glass': True}


def lobby():
    """The ground floor: the tiled floor, a pale back wall, two front columns, the reception desk (white with a
    walnut band), the search kiosk, a bench with a projector and plants."""
    rnd = random.Random(3)
    span('lobby_floor', 0, -0.2, -0.05, W, D, 0.0, tiles('b_lobby_tiles', PAL['plinth'], PAL['grout'], 1.2), bevel=0.0)
    h = LOBBY_H - SLAB
    # (l0's lobby is closed at the back by pale blue glazing: a pale blue wall with a glossy finish stands in)
    span('back_wall', 0, D, 0, W, D + 0.3, h, M('lobby_glaze', rough=0.2), bevel=0.01)
    span('door', W - 3.2, D - 0.02, 0, W - 1.6, D, 2.6, M('door', rough=0.35, metal=0.5), bevel=0.01)
    for x in (7.5, 20.5):
        span('column', x - 0.35, 0.3, 0, x + 0.35, 1.0, h, M('shell', rough=0.5), bevel=0.04)
    # the reception desk: a long white body, a walnut band on its front and a raised counter
    rx, ry = 12.0, 2.4
    span('reception', rx, ry, 0, rx + 4.4, ry + 0.9, 1.05, M('shell', rough=0.4), bevel=0.03)
    span('reception_band', rx + 1.8, ry - 0.03, 0.05, rx + 4.35, ry, 0.55, M('walnut', rough=0.5), bevel=0.01)
    span('reception_top', rx - 0.05, ry - 0.05, 1.05, rx + 4.45, ry + 0.95, 1.1, M('shell_edge', rough=0.3), bevel=0.01)
    put('monitor', (rx + 1.2, ry + 0.6, 1.1), math.pi, 0.8)
    put('plant_small', (rx + 3.8, ry + 0.4, 1.1))
    put('kiosk', (rx + 6.5, 1.0, 0), rnd.uniform(-0.2, 0.2))
    # the host colour key's place on the back wall (the dots are live: drawn here only as blank discs)
    for i in range(3):
        A.cylinder('key_disc', 0.28, 0.03, (13.2 + i * 0.8, D - 0.001, 3.1), M('shell_edge', rough=0.4),
                   rot=(math.pi / 2, 0, 0), segments=32)
    span('bench_seat', 2.5, 2.0, 0.4, 4.6, 2.5, 0.46, M('shell_edge', rough=0.5), bevel=0.02)
    for x in (2.6, 4.4):
        span('bench_leg', x, 2.05, 0, x + 0.1, 2.45, 0.4, M('frame', rough=0.4, metal=0.5), bevel=0.01)
    span('side_table', 5.0, 1.9, 0, 5.8, 2.5, 0.5, M('oak', rough=0.5), bevel=0.02)
    put('projector', (5.4, 2.2, 0.5), 0.6)
    for x, y, k in ((1.0, 1.0, 'plant_tall'), (8.6, 1.2, 'plant_tall'), (W - 4.0, 1.0, 'plant_tall'), (1.0, D - 0.8, 'plant_bush'),
                    (W - 0.9, D - 0.8, 'plant_bush'), (10.5, D - 0.7, 'plant_bush')):
        put(k, (x, y, 0), rnd.uniform(0, 6.28))
    ceiling_fill(h, 1400)
    A.light('lobby_glow', 'AREA', (14.2, D - 1.0, h - 0.2), 250, '#fff0dc', shape='RECTANGLE', size=8, size_y=2)
    before = set(bpy.data.objects)
    span('ctx_ceiling', 0, -0.2, h, W, D + 0.3, h + 3.0, M('shell'), bevel=0.0)
    span('ctx_spine', -SPINE_W, -SPINE_FRONT, 0, 0, D + 0.3, LOBBY_H + F2F, M('shell'), bevel=0.0)
    span('ctx_lift', W, 0, 0, W + LIFT_W, D, LOBBY_H + F2F, M('shell'), bevel=0.0)
    for o in set(bpy.data.objects) - before:
        o.visible_camera = False
        o['ctx'] = True
    return {}


# --- spine, lift, roof --------------------------------------------------------------------------------

def spine_segment(h: float):
    """One storey of the spine: the heavy white column the plates hang on; its front-right corner rounded."""
    prism('spine', (SPINE_W, D + 0.3 + SPINE_FRONT, h), (-SPINE_W / 2, (D + 0.3 - SPINE_FRONT) / 2, 0),
          M('shell', rough=0.5), radius=0.45)
    context(0, h, spine=False)
    return {}


def spine_cap():
    """The spine's top: it stands 0.6 m above the roof's parapet, capped."""
    prism('spine', (SPINE_W, D + 0.3 + SPINE_FRONT, 0.6), (-SPINE_W / 2, (D + 0.3 - SPINE_FRONT) / 2, 0), M('shell', rough=0.5), radius=0.45)
    prism('spine_cap', (SPINE_W + 0.12, D + 0.42 + SPINE_FRONT, 0.14), (-SPINE_W / 2, (D + 0.3 - SPINE_FRONT) / 2, 0.6),
          M('shell_edge', rough=0.4), radius=0.5)
    return {}


def lift_segment(h: float):
    """One storey of the lift shaft on the right: a frame of square posts and a beam at each floor, with dark
    glazing between and the lift doors on its front."""
    fr = M('shell', rough=0.5)
    x0, x1 = W, W + LIFT_W
    for x, y in ((x0 + 0.25, -0.1), (x1 - 0.25, -0.1), (x1 - 0.25, D - 0.25)):
        span('post', x - 0.25, y - 0.25, 0, x + 0.25, y + 0.25, h, fr, bevel=0.02)
    span('beam_front', x0, -0.35, h - 0.45, x1, 0.15, h, fr, bevel=0.02)
    span('beam_side', x1 - 0.5, -0.35, h - 0.45, x1, D, h, fr, bevel=0.02)
    span('shaft_glass_front', x0 + 0.5, -0.05, 0, x1 - 0.5, 0.0, h - 0.45, M('lift_glass', rough=0.25), bevel=0.0)
    span('shaft_glass_side', x1 - 0.3, 0.15, 0, x1 - 0.25, D - 0.5, h - 0.45, M('lift_glass', rough=0.25), bevel=0.0)
    span('lift_door_l', x0 + 0.75, -0.08, 0, x0 + LIFT_W / 2 - 0.02, -0.05, 2.5, M('door', rough=0.35, metal=0.5), bevel=0.005)
    span('lift_door_r', x0 + LIFT_W / 2 + 0.02, -0.08, 0, x1 - 0.75, -0.05, 2.5, M('door', rough=0.35, metal=0.5), bevel=0.005)
    for y in (2.4, 4.8):
        span('side_mullion', x1 - 0.35, y - 0.05, 0, x1 - 0.2, y + 0.05, h - 0.45, M('mullion', rough=0.4), bevel=0.0)
    before = set(bpy.data.objects)
    span('ctx_floor', 0, -0.2, -SLAB, W, D, h, M('shell'), bevel=0.0)
    for o in set(bpy.data.objects) - before:
        o.visible_camera = False
    return {}


def lift_cap():
    fr = M('shell', rough=0.5)
    span('lift_top', W, -0.35, 0, W + LIFT_W, D, 0.9, fr, bevel=0.03)
    return {}


def roof():
    """The roof, cut away as l0: only its rim stays, a heavy white band along the back and down the left side over
    the top floor's walls, so the top floor is seen from above."""
    fr = M('shell', rough=0.45)
    t = 0.9
    span('roof_back', 0, D - 0.6, -SLAB, W, D + 0.3, 0.35, fr, bevel=0.03)
    span('roof_left', 0, -0.2, -SLAB, t, D + 0.3, 0.35, fr, bevel=0.03)
    span('roof_back_cap', 0, D - 0.65, 0.35, W, D + 0.35, 0.5, M('shell_edge', rough=0.4), bevel=0.03)
    span('roof_left_cap', -0.05, -0.25, 0.35, t + 0.05, D + 0.35, 0.5, M('shell_edge', rough=0.4), bevel=0.03)
    before = set(bpy.data.objects)
    span('ctx_floor', 0, -0.2, -F2F, W, D + 0.3, -SLAB, M('shell'), bevel=0.0)
    for o in set(bpy.data.objects) - before:
        o.visible_camera = False
    return {}


# --- ground ---------------------------------------------------------------------------------------------

ANNEX_X0, ANNEX_W, ANNEX_D, ANNEX_H = W + LIFT_W + 1.6, 8.5, 5.5, 4.2
PLINTH = (-SPINE_W - 3.0, -9.0, W + LIFT_W + 1.6 + ANNEX_W + 2.5, D + 2.5)   # x0, y0, x1, y1


def whole_building(hide=True):
    """The mock building as invisible occluders: for the plinth's ground shadow and the annex's shade."""
    top = LOBBY_H + FLOORS * F2F
    before = set(bpy.data.objects)
    span('ctx_body', 0, 0, 0, W, D + 0.3, top + 0.6, M('shell'), bevel=0.0)
    span('ctx_spine', -SPINE_W, -SPINE_FRONT, 0, 0, D + 0.3, top + 0.74, M('shell'), bevel=0.0)
    span('ctx_lift', W, -0.35, 0, W + LIFT_W, D, top + 0.9, M('shell'), bevel=0.0)
    for o in set(bpy.data.objects) - before:
        o.visible_camera = not hide
        o['ctx'] = True


def plinth():
    """The tiled plinth the building stands on, the pale ground round it and the building's long soft shadow
    (cast by an invisible stand-in of a FLOORS-storey building). Opaque: it carries the backdrop."""
    x0, y0, x1, y1 = PLINTH
    span('plinth', x0, y0, -0.45, x1, y1, 0.0, tiles('b_plinth_tiles', PAL['plinth'], PAL['grout'], 1.5), bevel=0.04)
    span('plinth_lip', x0 - 0.05, y0 - 0.05, -0.5, x1 + 0.05, y1 + 0.05, -0.3, M('lip', rough=0.6), bevel=0.02)
    g = span('ground', -200, -200, -0.52, 200, 200, -0.5, M('ground', rough=0.9), bevel=0.0)
    del g
    whole_building()
    return {}


def annex():
    """The storehouse: an open-fronted annex right of the lift with a warm pendant and crates."""
    x0, x1, y0, y1, h = ANNEX_X0, ANNEX_X0 + ANNEX_W, 0.8, 0.8 + ANNEX_D, ANNEX_H
    fr = M('shell', rough=0.5)
    span('annex_floor', x0, y0, 0, x1, y1, 0.04, M('floor', rough=0.5), bevel=0.0)
    span('annex_back', x0, y1, 0, x1, y1 + 0.3, h, M('wall_warm', rough=0.8), bevel=0.01)
    span('annex_side', x1 - 0.3, y0, 0, x1, y1, h, fr, bevel=0.02)
    span('annex_left', x0, y0, 0, x0 + 0.3, y1, h, fr, bevel=0.02)
    span('annex_roof', x0 - 0.1, y0 - 0.3, h, x1 + 0.1, y1 + 0.3, h + 0.55, fr, bevel=0.03)
    span('annex_parapet', x0 - 0.1, y0 - 0.3, h + 0.55, x1 + 0.1, y0 + 0.2, h + 0.85, M('shell_edge'), bevel=0.02)
    put('pendant', (x0 + ANNEX_W / 2, y0 + ANNEX_D / 2, h - 0.8))
    A.ball('bulb', 0.08, (x0 + ANNEX_W / 2, y0 + ANNEX_D / 2, h - 0.72), glow('bulb', 15.0), kind='baked')
    A.light('annex_lamp', 'POINT', (x0 + ANNEX_W / 2, y0 + ANNEX_D / 2, h - 0.9), 600, PAL['warm'], shadow_soft_size=0.15)
    rnd = random.Random(5)
    stacks = [(x0 + 1.2, y1 - 0.8, 3), (x0 + 2.3, y1 - 0.9, 2), (x0 + 3.5, y1 - 0.8, 1), (x0 + 5.6, y1 - 0.8, 2),
              (x0 + 6.8, y1 - 0.9, 3), (x0 + 1.7, y0 + 1.8, 1), (x0 + 5.3, y0 + 2.0, 2), (x0 + 6.6, y0 + 1.6, 1)]
    for x, y, n in stacks:
        for k in range(n):
            put('crate', (x + rnd.uniform(-0.05, 0.05), y, k * 0.78), rnd.choice((0, math.pi / 2)) + rnd.uniform(-0.06, 0.06))
    whole_building()
    return {}


# --- rendering -------------------------------------------------------------------------------------------

def project(p: Vector) -> tuple[float, float]:
    """World point to screen metres (right, up)."""
    right, up, _ = axes()
    return p.dot(right), p.dot(up)


def window(pts) -> tuple[float, float, float, float]:
    us, vs = zip(*(project(p) for p in pts))
    return min(us), max(us), min(vs), max(vs)


def visible_points(margin: float):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in bpy.data.objects:
        if o.type != 'MESH' or o.hide_render or not o.visible_camera or o.get('proto'):
            continue
        e = o.evaluated_get(dg)
        pts += [e.matrix_world @ Vector(c) for c in e.bound_box]
    return pts


def render(name: str, out: Path, anchor: Vector, mult: int, frame=None, opaque=False) -> dict:
    """Render the camera-visible meshes framed by `frame` (world points; default their bounds). The window is
    snapped so the anchor lands on a pixel that stays whole at tier 1 (a multiple of `mult`)."""
    ppm = PPM_1X * mult
    u_min, u_max, v_min, v_max = window(frame or visible_points(0))
    ua, va = project(anchor)
    q = mult

    def snap(m):
        return math.ceil((m * ppm + 2 * mult) / q) * q
    left, right_, top, bottom = snap(ua - u_min), snap(u_max - ua), snap(v_max - va), snap(va - v_min)
    w, h = left + right_, top + bottom
    u0, v1 = ua - left / ppm, va + top / ppm
    u1, v0 = u0 + w / ppm, v1 - h / ppm
    right, up, back = axes()
    cam = bpy.data.cameras.get('b_cam') or bpy.data.cameras.new('b_cam')
    cam.type, cam.clip_end, cam.ortho_scale = 'ORTHO', 400, max(w, h) / ppm
    ob = bpy.data.objects.get('b_cam') or bpy.data.objects.new('b_cam', cam)
    if ob.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(ob)
    ob['proto'] = True   # (kept across pieces)
    centre = right * (u0 + u1) / 2 + up * (v0 + v1) / 2
    ob.location = centre + back * 150
    A.aim(ob, centre)
    s = bpy.context.scene
    s.camera = ob
    s.render.film_transparent = not opaque
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = w, h, 100
    p = out / f'{name}@{ppm:.0f}.png'
    s.render.filepath = str(p)
    bpy.ops.render.render(write_still=True)
    return {'ppm': round(ppm, 3), 'mult': mult, 'file': p.name, 'size': [w, h], 'anchor_px': [left, top]}


def px(offset: Vector, mult: int) -> list[float]:
    """A world offset from the anchor to screen pixels (x right, y down) at tier `mult`."""
    u, v = project(offset)
    return [round(u * PPM_1X * mult, 2), round(-v * PPM_1X * mult, 2)]


# DOM slots, world offsets from the piece's anchor: where the live controls sit (building_finish converts them)
SLOTS = {
    'spine': {'plate': Vector((-SPINE_W * 0.62, -SPINE_FRONT, 2.2)), 'ring': Vector((-0.75, -SPINE_FRONT, 2.2)),
              'lantern_bracket': Vector((-SPINE_W, -SPINE_FRONT + 0.6, 2.9))},
    'floor': {'shutter_handle': Vector((W - 1.2, -0.2, -SLAB / 2)), 'focus_hit': Vector((W / 2, D / 2, 1.5)),
              'to_let_card': Vector((W * 0.3, -0.05, 1.6))},
}

# name: (builder, level z of its anchor, frame points or None, opaque)
FLOOR_FRAME = [Vector(p) for p in ((0, -0.2, -SLAB), (W, -0.2, -SLAB), (0, D + 0.3, F2F - SLAB), (W, D + 0.3, F2F - SLAB),
                                   (0, -0.2, F2F - SLAB), (W, D + 0.3, -SLAB))]
_x0, _y0, _x1, _y1 = PLINTH
PLINTH_FRAME = [Vector(p) for p in ((_x0 - 2, _y0 - 2, -0.5), (_x1 + 9, _y0 - 2, -0.5), (_x0 - 2, _y1 + 2, -0.5),
                                    (_x1 + 9, _y1 + 2, -0.5), (_x0 - 2, _y1, LOBBY_H + FLOORS * F2F + 2))]
PIECES = {
    'floor-open-lit': (lambda: open_floor(True), 'floor'),
    'floor-open-unlit': (lambda: open_floor(False), 'floor'),
    'floor-glass-lit': (lambda: glass_floor(True), 'floor'),
    'floor-glass-unlit': (lambda: glass_floor(False), 'floor'),
    'lobby': (lobby, None),
    'spine': (lambda: spine_segment(F2F), 'spine'),
    'spine-lobby': (lambda: spine_segment(LOBBY_H), 'spine'),
    'spine-cap': (spine_cap, None),
    'lift': (lambda: lift_segment(F2F), None),
    'lift-lobby': (lambda: lift_segment(LOBBY_H), None),
    'lift-cap': (lift_cap, None),
    'roof': (roof, None),
    'plinth': (plinth, None),
    'annex': (annex, None),
}


def main() -> None:
    A.require_blender()
    argv = sys.argv[sys.argv.index('--') + 1:]
    out = Path(argv.pop(0)).resolve()
    mult, samples = 2, 32
    if '--mult' in argv:
        i = argv.index('--mult'); mult = int(argv[i + 1]); del argv[i:i + 2]
    if '--samples' in argv:
        i = argv.index('--samples'); samples = int(argv[i + 1]); del argv[i:i + 2]
    only = argv
    out.mkdir(parents=True, exist_ok=True)
    A.reset()
    studio(samples)
    load_protos()
    info = {'camera': {'pitch': PITCH, 'yaw': YAW, 'ppm_1x': round(PPM_1X, 4)}, 'step_px': STEP_PX,
            'lobby_step_px': round(LOBBY_H / F2F * STEP_PX), 'floor': {'w': W, 'd': D, 'f2f': F2F, 'slab': SLAB},
            'blender': bpy.app.version_string, 'pieces': {}}
    manifest = out / 'pieces.json'
    if only and manifest.exists():
        info['pieces'] = json.loads(manifest.read_text())['pieces']
    for name, (build, slots) in PIECES.items():
        if only and name not in only:
            continue
        clear()
        LIGHTS.clear()
        build()
        frame = FLOOR_FRAME if name.startswith('floor-') else PLINTH_FRAME if name == 'plinth' else None
        tier = render(name, out, Vector((0, 0, 0)), mult, frame, opaque=name == 'plinth')
        tier['slots'] = {k: px(v, mult) for k, v in SLOTS.get(slots, {}).items()} if slots else {}
        info['pieces'][name] = tier
        print('PIECE', name, tier['size'], flush=True)
    manifest.write_text(json.dumps(info, indent=1))


if __name__ == '__main__':
    main()
