"""The floor kit's props from the 3D models (floor review 2): every prop that exists as a GLB is rendered in Blender
with the one world camera, so it is aligned with the walls and the floor grid by construction.

    blender -b --factory-startup -P art/scripts/render_props.py -- MODELS OUT_DIR [--survey] [prop ...]

MODELS is the model library (~/.cache/agent-fleet-assets/models on carbon: a GLB and its source crop per object,
not committed). Each prop is imported, normalised to its real size and turned to face the camera (or, backed
against a wall, turned to the wall's axis), decimated if heavy, and rendered like build_kit.py's pieces (same
studio, camera and tiers). The contact shadow is a separate render: the prop a holdout, casting onto
shadow catchers. OUT_DIR gets <prop>@<ppm>.png, <prop>.shadow@<ppm>.png and props.json (per prop: tiers and shadow
tiers with size and anchor pixel, footprint, slots, the model it came from). art/kit/finish.py makes them kit sprites.

--survey instead renders every GLB under MODELS as imported (no turn, no scaling) at a low density, and writes
survey.json with each one's size and face count: for choosing the turns and sizes below.

World coordinates as build_kit.py: x along the back wall, y towards it, z up, metres. A prop's anchor is its base
centre; a wall-backed prop's anchor is the middle of its back on the wall face (y = 0), so it is placed on the wall.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402
import build_kit as K  # noqa: E402

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

MAX_FACES = 150_000   # decimate heavier meshes to this: the sprite is a few hundred pixels across


# --- loading ------------------------------------------------------------------------------------

def load(path: Path, name: str) -> bpy.types.Object:
    """Import a GLB as one mesh object at the origin, transforms applied."""
    before = {o.name for o in bpy.data.objects}
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [bpy.data.objects[n] for n in {o.name for o in bpy.data.objects} - before]
    meshes = [o for o in new if o.type == 'MESH']
    if not meshes:
        sys.exit(f'props: {path} has no mesh')
    bpy.ops.object.select_all(action='DESELECT')
    for o in meshes:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    for o in new:
        if o.name in bpy.data.objects and o is not ob:
            bpy.data.objects.remove(o)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    ob.name = name
    return ob


def faces(ob) -> int:
    return len(ob.data.polygons)


def bounds(ob) -> tuple[Vector, Vector]:
    co = [ob.matrix_world @ v.co for v in ob.data.vertices]
    return (Vector((min(c.x for c in co), min(c.y for c in co), min(c.z for c in co))),
            Vector((max(c.x for c in co), max(c.y for c in co), max(c.z for c in co))))


# --- survey -------------------------------------------------------------------------------------

def survey(models: Path, out: Path) -> None:
    K.studio()
    s = bpy.context.scene
    s.cycles.samples = 24
    info = {}
    for glb in sorted(models.rglob('*.glb')):
        K.clear()
        rel = str(glb.relative_to(models).with_suffix(''))
        slug = rel.replace('/', '__').replace(' ', '-')
        ob = load(glb, slug)
        lo, hi = bounds(ob)
        size = hi - lo
        # (as imported, scaled to fit a 2 m box so every model shows at a comparable size)
        k = 2.0 / max(size)
        ob.scale = (k, k, k)
        ob.location = -Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)) * k
        bpy.context.view_layer.update()
        K.floor_catcher()
        saved = K.TIERS
        K.TIERS = (0.35,)
        tiers = K.render(slug, out, Vector((0, 0, 0)), [], 0.05)
        K.TIERS = saved
        info[rel] = {'size': [round(v, 3) for v in size], 'faces': faces(ob), 'file': tiers[0]['file']}
        print('SURVEY', rel, info[rel]['size'], info[rel]['faces'], flush=True)
    (out / 'survey.json').write_text(json.dumps(info, indent=1))


# --- props --------------------------------------------------------------------------------------
# A part is a model placed in the prop: `size` scales it uniformly so its extent along one axis ('x' width, 'y'
# depth, 'z' height) is that many metres; `turn` (degrees about z) turns its front to the camera (the models' fronts
# face -y, towards the viewer, unless noted); `at` is where its base centre goes, or with back=True the middle of
# its back (its +y side) at floor level: flush against a wall face at y = 0.

def part(glb, size, turn=0.0, at=(0, 0, 0), back=False, backrest=None):
    """backrest: turn the model so its tall part (a chair's back) lies towards this world direction ('+y' or
    '-y') from its centre, whatever way the model faces; `turn` is then ignored."""
    return {'glb': glb, 'size': size, 'turn': turn, 'at': at, 'back': back, 'backrest': backrest}


def top_centroid(ob, share=0.3) -> Vector:
    """The mean of the vertices in the top `share` of the object's height."""
    lo, hi = bounds(ob)
    cut = hi.z - share * (hi.z - lo.z)
    pts = [ob.matrix_world @ v.co for v in ob.data.vertices]
    pts = [q for q in pts if q.z >= cut]
    return sum(pts, Vector()) / len(pts)


