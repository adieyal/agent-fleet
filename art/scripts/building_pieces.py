"""The building view's (L0) Blender pieces: every floor state, the lobby, the spine and lift columns, the lantern
bracket, the roof, the plinth with its ground shadow per capacity, and the storehouse annex per crate count.

    blender -b --factory-startup -P art/scripts/building_pieces.py -- OUT_DIR [--mult 4] [--samples 64] [piece ...]

Every piece is modelled at real size in one building frame and rendered with the canonical camera
(artlib.canonical_camera: yaw 30 deg, rays falling at atan(1/2), verticals vertical and full length; the Camera section of
docs/design/art-direction.md) at PPM_1X x mult px/m, on transparent film. A piece is rendered in context:
the floor above (its slab is this floor's ceiling), the spine and the lift are present but invisible to the camera,
so they still cast shadows and bounce light. OUT_DIR gets <render>@<ppm>.png and pieces.json (per piece: its render,
an optional crop, size, the pixel its anchor lands on, and DOM slots as pixel offsets from that anchor).
building_finish.py crops and writes the WebP tiers and the manifest.

Stacking: every piece's anchor is the point (0, 0, z) of its level (the front-left corner of the floor's top, x along
the front, y into the building, z up), and a floor-to-floor step is exactly STEP_PX screen pixels at tier 1, so the
browser places floor k at origin - (0, LOBBY_STEP + k * STEP_PX) and never moves or resizes it. The spine and the
lift are each rendered once as a tall column and cut into bands one level high (a vertical prism repeats every
storey on screen), so their segments tile without a seam.
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
from mathutils import Vector  # noqa: E402

MODELS = Path.home() / 'Development' / 'agent-fleet-assets' / 'models'
# The canonical projection: one metre along world x, y and z lands at these screen offsets (px right, px down) per px/m.
# It is steeper than l0's building (art-direction.md, The concepts disagree): the front edge descends 16 deg to the
# right and the floors show their depth, so the building is narrower and shallower than l0's to fit ten floors.
PX, PY, PZ = A.canonical_projection(1.0)
# what every piece records in the manifest: the projection it was rendered with
PROJECTION = {'projection': 'canonical', 'yaw': A.CANONICAL_YAW, 'depression': round(A.CANONICAL_DEPRESSION, 4),
              'x_px': [round(v, 6) for v in PX], 'y_px': [round(v, 6) for v in PY], 'z_px': [round(v, 6) for v in PZ]}
F2F, SLAB = 4.0, 0.85             # floor to floor; slab thickness shown at the cut edge (l0's heavy bands)
CAP = 0.16                         # the slab edge's pale cap (l0's bright band along every slab)
SKY, SUN, EXPOSURE = 0.85, 6.0, -1.1   # the sun well over a blue sky: l0's blue ground shadow, fronts still pale
# towards the sun: the left, a little behind, and low, so the long shadow runs off to the right and a little towards
# the viewer (l0's lower right) without covering the plinth in front; a shadowless fill from the front keeps the
# fronts pale, as l0's are
SUN_FROM = Vector((-1.0, 0.3, 0.45))
FRONT_FILL, FRONT_FROM = 2.0, Vector((-0.3, -1.0, 0.5))
GLASS_GLOW = 18.0                  # W per square metre of the warm panels behind a working glass floor's panes
PLINTH_LIFT = 0.5              # the plinth's exposure over the rest's: it is rendered without the front fill
GLASS_SETBACK = 0.45               # glazing stands behind the slab edge, which reads as a ledge (l0)
# a floor's width along the front and depth: the front drops W / 4 m across the screen, and the slab above hides a
# floor beyond about H / 0.433 = 7.3 m, so 24 x 9 m keeps ten floors on a 1440 x 900 screen with every floor's two
# rows of desks in view
W, D = 24.0, 9.0
H = F2F - SLAB                     # a storey's clear height
PPM_1X = 20.0                      # tier 1: about the scale ten floors show at on a desktop (zoom ~0.7)
STEP_PX = round(F2F * PPM_1X)      # 80 px: verticals are full length
LOBBY_STEP_PX = 98                 # the lobby is a little taller than a storey (l0), a whole pixel too
LOBBY_H = LOBBY_STEP_PX / PPM_1X   # 4.90 m
SPINE_W, SPINE_FRONT = 6.0, 1.5    # the spine's width and how far it stands proud of the floors' front
LIFT_W = 4.0
COLUMN_FLOORS = 4                  # floors in the tall spine and lift renders the bands are cut from
MAX_CAPACITY = 10                  # fleet/modules/workspace/domain/building.py: one plinth shadow per capacity
MAX_CRATES = 6                     # annex states: 0..6 crates (more shows as 6)
FACE_CAP = 6000                    # decimate models to this: a chair is ~25 px tall at tier 1

PAL = {
    'shell': '#fbf1e6', 'shell_edge': '#e2d9d0', 'floor': '#b4b3bb', 'wall': '#d2d4dc', 'wall_warm': '#ddd2c8',
    'oak': '#b98f6c', 'frame': '#45434a', 'cabinet': '#6b6b73', 'mullion': '#26272c', 'blind': '#9a9ca3',
    'walnut': '#8f5e3a', 'plinth': '#fbe9d6', 'grout': '#c2b1a2', 'ground': '#ffffff', 'door': '#8a8890', 'lip': '#9c8878',
    'lift_glass': '#d3dbe5', 'lobby_glaze': '#c4d6ea', 'bulb': '#fff3d0', 'warm': '#ffbf78', 'card': '#f6f4ef',
    'ink': '#2f3136', 'dots': '#f4f7fb', 'steel': '#5d5d64', 'car': '#2c2d33', 'slab_face': '#d9d3cd',
    'lab_top': '#dcdde2', 'lab_base': '#5f6068', 'flask': '#cfe6ee', 'plate': '#34363c',
}


# --- scene ------------------------------------------------------------------------------------------

def studio(samples: int) -> None:
    """High-key daylight: a pale sky, a soft sun from the front left whose shadow runs off to the right."""
    s = bpy.context.scene
    world = bpy.data.worlds.new('sky')
    world.use_nodes = True
    bg = world.node_tree.nodes['Background']
    bg.inputs['Color'].default_value = A.srgb('#9fc4f2')   # (blue: it colours l0's ground shadow)
    bg.inputs['Strength'].default_value = SKY
    s.world = world
    sun = A.light('sun', 'SUN', (0, 0, 30), SUN, '#fff4e6', angle=math.radians(9))
    sun.rotation_euler = SUN_FROM.to_track_quat('Z', 'Y').to_euler()
    fill = A.light('front_fill', 'SUN', (0, 0, 30), FRONT_FILL, '#fff8f0', angle=math.radians(30), use_shadow=False)
    fill.rotation_euler = FRONT_FROM.to_track_quat('Z', 'Y').to_euler()
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
        if o.name not in ('sun', 'front_fill') and not o.get('proto'):
            bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


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
    """A box whose vertical edges are rounded by `radius` (horizontal edges stay sharp). Base centre at `loc`."""
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


def hidden(*boxes) -> None:
    """Camera-invisible occluders: (x0, y0, z0, x1, y1, z1) boxes that still shade and bounce light."""
    for b in boxes:
        o = span('ctx', *b, M('shell'), bevel=0.0)
        o.visible_camera = False
        o['ctx'] = True


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
    A.tint(proto('crate', 'garage/crate', ('x', CRATE)), '#a88a7c')   # the model's orange to l0's crate brown
    proto('kiosk', 'lobby3/search kiosk', ('z', 1.5))
    proto('projector', 'lobby3/film projector', ('z', 0.55))
    proto('pendant', 'waiting area/ceiling lamp', ('z', 0.5))
    proto('bookcase', 'project fixtures/low bookcase with files', ('z', 1.2), back=True)
    proto('sofa', 'couches/cream loveseat', ('x', 1.9), backrest='+y')
    proto('coffee_table', 'couches/oval coffee table', ('x', 1.1))


# --- furniture --------------------------------------------------------------------------------------

DESK_W, DESK_D, DESK_Z = 1.6, 0.8, 0.74


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
        A.light('desk_lamp', 'POINT', (lx, ly, DESK_Z + 0.33), 30, PAL['warm'], shadow_soft_size=0.08)


def bench(x, y, n, rnd, lit, pendants=False):
    """Two rows of n desks facing each other across a low screen; (x, y) is the near-left corner. Pendants hang over
    it on cords from the ceiling, lit when the bench is working."""
    for i in range(n):
        desk(x + i * DESK_W, y, -1, rnd, lit)
        desk(x + i * DESK_W, y + DESK_D, 1, rnd, lit)
    span('screen', x + 0.05, y + DESK_D - 0.015, DESK_Z, x + n * DESK_W - 0.05, y + DESK_D + 0.015, DESK_Z + 0.3,
         M('blind', rough=0.8), bevel=0.01)
    if pendants:
        for i in range(n):
            px_, py_ = x + (i + 0.5) * DESK_W, y + DESK_D
            put('pendant', (px_, py_, H - 1.2))
            A.cylinder('cord', 0.01, 0.75, (px_, py_, H - 0.75), M('frame'), segments=6)
            if lit:
                A.ball('bulb', 0.07, (px_, py_, H - 1.12), glow('bulb', 10.0), kind='baked')
    if lit:
        # (a narrow spread keeps the warmth a pool round the bench)
        A.light('bench_wash', 'AREA', (x + n * DESK_W / 2, y + DESK_D, H - 0.3), 45 * n, PAL['warm'],
                shape='RECTANGLE', size=n * DESK_W, size_y=2 * DESK_D, spread=math.radians(70))


def meeting(x, y, rnd):
    """A meeting table (oak, 2.6 x 1.2 m) with three chairs a side; (x, y) its near-left corner."""
    span('table', x, y, DESK_Z - 0.04, x + 2.6, y + 1.2, DESK_Z, M('oak', rough=0.45), bevel=0.01)
    for lx in (x + 0.3, x + 2.2):
        span('pedestal', lx, y + 0.45, 0, lx + 0.1, y + 0.75, DESK_Z - 0.04, M('frame', rough=0.4, metal=0.5), bevel=0.01)
    for i in range(3):
        cx = x + 0.45 + i * 0.85 + rnd.uniform(-0.08, 0.08)
        put('chair', (cx, y + 1.5, 0), rnd.uniform(-0.2, 0.2))
        put('chair', (cx, y - 0.3, 0), math.pi + rnd.uniform(-0.2, 0.2))


def cabinets(x, n, y):
    """n low cabinets in a row from x, their front at y."""
    for i in range(n):
        span('cabinet', x + i * 0.9, y, 0, x + i * 0.9 + 0.88, y + 0.45, 0.8, M('cabinet', rough=0.5), bevel=0.012)


def bookcases(x, n, wall_y):
    """n low bookcases with files backed against the wall at wall_y."""
    for i in range(n):
        put('bookcase', (x + i * 1.25, wall_y, 0))


# --- floors -----------------------------------------------------------------------------------------

def slab(z=0.0, name='slab'):
    # (it runs under the back wall, so the wall's top never shows between stacked floors)
    span(name, 0, -0.2, z - SLAB, W, D + 0.3, z - CAP, M('slab_face', rough=0.5), bevel=0.03)
    # the pale cap along the cut edge, standing a little proud (l0's bright band)
    span(name + '_cap', 0, -0.26, z - CAP, W, D + 0.3, z - 0.01, M('shell', rough=0.3), bevel=0.03)
    # the satin floor inside a pale rim along the cut edge
    span(name + '_top', 0, 0.0, z - 0.02, W, D, z, M('floor', rough=0.28), bevel=0.0)


def back_wall(warm=False):
    span('back_wall', 0, D, 0, W, D + 0.3, H, M('wall_warm' if warm else 'wall', rough=0.8), bevel=0.01)


def context(top=False) -> None:
    """Camera-invisible surroundings of a storey: the ceiling slab (for the top floor, only the roof's rim), the floor
    below, the spine and the lift."""
    if top:
        hidden((0, D - 0.6, H, W, D + 0.3, F2F + 0.5), (0, -0.2, H, 0.9, D + 0.3, F2F + 0.5))
    else:
        hidden((0, -0.2, H, W, D + 0.3, F2F + 0.5))
    hidden((0, -0.2, -F2F, W, D + 0.3, -SLAB), (-SPINE_W, -SPINE_FRONT, -F2F, 0, D + 0.3, 2 * F2F),
           (W, -0.35, -F2F, W + LIFT_W, D, 2 * F2F))


def ceiling_fill(z_top, energy):
    """The neutral fill under a ceiling: interiors are not lit only through the cut-away front."""
    A.light('fill', 'AREA', (W / 2, D / 2, z_top - 0.05), energy, '#f4f6ff', shape='RECTANGLE', size=W, size_y=D)


def wall_wash() -> None:
    """Working light on the back wall: warm scallops from spots under the ceiling (l0's amber back walls)."""
    n = round(W / 4.3)
    for i in range(n):
        x = (i + 0.5) * W / n
        sp = A.light('washer', 'SPOT', (x, D - 1.0, H - 0.25), 180, PAL['warm'], spot_size=math.radians(80),
                     spot_blend=0.8, shadow_soft_size=0.2)
        A.aim(sp, (x, D, 0.8))


def curtain_wall(blinds_from: float | None):
    """The glazed front: mullions every 1.5 m, a sill and a head, panes of glass; blinds half down from x >= blinds_from."""
    g = GLASS_SETBACK
    m = M('mullion', rough=0.35, metal=0.4)
    span('sill', 0, g - 0.12, 0, W, g, 0.12, m, bevel=0.01)
    span('head', 0, g - 0.12, H - 0.12, W, g, H, m, bevel=0.01)
    n = int(W / 1.5)
    for i in range(n + 1):
        x = i * W / n
        span('mullion', x - 0.04, g - 0.13, 0, x + 0.04, g, H, m, bevel=0.01)
    span('transom', 0, g - 0.1, 2.6, W, g, 2.66, m, bevel=0.005)
    A.box('pane', (W, 0.01, H - 0.24), (W / 2, g - 0.06, 0.12), glass('b_glass', '#c9d1d9', 0.04), bevel=0.0)
    for i in range(n):
        x0 = i * W / n
        if blinds_from is not None and x0 >= blinds_from:
            drop = 1.3 if i % 3 else 2.0
            for k in range(int(drop / 0.09)):
                z = H - 0.14 - k * 0.09
                span('slat', x0 + 0.05, g + 0.05, z - 0.05, x0 + W / n - 0.05, g + 0.08, z, M('blind', rough=0.7), bevel=0.0)


# the to-let card's centre: in one window, between the pane and the mullions' faces (behind the tinted glass it vanished)
CARD = Vector((W * 0.3, GLASS_SETBACK - 0.1, 1.55))


def to_let() -> None:
    """A free floor's marks: a small "To let" card inside one window and a dotted outline round the glazing."""
    c = CARD
    span('card', c.x - 0.6, c.y, c.z - 0.4, c.x + 0.6, c.y + 0.02, c.z + 0.4, M('card', rough=0.7), bevel=0.01)
    A.text_mesh('card_text', 'TO LET', 0.26, (c.x, c.y - 0.003, c.z), M('ink', rough=0.6))
    dot = glow('dots', 0.6)
    y, step, s = GLASS_SETBACK - 0.16, 0.36, 0.1
    for x in [0.35 + i * step for i in range(int((W - 0.7) / step) + 1)]:
        for z in (0.3, H - 0.3):
            span('dot', x - s / 2, y, z - s / 2, x + s / 2, y + 0.02, z + s / 2, dot, bevel=0.0)
    for z in [0.3 + i * step for i in range(1, int((H - 0.6) / step))]:
        for x in (0.35, W - 0.35):
            span('dot', x - s / 2, y, z - s / 2, x + s / 2, y + 0.02, z + s / 2, dot, bevel=0.0)


def lab_bench(x, y, n, rnd, lit):
    """A lab bench (l0's Lab floor): a grey cabinet base with a pale top in n 1.6 m sections, glassware and an
    instrument or a screen on each, stools on the near side; lit, a warm task light over each section."""
    span('lab_base', x, y, 0, x + n * 1.6, y + 0.9, 0.86, M('lab_base', rough=0.5), bevel=0.015)
    span('lab_top', x - 0.03, y - 0.03, 0.86, x + n * 1.6 + 0.03, y + 0.93, 0.92, M('lab_top', rough=0.3), bevel=0.01)
    for i in range(1, n * 2):
        span('drawer_gap', x + 0.8 * i - 0.01, y - 0.006, 0.08, x + 0.8 * i + 0.01, y, 0.8, M('frame'), bevel=0.0)
    flask = glass('b_flask', PAL['flask'], 0.3)
    for i in range(n):
        cx = x + 1.6 * i + 0.8
        k = rnd.random()
        if k < 0.4:
            put('monitor', (cx, y + 0.6, 0.92))
        elif k < 0.8:
            span('instrument', cx - 0.3, y + 0.3, 0.92, cx + 0.3, y + 0.75, 1.32, M('lab_top', rough=0.4), bevel=0.03)
            span('instrument_face', cx - 0.2, y + 0.29, 1.05, cx + 0.2, y + 0.3, 1.25, M('plate', rough=0.2), bevel=0.0)
        for _ in range(rnd.randint(2, 4)):
            A.cylinder('flask', rnd.uniform(0.04, 0.07), rnd.uniform(0.15, 0.3),
                       (cx + rnd.uniform(-0.7, 0.7), y + rnd.uniform(0.2, 0.7), 0.92), flask, segments=12)
        A.cylinder('stool', 0.17, 0.62, (cx + rnd.uniform(-0.3, 0.3), y - 0.45, 0), M('frame', rough=0.4, metal=0.5), segments=16)
        if lit:
            A.light('task', 'POINT', (cx, y + 0.5, 1.7), 45, PAL['warm'], shadow_soft_size=0.2)


# l0 paints furniture larger than the building's scale; here it is 1.3x, which keeps a desk legible (about 30 px wide)
# when ten floors share a desktop screen. Furniture is laid out on a floor of W / FURN x D / FURN and then blown up by
# FURN about the floor's corner.
FURN = 1.3
WF, DF = W / FURN, D / FURN        # 18.5 x 6.9 furniture metres
BENCHES = {   # (x, y, desks per row) of each bench in furniture metres: two rows deep, as l0's floors
    'open': ((0.8, 1.0, 3), (7.0, 0.9, 3), (13.2, 1.1, 3), (3.0, 3.9, 3), (9.8, 4.0, 3)),
    'glass': ((0.8, 1.2, 3), (7.0, 1.2, 3), (13.2, 1.2, 3), (3.5, 4.1, 3), (10.5, 4.1, 3)),
    'lab': ((1.0, 1.2, 3), (7.4, 1.3, 3), (13.4, 1.2, 3), (2.5, 4.3, 4), (10.0, 4.4, 4)),
}


def scaled(build) -> None:
    """Run `build` and blow up what it makes by FURN about the origin: objects, their places and their lights."""
    before = set(bpy.data.objects)
    build()
    for o in set(bpy.data.objects) - before:
        o.location = o.location * FURN
        o.scale = o.scale * FURN
        if o.type == 'LIGHT':
            o.data.energy *= FURN ** 2


def furnish(kind, rnd, lit) -> None:
    """The room's furniture at l0's scale and density: desk benches two rows deep with a meeting table, bookcases and
    cabinets on the back wall and plants (open); fewer behind glass; lab benches with tall cabinets (lab)."""
    def build():
        for x, y, n in BENCHES[kind]:
            (lab_bench if kind == 'lab' else bench)(x, y, n, rnd, lit)
        back = DF - 0.45
        if kind == 'open':
            meeting(15.3, 4.3, rnd)
            bookcases(1.0, 3, DF)
            cabinets(5.0, 5, back)
            bookcases(10.0, 3, DF)
            cabinets(14.0, 4, back)
        elif kind == 'lab':
            for i in range(10):
                span('tall_cabinet', 2.0 + i * 1.0, DF - 0.6, 0, 2.95 + i * 1.0, DF, 1.9, M('lab_base', rough=0.5), bevel=0.015)
            cabinets(13.0, 5, back)
        else:
            cabinets(5.0, 5, back)
            bookcases(11.0, 3, DF)
        plants = ((0.5, DF - 0.6, 'plant_tall'), (WF - 0.5, DF - 0.6, 'plant_tall'), (0.5, 0.6, 'plant_bush'),
                  (WF - 0.6, 0.6, 'plant_bush'), (8.8, DF - 0.5, 'plant_tall'), (6.2, 3.3, 'plant_bush'))
        for x, y, k in plants:
            put(k, (x, y, 0), rnd.uniform(0, 6.28))
    scaled(build)


def floor(kind: str, lit: bool, top: bool) -> dict:
    """One storey. open: a priority floor, no front wall; lab: the same with lab benches (l0 has one); glass: a
    background floor behind glazing; free: an empty floor to let. Lit means working: amber light across the whole floor,
    desk lamps and a warm wash on the back wall; behind glass, a bright warm glow in the panes. The top floor has no
    ceiling (the roof is cut away) but the same size and anchor."""
    rnd = random.Random({'open': 7, 'lab': 17, 'glass': 11, 'free': 13}[kind])
    slab()
    back_wall(warm=lit)
    if kind in ('open', 'lab'):
        furnish(kind, rnd, lit)
    elif kind == 'glass':
        furnish(kind, rnd, lit)
        curtain_wall(W * 0.62)
    else:
        curtain_wall(None)
        to_let()
    if lit:
        wall_wash()
        # (behind glass a working floor glows, but stays quieter than any working open floor: busy never looks important;
        # tests/test_building_browser.py measures it)
        # (per square metre of floor: the same warmth on any floor plate)
        A.light('amber', 'AREA', (W / 2, D / 2, H - 0.1), W * D * (4.4 if kind == 'glass' else 6.4), PAL['warm'],
                shape='RECTANGLE', size=W, size_y=D)
        if kind == 'glass':
            for cx in (W * 0.18, W * 0.5, W * 0.82):
                A.light('glow', 'AREA', (cx, D / 2, H - 0.2), GLASS_GLOW * 7.0 * (D - 2), PAL['warm'], shape='RECTANGLE',
                        size=7.0, size_y=D - 2)
    ceiling_fill(H, {'open': 250, 'lab': 250, 'glass': 60, 'free': 220}[kind] * (0.6 if lit else 1.0))
    context(top)
    slots = {'shutter_handle': Vector((W - 1.6, -0.2, -SLAB / 2)), 'focus_hit': Vector((W / 2, D / 2, 1.5))}
    if kind == 'free':
        slots['to_let_card'] = CARD
    return {'frame': FLOOR_FRAME, 'slots': slots}


def lobby() -> dict:
    """The ground floor, enclosed as l0: a clear glass front between white columns under the slab above, the reception
    desk (white with a walnut front) in the foreground, the search kiosk beside it, a bench with a projector, plants,
    pale blue glazing at the back with a door, and the mounts of the host colour key (the dots themselves are live)."""
    rnd = random.Random(3)
    span('lobby_floor', 0, -0.2, -0.05, W, D, 0.0, tiles('b_lobby_tiles', PAL['plinth'], PAL['grout'], 1.2), bevel=0.0)
    h = LOBBY_H - SLAB
    span('back_wall', 0, D, 0, W, D + 0.3, h, M('lobby_glaze', rough=0.2), bevel=0.01)
    for x in range(2, int(W), 3):
        span('glaze_mullion', x - 0.04, D - 0.03, 0, x + 0.04, D, h, M('mullion', rough=0.4), bevel=0.0)
    span('door', W - 4.2, D - 0.05, 0, W - 2.4, D - 0.02, 2.6, M('door', rough=0.35, metal=0.5), bevel=0.01)
    # the glass front: thin frames every 3 m, clear panes, a white column at each third
    g = GLASS_SETBACK
    frame = M('mullion', rough=0.35, metal=0.4)
    span('front_sill', 0, g - 0.08, 0, W, g, 0.08, frame, bevel=0.0)
    span('front_head', 0, g - 0.08, h - 0.1, W, g, h, frame, bevel=0.0)
    for i in range(int(W / 3) + 1):
        x = min(i * 3.0, W)
        span('front_mullion', x - 0.03, g - 0.08, 0, x + 0.03, g, h, frame, bevel=0.0)
    A.box('front_pane', (W, 0.01, h - 0.18), (W / 2, g - 0.04, 0.08), glass('b_lobby_glass', '#eef3f7', 0.02), bevel=0.0)
    for x in (W * 0.34, W * 0.7):
        span('column', x - 0.45, -0.1, 0, x + 0.45, 0.8, h, M('shell', rough=0.5), bevel=0.04)
    # the furniture, in furniture metres (FURN): the reception desk in the foreground, a long white body with a
    # walnut front on its right half; the kiosk beside it; a bench and a projector; a sofa further in; plants
    rx, ry, rw = WF * 0.38, 0.6, 5.4

    def furniture():
        span('reception', rx, ry, 0, rx + rw, ry + 0.8, 1.05, M('shell', rough=0.4), bevel=0.03)
        span('reception_band', rx + rw * 0.45, ry - 0.04, 0.08, rx + rw - 0.1, ry, 0.68, M('walnut', rough=0.5), bevel=0.01)
        span('reception_top', rx - 0.05, ry - 0.05, 1.05, rx + rw + 0.05, ry + 0.85, 1.1, M('shell_edge', rough=0.3), bevel=0.01)
        put('monitor', (rx + 1.0, ry + 0.6, 1.1), math.pi, 0.8)
        put('plant_small', (rx + rw - 0.4, ry + 0.4, 1.1))
        put('kiosk', (rx + rw + 1.6, 0.8, 0), rnd.uniform(-0.15, 0.15))
        span('bench_seat', 1.2, 0.9, 0.42, 3.2, 1.35, 0.48, M('shell_edge', rough=0.5), bevel=0.02)
        for x in (1.3, 3.0):
            span('bench_leg', x, 0.95, 0, x + 0.1, 1.3, 0.42, M('frame', rough=0.4, metal=0.5), bevel=0.01)
        span('side_table', 3.6, 0.9, 0, 4.4, 1.5, 0.5, M('walnut', rough=0.5), bevel=0.02)
        put('projector', (4.0, 1.2, 0.5), 0.6)
        put('sofa', (2.4, 4.6, 0))
        put('coffee_table', (2.4, 3.6, 0))
        for x, y, k in ((0.6, 0.7, 'plant_tall'), (5.6, 0.8, 'plant_bush'), (WF - 3.2, 0.8, 'plant_tall'), (0.7, DF - 0.6, 'plant_bush'),
                        (WF - 0.7, DF - 0.6, 'plant_bush'), (WF - 0.8, 0.8, 'plant_tall'), (rx - 0.7, DF - 0.6, 'plant_tall')):
            put(k, (x, y, 0), rnd.uniform(0, 6.28))
    scaled(furniture)
    rx, ry, rw = rx * FURN, ry * FURN, rw * FURN   # (true metres from here)
    # the host colour key's mounts on the back wall: three discs (the dots are live DOM on the host_key slot)
    for i in range(3):
        A.cylinder('key_disc', 0.32, 0.03, (rx + 1.8 + i * 1.0, D - 0.001, 2.7), M('shell_edge', rough=0.4),
                   rot=(math.pi / 2, 0, 0), segments=32)
    ceiling_fill(h, 1100)   # (less than an office's: the furniture keeps its contrast behind the glass)
    A.light('lobby_glow', 'AREA', (rx + rw / 2, D - 1.0, h - 0.2), 300, '#fff0dc', shape='RECTANGLE', size=8, size_y=2)
    hidden((0, -0.2, h, W, D + 0.3, h + 3.0), (-SPINE_W, -SPINE_FRONT, 0, 0, D + 0.3, LOBBY_H + F2F),
           (W, -0.35, 0, W + LIFT_W, D, LOBBY_H + F2F))
    slots = {'front_desk': Vector((rx + rw / 2, ry + 0.6, 1.65)), 'desk_lantern': Vector((rx + rw / 2, ry + 0.6, 3.6)),
             'host_key': Vector((rx + 2.8, D, 2.7)), 'kiosk': Vector((rx + rw + 2.4, 1.2, 2.0)),
             'visitors': Vector((4.5, 9.0, 0.0))}
    return {'frame': LOBBY_FRAME, 'slots': slots}


# --- spine, lift, roof --------------------------------------------------------------------------------

def column_top() -> float:
    return LOBBY_H + COLUMN_FLOORS * F2F


def level_z(i: int) -> float:
    """Level 0 is the lobby floor, 1.. the floors, COLUMN_FLOORS + 1 the roof."""
    return 0.0 if i == 0 else LOBBY_H + (i - 1) * F2F


def building_occluders(top: float) -> None:
    """The floors as camera-invisible occluders beside a column: a slab at every level and the back wall, so light
    still reaches the spine's right face, which is each open floor's left wall; and the ground."""
    for i in range(1, COLUMN_FLOORS + 2):
        z = level_z(i)
        hidden((0.02, -0.2, z - SLAB, W, D + 0.3, z))
    hidden((0.02, D, 0, W, D + 0.3, top), (-40, -40, -1.0, 80, 40, -0.02))


def spine_column() -> dict:
    """The spine as one tall column (lobby, COLUMN_FLOORS floors, the part above the roof and its cap), rendered once
    and cut into bands: spine-lobby, spine (repeating) and spine-cap. A fill light linked to the spine alone lifts its
    front face, which turns away from the sun, to l0's warm white."""
    top = column_top()
    body = prism('spine', (SPINE_W, D + 0.3 + SPINE_FRONT, top + 0.6), (-SPINE_W / 2, (D + 0.3 - SPINE_FRONT) / 2, 0),
                 M('shell', rough=0.5), radius=0.45)
    cap = prism('spine_cap', (SPINE_W + 0.12, D + 0.42 + SPINE_FRONT, 0.14), (-SPINE_W / 2, (D + 0.3 - SPINE_FRONT) / 2, top + 0.6),
                M('shell_edge', rough=0.4), radius=0.5)
    lit = bpy.data.collections.new('spine_lit')
    for o in (body, cap):
        lit.objects.link(o)
    fill = A.light('spine_fill', 'SUN', (0, 0, 30), 1.6, '#fff6ee', angle=math.radians(20))
    fill.rotation_euler = Vector((-0.25, -1.0, 0.45)).to_track_quat('Z', 'Y').to_euler()
    fill.light_linking.receiver_collection = lit
    building_occluders(top)
    x = -SPINE_W
    # measured on l0: plates 75 x 28 px centred 30% across the spine's face and 26 px over their floor, rings 32 px
    # at 71% (so 1.1 m up, 3.4 x 1.13 m and 1.3 m at the fitted scale); the focus switch sits over the plate
    slots = {'plate': Vector((-SPINE_W * 0.7, -SPINE_FRONT, 1.1)), 'ring': Vector((-SPINE_W * 0.29, -SPINE_FRONT, 1.1)),
             'focus_switch': Vector((-SPINE_W * 0.7, -SPINE_FRONT, 2.25)),
             'lantern_bracket': Vector((x + 0.35, -SPINE_FRONT, 3.0))}
    boxes = {'plate': (3.4, 1.13), 'ring': (1.3, 1.3), 'focus_switch': (2.6, 0.6)}
    return {'column': 'spine', 'corner': Vector((0, 0, 0)), 'slots': slots, 'boxes': boxes}


def lift_storey(z: float, h: float, doors_open: bool) -> None:
    """One storey of the lift shaft on the right: square posts, a beam at the floor above, pale glazing between and
    the lift doors on its front (open: slid apart over the dark car)."""
    fr = M('shell', rough=0.5)
    x0, x1 = W, W + LIFT_W
    for x, y in ((x0 + 0.25, -0.1), (x1 - 0.25, -0.1), (x1 - 0.25, D - 0.25)):
        span('post', x - 0.25, y - 0.25, z, x + 0.25, y + 0.25, z + h, fr, bevel=0.02)
    span('beam_front', x0, -0.35, z + h - 0.45, x1, 0.15, z + h, fr, bevel=0.02)
    span('beam_side', x1 - 0.5, -0.35, z + h - 0.45, x1, D, z + h, fr, bevel=0.02)
    span('shaft_glass_front', x0 + 0.5, -0.05, z, x1 - 0.5, 0.0, z + h - 0.45, M('lift_glass', rough=0.25), bevel=0.0)
    span('shaft_glass_side', x1 - 0.3, 0.15, z, x1 - 0.25, D - 0.5, z + h - 0.45, M('lift_glass', rough=0.25), bevel=0.0)
    span('car', x0 + 0.75, -0.04, z, x1 - 0.75, 0.2, z + 2.5, M('car', rough=0.6), bevel=0.0)
    leaf = (LIFT_W - 1.5) / 2 - 0.02
    shift = 0.7 if doors_open else 0.0   # (the leaves slide behind the posts, never past the shaft's frame)
    span('lift_door_l', x0 + 0.75 - shift, -0.1, z, x0 + 0.75 + leaf - shift, -0.06, z + 2.5, M('door', rough=0.35, metal=0.5), bevel=0.005)
    span('lift_door_r', x1 - 0.75 - leaf + shift, -0.1, z, x1 - 0.75 + shift, -0.06, z + 2.5, M('door', rough=0.35, metal=0.5), bevel=0.005)
    span('lift_head', x0 + 0.5, -0.12, z + 2.5, x1 - 0.5, -0.02, z + 2.7, M('steel', rough=0.4, metal=0.5), bevel=0.005)
    # the side, which the canonical camera shows square on: a white lattice (l0's lift is a pale frame, not a wall)
    for y in (D * k / 3 for k in (1, 2)):
        span('side_post', x1 - 0.45, y - 0.18, z, x1 - 0.1, y + 0.18, z + h - 0.45, fr, bevel=0.02)
    span('side_rail', x1 - 0.4, 0.15, z + (h - 0.45) / 2 - 0.08, x1 - 0.15, D - 0.5, z + (h - 0.45) / 2 + 0.08, fr, bevel=0.01)


def lift_column(doors_open: bool) -> dict:
    """The lift shaft as one tall column, cut into bands like the spine; its top rises 0.9 m over the roof."""
    top = column_top()
    lift_storey(0, LOBBY_H, doors_open)
    for i in range(1, COLUMN_FLOORS + 1):
        lift_storey(level_z(i), F2F, doors_open)
    span('lift_top', W, -0.35, top, W + LIFT_W, D, top + 0.9, M('shell', rough=0.5), bevel=0.03)
    building_occluders(top)
    # (one frame for both door states, so the open and shut bands swap in place)
    frame = [Vector((x, y, z)) for x in (W - 0.05, W + LIFT_W + 0.05) for y in (-0.45, D) for z in (0, top + 0.95)]
    return {'column': 'lift-open' if doors_open else 'lift', 'frame': frame, 'corner': Vector((W, -0.35, 0)),
            'slots': {'lift_door': Vector((W + LIFT_W / 2, -0.1, 1.25))}}


def lantern_bracket() -> dict:
    """The bracket a floor's lantern hangs from: a steel arm from the spine's front left, with a stay and a hook.
    Anchored on its level like the spine; the lantern itself is live (it swings), so only its hook point is given."""
    st = M('steel', rough=0.35, metal=0.7)
    x, y, z, reach = -SPINE_W + 0.35, -SPINE_FRONT - 0.1, 3.0, 2.3   # (l0's arm is about half a plate long)
    span('bracket_plate', x - 0.2, y, z - 0.6, x + 0.2, y + 0.1, z + 0.2, st, bevel=0.01)
    span('bracket_arm', x - reach, y - 0.06, z - 0.06, x, y + 0.06, z + 0.06, st, bevel=0.01)
    A.box('bracket_stay', (1.3, 0.07, 0.07), (x - 0.55, y, z - 0.55), st, bevel=0.01, rot=(0, math.radians(-28), 0))
    A.cylinder('bracket_hook', 0.02, 0.25, (x - reach + 0.2, y, z - 0.25), st, segments=8)
    hidden((-SPINE_W, -SPINE_FRONT, -1, 0, D + 0.3, 6))
    return {'slots': {'lantern': Vector((x - reach + 0.2, y, z - 0.3))}}


def roof() -> dict:
    """The roof, cut away as l0: only its rim stays, a heavy white band along the back and down the left side over
    the top floor's walls, so the top floor is seen from above."""
    fr = M('shell', rough=0.45)
    t = 0.9
    span('roof_back', 0, D - 0.6, -SLAB, W, D + 0.3, 0.35, fr, bevel=0.03)
    span('roof_left', 0, -0.2, -SLAB, t, D + 0.3, 0.35, fr, bevel=0.03)
    span('roof_back_cap', 0, D - 0.65, 0.35, W, D + 0.35, 0.5, M('shell_edge', rough=0.4), bevel=0.03)
    span('roof_left_cap', -0.05, -0.25, 0.35, t + 0.05, D + 0.35, 0.5, M('shell_edge', rough=0.4), bevel=0.03)
    hidden((0, -0.2, -F2F, W, D + 0.3, -SLAB))
    return {}


# --- ground ---------------------------------------------------------------------------------------------

# l0's annex: a third of the building's width, about two storeys high, as deep as the lift's side
ANNEX_X0, ANNEX_W, ANNEX_D, ANNEX_H = W + LIFT_W + 1.0, 10.0, 8.5, 6.0
ANNEX_Y0 = 0.3
# the plinth: a rectangle square to the building, reaching well in front (l0) and past the spine and the annex
PLINTH = (-SPINE_W - 4.0, -9.0, ANNEX_X0 + ANNEX_W + 3.0, D + 3.0)   # x0, y0, x1, y1


def whole_building(floors: int) -> None:
    """A building of `floors` storeys above the lobby as invisible occluders: the plinth's shadow, the annex's shade."""
    top = LOBBY_H + floors * F2F
    hidden((0, 0, 0, W, D + 0.3, top + 0.5), (-SPINE_W, -SPINE_FRONT, 0, 0, D + 0.3, top + 0.74),
           (W, -0.35, 0, W + LIFT_W, D, top + 0.9))


def shadow_tip(p: Vector, ground=-0.5) -> Vector:
    s = SUN_FROM.normalized()
    return p - s * ((p.z - ground) / s.z)


def plinth(floors: int) -> dict:
    """The tiled plinth, the pale ground round it and the long soft shadow of a building `floors` storeys high.
    Opaque; its far ground renders flat, so its corner pixel is the backdrop colour the view fills round it."""
    x0, y0, x1, y1 = PLINTH
    span('plinth', x0, y0, -0.45, x1, y1, 0.0, tiles('b_plinth_tiles', PAL['plinth'], PAL['grout'], 1.5), bevel=0.04)
    span('plinth_lip', x0 - 0.05, y0 - 0.05, -0.5, x1 + 0.05, y1 + 0.05, -0.3, M('lip', rough=0.6), bevel=0.02)
    span('ground', -300, -300, -0.52, 300, 300, -0.5, M('ground', rough=0.9), bevel=0.0)
    whole_building(floors)
    top = LOBBY_H + floors * F2F + 0.9
    pts = [Vector((x, y, -0.5)) for x in (x0 - 3, x1 + 3) for y in (y0 - 3, y1 + 3)]
    pts += [shadow_tip(Vector((x, y, top))) + Vector((3, 3, 0)) for x in (-SPINE_W, W + LIFT_W) for y in (-SPINE_FRONT, D + 0.3)]
    pts += [Vector((x0, y1, top))]   # (the frame reaches the building's top: the view's canvas)
    # (no front fill here: it lit the shadow as much as the ground round it and greyed it out; the ground is then lit
    # by the sun and the blue sky alone, so the shadow is sky blue, and a brighter exposure brings the ground to l0's)
    return {'frame': pts, 'opaque': True, 'mult': 2, 'fill': 0.0, 'exposure': EXPOSURE + PLINTH_LIFT}


ANNEX_FRAME = [Vector((x, y, z)) for x in (ANNEX_X0 - 0.4, ANNEX_X0 + ANNEX_W + 0.4)
               for y in (ANNEX_Y0 - 0.6, ANNEX_Y0 + ANNEX_D + 0.5) for z in (0, ANNEX_H + 0.95)]
# crate places (x from the annex's left, depth from its front, z), filled in order: two stacks of three as l0
CRATE = 1.2 * FURN                 # a crate's width at the furniture scale; it is 0.8 x that high
CRATES = [(2.0, 3.5, 0), (3.8, 3.8, 0), (2.9, 3.6, CRATE * 0.8), (6.0, 3.2, 0), (7.8, 3.5, 0), (6.9, 3.3, CRATE * 0.8)]


def annex(crates: int) -> dict:
    """The storehouse: an open-fronted annex right of the lift under a warm pendant, holding `crates` crates (a
    shuttered project each)."""
    x0, x1, y0, y1, h = ANNEX_X0, ANNEX_X0 + ANNEX_W, ANNEX_Y0, ANNEX_Y0 + ANNEX_D, ANNEX_H
    fr = M('shell', rough=0.5)
    span('annex_floor', x0, y0, 0, x1, y1, 0.04, M('floor', rough=0.5), bevel=0.0)
    span('annex_back', x0, y1, 0, x1, y1 + 0.3, h, M('wall_warm', rough=0.8), bevel=0.01)
    span('annex_side', x1 - 0.3, y0, 0, x1, y1, h, fr, bevel=0.02)
    span('annex_left', x0, y0, 0, x0 + 0.3, y1, h, fr, bevel=0.02)
    span('annex_roof', x0 - 0.1, y0 - 0.3, h, x1 + 0.1, y1 + 0.3, h + 0.55, fr, bevel=0.03)
    span('annex_parapet', x0 - 0.1, y0 - 0.3, h + 0.55, x1 + 0.1, y0 + 0.2, h + 0.85, M('shell_edge'), bevel=0.02)
    lamp = Vector((x0 + ANNEX_W / 2, y0 + 3.0, h - 1.6))
    put('pendant', lamp, scale=1.6)
    A.cylinder('cord', 0.015, 0.8, lamp + Vector((0, 0, 0.8)), M('frame'), segments=6)
    A.ball('bulb', 0.12, lamp + Vector((0, 0, 0.1)), glow('bulb', 15.0), kind='baked')
    A.light('annex_lamp', 'POINT', lamp - Vector((0, 0, 0.1)), 2200, PAL['warm'], shadow_soft_size=0.2)
    rnd = random.Random(5)
    for x, y, z in CRATES[:crates]:
        put('crate', (x0 + x + rnd.uniform(-0.05, 0.05), y0 + y, z), rnd.uniform(-0.08, 0.08))
    whole_building(6)
    slots = {'storehouse_door': Vector((x0 + ANNEX_W / 2, y0, 1.4)),
             'door_lantern': Vector((x0 + ANNEX_W / 2, y0 - 0.2, h - 0.4)),
             'crates': Vector((x0 + 5.0, y0 + 3.8, 1.0))}
    return {'frame': ANNEX_FRAME, 'slots': slots}


# --- rendering -------------------------------------------------------------------------------------------

def project(p: Vector) -> tuple[float, float]:
    """World point to screen metres (right, down) under the canonical projection."""
    return p.x * PX[0] + p.y * PY[0] + p.z * PZ[0], p.x * PX[1] + p.y * PY[1] + p.z * PZ[1]


def ground_point(u: float, v: float) -> Vector:
    """The point on z = 0 that projects to screen metres (u, v): where to aim the camera."""
    det = PX[0] * PY[1] - PY[0] * PX[1]
    return Vector(((u * PY[1] - PY[0] * v) / det, (PX[0] * v - u * PX[1]) / det, 0.0))


def visible_points():
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in bpy.data.objects:
        if o.type != 'MESH' or o.hide_render or not o.visible_camera or o.get('proto'):
            continue
        e = o.evaluated_get(dg)
        pts += [e.matrix_world @ Vector(c) for c in e.bound_box]
    return pts


def render(name: str, out: Path, mult: int, frame=None, opaque=False) -> dict:
    """Render the camera-visible meshes framed by `frame` (world points; default their bounds). The window is snapped
    so the anchor (the origin) lands on a pixel that stays whole at tier 1 (a multiple of `mult`)."""
    ppm = PPM_1X * mult
    us, vs = zip(*(project(p) for p in (frame or visible_points())))

    q = mult if isinstance(mult, int) else 1   # (a fractional scale is for comparisons: whole pixels are enough)

    def snap(m):
        return math.ceil((m * ppm + 2 * q) / q) * q
    left, right_, top, bottom = snap(-min(us)), snap(max(us)), snap(-min(vs)), snap(max(vs))
    w, h = left + right_, top + bottom
    s = bpy.context.scene
    s.render.film_transparent = not opaque
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = w, h, 100
    # the canonical camera centred on the window (it sets the scale from the resolution, so that comes first); far
    # enough out that nothing of the building or its plinth is behind it
    A.canonical_camera(s, ground_point((right_ - left) / 2 / ppm, (bottom - top) / 2 / ppm), ppm, name='b_cam', distance=300.0)
    p = out / f'{name}@{ppm:.0f}.png'
    s.render.filepath = str(p)
    bpy.ops.render.render(write_still=True)
    return {'mult': mult, 'file': p.name, 'size': [w, h], 'anchor_px': [left, top]}


def px(offset: Vector, mult: int) -> list[float]:
    """A world offset from the anchor to screen pixels (x right, y down) at tier `mult`."""
    u, v = project(offset)
    return [round(u * PPM_1X * mult, 2), round(v * PPM_1X * mult, 2)]


def box_px(size, mult: int) -> list[float]:
    """A w x h metre rectangle on the front face (x along the front, z up) to its screen extent in pixels."""
    w, h = size
    return [round(w * PX[0] * PPM_1X * mult, 2), round(h * -PZ[1] * PPM_1X * mult, 2)]


def bands(column: str, shot: dict, mult: int, corner: Vector) -> dict:
    """Cut a tall column render into its pieces: <column>-lobby (level 0 to 1), <column> (one storey, taken from the
    middle so it repeats) and <column>-cap (above the roof level). Each crop is whole rows; the anchor is its level's.
    The cuts follow the level rows shifted by how far the column's front `corner` lies below the level point on screen
    (the lift, at the front's far end, sits lower than the level points at x = 0); a vertical prism repeats every
    storey on screen at any shift."""
    ax, ay = shot['anchor_px']
    w, h = shot['size']
    shift = round(project(corner)[1] * PPM_1X) * mult   # (a whole tier-1 pixel, so smaller tiers cut clean)

    def row(i):
        return ay - (0 if i == 0 else (LOBBY_STEP_PX + (i - 1) * STEP_PX) * mult)
    cuts = {f'{column}-lobby': (row(1) + shift, h, row(0)), column: (row(3) + shift, row(2) + shift, row(2)),
            f'{column}-cap': (0, row(COLUMN_FLOORS + 1) + shift, row(COLUMN_FLOORS + 1))}
    return {name: {**shot, 'crop': [0, r0, w, r1], 'size': [w, r1 - r0], 'anchor_px': [ax, level - r0]}
            for name, (r0, r1, level) in cuts.items()}


FLOOR_FRAME = [Vector(p) for p in ((0, -0.2, -SLAB), (W, -0.2, -SLAB), (0, D + 0.3, H), (W, D + 0.3, H),
                                   (0, -0.2, H), (W, D + 0.3, -SLAB))]
LOBBY_FRAME = [Vector(p) for p in ((0, -0.2, -0.05), (W, -0.2, -0.05), (0, D + 0.3, LOBBY_H), (W, D + 0.3, LOBBY_H),
                                   (0, -0.2, LOBBY_H))]

PIECES = {'lobby': lobby, 'roof': roof, 'lantern-bracket': lantern_bracket, 'spine-column': spine_column,
          'lift-column': lambda: lift_column(False), 'lift-open-column': lambda: lift_column(True)}
for _kind in ('open', 'lab', 'glass', 'free'):
    for _lit in ((True, False) if _kind != 'free' else (False,)):
        for _top in (False, True):
            _name = f'floor-{_kind}' + ('-top' if _top else '') + ('' if _kind == 'free' else '-lit' if _lit else '-unlit')
            PIECES[_name] = (lambda k=_kind, l=_lit, t=_top: floor(k, l, t))
for _n in range(MAX_CRATES + 1):
    PIECES[f'annex-{_n}'] = (lambda n=_n: annex(n))
for _n in range(1, MAX_CAPACITY + 1):
    PIECES[f'plinth-{_n}'] = (lambda n=_n: plinth(n))


def stack_doc() -> dict:
    """How the view assembles the pieces, for the manifest (building_finish.py adds per-tier numbers)."""
    return {
        'origin': "the plinth's anchor: the lobby floor's front-left corner; the plinth image is the view's canvas",
        'levels': 'level 0 is the lobby; floor k (0 = first above the lobby) at origin.y - lobby_step_px - k * step_px; '
                  'the roof, spine-cap and lift-cap at the level above the top floor (k = capacity)',
        'place': 'draw each piece at (level x - anchor_px[0], level y - anchor_px[1]); slots are offsets from the level point',
        'order': ['plinth-<capacity>', 'annex-<crates>', 'spine-lobby, spine per floor, spine-cap',
                  'lantern-bracket (where a floor or the lobby has attention)', 'lobby', 'floors bottom to top', 'roof',
                  'lift-lobby or lift-open-lobby, lift or lift-open per floor, lift-cap'],
        'floor': {'open': ['floor-open-lit', 'floor-open-unlit'], 'lab': ['floor-lab-lit', 'floor-lab-unlit'], 'glass': ['floor-glass-lit', 'floor-glass-unlit'],
                  'free': ['floor-free'], 'top': 'the top floor uses the -top variant of its state (no ceiling)'},
        'annex': f'annex-<n> for n crates, n capped at {MAX_CRATES}',
        'plinth': f'plinth-<capacity>, 1..{MAX_CAPACITY}: the ground shadow is as long as the building is tall',
    }


def main() -> None:
    A.require_blender()
    argv = sys.argv[sys.argv.index('--') + 1:]
    out = Path(argv.pop(0)).resolve()
    mult, samples = 2, 32
    if '--mult' in argv:   # (a fraction renders at some other scale, e.g. l2's 171.5 px/m = 8.576, for comparison only)
        i = argv.index('--mult'); mult = float(argv[i + 1]); del argv[i:i + 2]
        mult = int(mult) if mult.is_integer() else mult
    if '--samples' in argv:
        i = argv.index('--samples'); samples = int(argv[i + 1]); del argv[i:i + 2]
    only = argv
    out.mkdir(parents=True, exist_ok=True)
    A.reset()
    studio(samples)
    load_protos()
    info = {'camera': {**PROJECTION, 'ppm_1x': PPM_1X}, 'step_px': STEP_PX,
            'lobby_step_px': LOBBY_STEP_PX, 'floor': {'w': W, 'd': D, 'f2f': F2F, 'slab': SLAB},
            'blender': bpy.app.version_string, 'stack': stack_doc(), 'pieces': {}}
    manifest = out / 'pieces.json'
    if only and manifest.exists():
        info['pieces'] = json.loads(manifest.read_text())['pieces']
    for name, build in PIECES.items():
        if only and not any(name == o or (o.endswith('*') and name.startswith(o[:-1])) for o in only):
            continue
        clear()
        spec = build()
        bpy.data.objects['front_fill'].data.energy = spec.get('fill', FRONT_FILL)
        bpy.context.scene.view_settings.exposure = spec.get('exposure', EXPOSURE)
        m = min(mult, spec.get('mult', mult))
        shot = render(name, out, m, spec.get('frame'), spec.get('opaque', False))
        shot['slots'] = {k: px(v, m) for k, v in spec.get('slots', {}).items()}
        shot['boxes'] = {k: box_px(v, m) for k, v in spec.get('boxes', {}).items()}
        shot['projection'] = PROJECTION
        if 'column' in spec:
            made = bands(spec['column'], shot, m, spec['corner'])
            if spec['column'] == 'lift-open':   # (the cap is the shut column's)
                del made['lift-open-cap']
            info['pieces'].update(made)
        else:
            info['pieces'][name] = shot
        print('PIECE', name, shot['size'], flush=True)
        manifest.write_text(json.dumps(info, indent=1))


if __name__ == '__main__':
    main()