def place(models: Path, p: dict, name: str) -> bpy.types.Object:
    ob = load(models / f"{p['glb']}.glb", name)
    turn = p['turn']
    if p['backrest']:
        lo, hi = bounds(ob)
        c = top_centroid(ob) - (lo + hi) / 2
        want = 90 if p['backrest'] == '+y' else -90
        turn = round(want - math.degrees(math.atan2(c.y, c.x)))
        print('TURN', p['glb'], turn, flush=True)
    ob.data.transform(Matrix.Rotation(math.radians(turn), 4, 'Z'))
    lo, hi = bounds(ob)
    axis, metres = p['size']
    k = metres / (hi - lo)['xyz'.index(axis)]
    ob.data.transform(Matrix.Scale(k, 4))
    lo, hi = bounds(ob)
    base = Vector(((lo.x + hi.x) / 2, hi.y if p['back'] else (lo.y + hi.y) / 2, lo.z))
    ob.data.transform(Matrix.Translation(Vector(p['at']) - base))
    if faces(ob) > MAX_FACES:
        mod = ob.modifiers.new('decimate', 'DECIMATE')
        mod.ratio = MAX_FACES / faces(ob)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return ob


WOOD, STEEL = '#c9a57a', '#4b4c52'   # the desk top and legs: the AI bench's oak and dark steel, as l2
DESK_Z, TOP_T, LEG = 0.74, 0.035, 0.045


def desk(models: Path, w: float, d: float, x0: float, y1: float, pedestal_x: float, h=DESK_Z) -> None:
    """A desk top w x d on four steel legs, far edge at y1, left end at x0, with a drawer pedestal (the drawers
    model) under it on the near side. Exact boxes, so its edges run along the world axes."""
    top = A.material('prop_wood', WOOD, rough=0.55)
    steel = A.material('prop_steel', STEEL, rough=0.45, metal=0.6)
    A.box('top', (w, d, TOP_T), (x0 + w / 2, y1 - d / 2, h - TOP_T), top, bevel=0.006)
    inset = 0.04
    for lx in (x0 + inset + LEG / 2, x0 + w - inset - LEG / 2):
        for ly in (y1 - inset - LEG / 2, y1 - d + inset + LEG / 2):
            A.box(f'leg_{lx:.2f}_{ly:.2f}', (LEG, LEG, h - TOP_T), (lx, ly, 0), steel, bevel=0.004)
        A.box(f'rail_{lx:.2f}', (LEG * 0.8, d - 2 * inset, 0.05), (lx, y1 - d / 2, 0.08), steel, bevel=0.003)
    A.box('apron', (w - 2 * inset, 0.025, 0.07), (x0 + w / 2, y1 - inset, h - TOP_T - 0.07), steel, bevel=0.003)
    place(models, part('objects/drawers', ('z', 0.6), at=(pedestal_x, y1 - d + 0.3, 0)), 'pedestal')


def desk_module(models: Path):
    """One desk of a bench (1.6 x 0.8 m, top at 0.74 m); benches are modules end to end. Anchor: the desk top's
    far edge at the module's left end, as the AI pieces were (so layout.js is unchanged)."""
    desk(models, 1.6, 0.8, 0.0, 0.0, pedestal_x=0.36)
    # (anchor at the top's far-left corner: the module is modelled with its floor at z = -0.74)
    for o in bpy.data.objects:
        if o.type == 'MESH':
            o.location.z -= DESK_Z
    slots = {'seat': [0.8, 0.3, -0.27], 'lamp': [0.18, -0.12, 0], 'desk_top': [0.8, -0.4, 0], 'floor_centre': [0.8, -0.4, -0.74]}
    return {'footprint': [0, -0.82, -DESK_Z, 1.6, 0, 0], 'slots': slots, 'module_m': 1.6,
            'anchor': "desk top, far edge, at the module's left end", 'from': 'objects/drawers + boxes', 'on': 'floor'}


def question_desk(models: Path):
    """The question desk: a short desk with a pedestal and a "?" tent card on it; the lantern hangs over it."""
    desk(models, 1.2, 0.7, -0.6, 0.35, pedestal_x=0.3)
    card = A.material('prop_card', '#f4f3f0', rough=0.7)
    ink = A.material('prop_ink', '#3d4450', rough=0.6)
    for i, s in enumerate((1, -1)):   # a tent: two leaves leaning together
        A.box(f'card_{i}', (0.2, 0.006, 0.15), (0.1, 0.05 + s * 0.03, DESK_Z), card, bevel=0.002, rot=(math.radians(-s * 12), 0, 0))
    A.text_mesh('mark', '?', 0.11, (0.1, 0.05 - 0.03 - 0.02, DESK_Z + 0.08), ink, rot=(math.radians(90 + 12), 0, 0))
    return {'footprint': [-0.6, -0.35, 0, 0.6, 0.35, 0.9], 'slots': {'lantern': [0, 0.1, 2.0], 'card': [0.1, 0.05, 0.85]},
            'from': 'objects/drawers + boxes', 'on': 'floor'}


PROPS = {
    # --- furniture on the floor ---
    'desk-module': desk_module,
    'question-desk': question_desk,
    'chair-back': dict(parts=[part('objects/chair', ('z', 1.0), backrest='-y')], doc='office chair, near side of a desk, seen from behind'),
    'chair-front': dict(parts=[part('objects/chair', ('z', 1.0), backrest='+y')], doc='office chair, far side of a desk, facing the viewer'),
    'shelf': dict(parts=[part('project fixtures/bookcase', ('z', 1.8), back=True)], wall='back', doc='bookcase with binders, against the back wall'),
    'shelf-low': dict(parts=[part('project fixtures/low bookcase with files', ('z', 1.2), back=True)], wall='back', doc='low bookcase with files, against the back wall'),
    'book-cart': dict(parts=[part('project fixtures/book cart', ('x', 0.9))], doc="the librarian's cart"),
    'whiteboard': dict(parts=[part('project fixtures/whiteboard on wheels', ('z', 1.9), back=True)], wall='back', doc='the briefing board on castors, backed against the wall'),
    'podium': dict(parts=[part('project fixtures/lectern with clipboard', ('z', 1.15))], doc="the orchestrator's lectern"),
    'plant-tall': dict(parts=[part('plants and rugs/plant in square pot', ('z', 1.3))], doc='plant in a square pot'),
    'plant-bush': dict(parts=[part('plants and rugs/plant in round pot', ('z', 1.0))], doc='plant in a round pot'),
    'plant-small': dict(parts=[part('objects/pot plant', ('z', 0.42))]),
    'floor-lamp': dict(parts=[part('plants and rugs/tripod floor lamp', ('z', 1.35))], doc='tripod floor lamp (its light is a glow sprite)'),
    'crate': dict(parts=[part('waiting area/hourglass crate', ('x', 0.7))], doc='the waiting crate, hourglass on its front'),
    'crate-stack': dict(parts=[
        part('garage/crate', ('x', 0.68), at=(-0.36, 0.26, 0)), part('garage/crate', ('x', 0.68), at=(0.36, 0.26, 0)),
        part('garage/crate', ('x', 0.68), turn=90, at=(-0.34, -0.3, 0)), part('garage/crate', ('x', 0.68), at=(0.38, -0.28, 0)),
        part('garage/crate', ('x', 0.68), turn=90, at=(-0.3, 0.24, 0.56)), part('garage/crate', ('x', 0.68), at=(0.36, 0.2, 0.56)),
        part('garage/crate', ('x', 0.68), at=(-0.02, 0.22, 1.1))], doc='crates stacked: storage furniture (the hourglass crate means waiting)'),
    # (turned to the left wall's axis: its back on the wall face x = 0)
    'crate-shelf': dict(parts=[part('garage/shelf of crates', ('z', 2.0), back=True)], wall='left', doc='shelf of crates, against the left wall'),
    # --- on the walls ---
    'wall-light': dict(parts=[part('plants and rugs/wall light', ('z', 0.6), back=True)], wall='back', on='wall',
                       doc='wall light; anchor the middle of its back at its foot (placed up the wall)'),
    # --- on a desk (shadow merged into the sprite: there is no floor under them to lay it on) ---
    'monitor': dict(parts=[part('objects/monitor', ('x', 0.55), at=(0, 0.1, 0)), part('coffee/keyboard', ('x', 0.42), at=(-0.04, -0.14, 0)),
                           part('coffee/mouse', ('y', 0.1), at=(0.26, -0.14, 0))], on='desk', doc='monitor, keyboard and mouse'),
    'laptop': dict(parts=[part('objects/laptop', ('x', 0.33))], on='desk'),
    'lamp': dict(parts=[part('objects/desk lamp', ('z', 0.46))], on='desk', doc='desk lamp, switched off (its light is glow-* sprites)'),
    'pen-pot': dict(parts=[part('objects/pencil holder', ('z', 0.18))], on='desk'),
    'paper-stack': dict(parts=[part('paper/stack of papers', ('x', 0.3))], on='desk'),
    'sketch': dict(parts=[part('paper/diagram sheet', ('x', 0.32))], on='desk'),
    'mug': dict(parts=[part('coffee/mug', ('z', 0.1))], on='desk'),
    'desk-plant': dict(parts=[part('objects/pot plant', ('z', 0.16))], on='desk'),
    'books': dict(parts=[part('objects/notebook', ('x', 0.24))], on='desk', doc='a notebook'),
    'paper-tray': dict(parts=[part('objects/paper tray', ('x', 0.34))], on='desk'),
    # --- the lantern: anchored at its centre, hanging ---
    'lantern': dict(parts=[part('status effects/lantern lit', ('z', 0.66), at=(0, 0, -0.33))], on='air',
                    doc='the attention lantern: anchor at its centre; the front facet takes the glyph'),
}


def build(models: Path, name: str):
    spec = PROPS[name]
    if callable(spec):
        return spec(models)
    for i, p in enumerate(spec['parts']):
        place(models, p, f'{name}_{i}')
    objs = [o for o in bpy.data.objects if o.type == 'MESH' and not o.is_shadow_catcher]
    lo = Vector([min(bounds(o)[0][i] for o in objs) for i in range(3)])
    hi = Vector([max(bounds(o)[1][i] for o in objs) for i in range(3)])
    info = {'footprint': [round(v, 3) for v in (*lo, *hi)], 'from': ' + '.join(sorted({p['glb'] for p in spec['parts']})),
            'on': spec.get('on', 'floor'), **({'doc': spec['doc']} if spec.get('doc') else {})}
    if spec.get('wall') == 'left':
        # modelled against a back wall (its back on y = 0), then turned a quarter so its back is on x = 0
        for o in objs:
            o.data.transform(Matrix.Rotation(math.radians(90), 4, 'Z'))
        lo2 = Vector([min(bounds(o)[0][i] for o in objs) for i in range(3)])
        hi2 = Vector([max(bounds(o)[1][i] for o in objs) for i in range(3)])
        info['footprint'] = [round(v, 3) for v in (*lo2, *hi2)]
    if spec.get('wall'):
        info['wall'] = spec['wall']
    if name == 'lamp':   # the shade: the head at the arm's end (the lamp's top part), its mouth a little lower
        c = top_centroid(objs[0], 0.25)
        info['slots'] = {'shade': [round(c.x, 3), round(c.y, 3), round(c.z - 0.04, 3)]}
    if name == 'lantern':
        info['slots'] = {'glyph': [0, round(lo.y * 0.55, 3), 0.0], 'cable_top': [0, 0, 1.2]}
    return info


def render_prop(models: Path, out: Path, name: str) -> dict:
    K.clear()
    info = build(models, name)
    objs = [o for o in bpy.data.objects if o.type == 'MESH' and not o.is_shadow_catcher]
    anchor = Vector((0, 0, 0))
    info['tiers'] = K.render(name, out, anchor, [], 0.03)
    # the contact shadow on its own: the prop casting onto the floor it stands on (and
    # the wall behind it); framed wide enough for the shadow to fade out inside the image
    if info['on'] == 'air':   # (the lantern hangs: no shadow)
        return info
    # (a holdout: the prop cuts itself out of the shadow render, so the wall straight behind a wall-backed prop is not
    # a black silhouette; the shadow shows only around it and through its gaps)
    for o in objs:
        o.is_holdout = True
    fp = info['footprint']
    height = fp[5] - fp[2]
    if info['on'] != 'wall':   # (mounted on the wall: nothing under it to catch a shadow)
        K.floor_catcher(fp[2])
    if info.get('wall') == 'back':
        K.wall_catcher(0.0)
    if info.get('wall') == 'left':
        K.catcher('catcher_left', (-0.001, 0, 0), (0, math.pi / 2, 0))
    info['shadow'] = K.render(name + '.shadow', out, anchor, [], 0.5 + 1.1 * height)
    for o in objs:
        o.is_holdout = False
    return info


def main() -> None:
    A.require_blender()
    args = sys.argv[sys.argv.index('--') + 1:]
    models, out = Path(args[0]).expanduser().resolve(), Path(args[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    A.reset()
    if '--survey' in args:
        survey(models, out)
        return
    only = [a for a in args[2:] if not a.startswith('--')]
    K.studio()
    info = {'camera': {'pitch': K.PITCH, 'yaw': K.YAW}, 'blender': bpy.app.version_string, 'props': {}}
    if only and (out / 'props.json').exists():   # re-rendering some props keeps the others
        info['props'] = json.loads((out / 'props.json').read_text())['props']
    for name in PROPS:
        if only and name not in only:
            continue
        info['props'][name] = render_prop(models, out, name)
        print('PROP', name, [t['size'] for t in info['props'][name]['tiers']], flush=True)
        (out / 'props.json').write_text(json.dumps(info, indent=1))


if __name__ == '__main__':
    main()
