"""Posed hands for the robot (the user's 16 GLBs, outside the repo) as a library for motion_rig.py.

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/robot_hands.py [-- --views <dir>]

Each GLB is welded and cleaned, recoloured to the robot's palette (the cream shell black like the robot's
mitts, the black knuckle bands charcoal so the fingers stay legible, the wrist ring and any cuff as the robot's
black and teal; a held book or sheet keeps its own colours), and put in one canonical frame: the wrist ring's
centre at the origin, the fingers along +X, palm down (-Z) and thumb forward (-Y): a LEFT hand in the Mixamo
T-pose. The ring and the finger axis are found from the geometry; the roll about the finger axis and the
handedness are set per file in HANDS, checked by eye with --views. Right hands are mirrored left hands.
One scale for every hand: the fist is made FIST_TO_CUFF times as wide as the robot's forearm cuff, and the
others take the fist's wrist-ring size. Writes ~/.cache/fleet-motion-test/robot_hands.blend (hand_<pose>_<L|R>).
"""

import colorsys
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palette import PALETTE, lin  # noqa: E402

SRC = Path.home() / '.local/state/fleet/renovation/robot-hands'
OUT = Path.home() / '.cache/fleet-motion-test/robot_hands.blend'
BODY = Path.home() / '.cache/fleet-motion-test/robot_body.blend'
CHARCOAL = '#2b3131'
FIST_TO_CUFF = 0.7  # the fist's width across the knuckles over the forearm cuff's width: the sheet's compact mitts

# pose: (file, is a left hand as modelled, roll about the finger axis in degrees, which ring for two-hand models)
HANDS = {
    'open':      ('hands1/hands1-1', False, 180, None),
    'thumbs_up': ('hands1/hands1-3', True, 90, None),
    'peace':     ('hands1/hands1-5', True, 90, None),
    'ok':        ('hands1/hands1-6', True, 90, None),
    'fist':      ('hands1/hands1-9', True, 90, None),
    'cupped':    ('hands2/hands2-1', True, 180, None),
    'pinch':     ('hands2/hands2-3', True, 90, None),
    'book':      ('hands3/hands3-1', False, 90, 'min_x'),  # both hands and the book, carried by the right hand
}
# hands3-2 ('holding a sheet') is unusable: the sheet of paper was generated as a solid cube


def load(path: Path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    for o in meshes:
        o.data.transform(o.matrix_world)
        o.parent = None
        o.matrix_world = Matrix()
    ob = meshes[0]
    if len(meshes) > 1:
        with bpy.context.temp_override(active_object=ob, selected_editable_objects=meshes):
            bpy.ops.object.join()
    for o in new:
        if o.name in bpy.data.objects and o is not ob and o.type != 'MESH':
            bpy.data.objects.remove(o)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()
    return ob


def classes(ob) -> list[str]:
    me = ob.data
    mat = me.materials[0]
    base = next(l.from_node.image for l in mat.node_tree.links
                if l.to_socket.name == 'Base Color' and l.from_node.type == 'TEX_IMAGE')
    w, h = base.size
    px = np.array(base.pixels[:]).reshape(h, w, 4)
    uv = me.uv_layers.active.data
    out = []
    for p in me.polygons:
        u = sum((uv[i].uv for i in p.loop_indices), Vector((0, 0))) / len(p.loop_indices)
        c = px[min(h - 1, max(0, int(u.y * h))), min(w - 1, max(0, int(u.x * w))), :3]
        hh, s, v = colorsys.rgb_to_hsv(*c)
        if 0.44 < hh < 0.54 and s > 0.3 and v > 0.75:
            out.append('ring')
        elif 0.44 < hh < 0.54 and s > 0.45:
            out.append('teal')
        elif v < 0.3:
            out.append('black')
        elif s < 0.22 and v > 0.55:
            out.append('cream')
        else:
            out.append('prop')  # a held book or sheet: keeps its texture
    return out


def frame(ob, cls, pick):
    """Wrist ring centre, and the finger direction (from the ring towards the hand, perpendicular to the ring)."""
    # the wrist marker: the bright ring on the single hands, the teal cuff on the others. Stray cyan
    # reflections litter the shells, so take connected patches of marker faces and keep the biggest (or, on the
    # two-hand models, the robot's right hand: the viewer's left, -X)
    want = ('teal',) if any(c == 'teal' for c in cls) and sum(c == 'teal' for c in cls) > 3000 else ('ring', 'teal')
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    left = {f.index for f in bm.faces if cls[f.index] in want}
    patches = []
    while left:
        i0 = left.pop()
        group, stack = [i0], [i0]
        while stack:
            for e in bm.faces[stack.pop()].edges:
                for g in e.link_faces:
                    if g.index in left:
                        left.discard(g.index)
                        group.append(g.index)
                        stack.append(g.index)
        patches.append(group)
    centre = lambda g: np.array([ob.data.polygons[i].center for i in g])  # noqa: E731
    big = sorted(patches, key=len, reverse=True)
    if pick == 'min_x':
        best = min(big[:2], key=lambda g: centre(g)[:, 0].mean())
    else:
        best = big[0]
    bm.free()
    pts = centre(best)
    c = pts.mean(0)
    # the finger axis: from the wrist marker to the hand's own centroid (the marker's rounded stub makes a
    # plane fit unreliable); on the two-hand models only the part of the model near that wrist counts
    body = np.array([p.center for p, k in zip(ob.data.polygons, cls) if k in ('cream', 'black')])
    if pick:
        body = body[np.linalg.norm(body - c, axis=1) < 0.6]
    n = Vector(body.mean(0) - c)
    radius = float(np.median(np.linalg.norm(pts - c, axis=1)))
    return Vector(c), n.normalized(), radius


def recolour(ob, cls) -> None:
    mats = {k: bpy.data.materials.get(f'hand_{k}') for k in ('shell', 'band', 'teal')}
    for k, colour in (('shell', PALETTE['black']), ('band', CHARCOAL), ('teal', PALETTE['teal'])):
        if mats[k] is None:
            m = bpy.data.materials.new(f'hand_{k}')
            m.use_nodes = True
            b = m.node_tree.nodes['Principled BSDF']
            rgb = m.node_tree.nodes.new('ShaderNodeRGB')
            rgb.name = rgb.label = 'host_tint' if k == 'teal' else k
            rgb.outputs[0].default_value = lin(colour)
            m.node_tree.links.new(rgb.outputs[0], b.inputs['Base Color'])
            m.diffuse_color = {'shell': (0.35, 0.35, 0.38, 1), 'band': (0.08, 0.08, 0.08, 1),
                               'teal': (0.0, 0.5, 0.55, 1)}[k]  # workbench views (--views) only
            b.inputs['Roughness'].default_value = 0.3
            b.inputs['Specular IOR Level'].default_value = 0.1
            b.inputs['Coat Weight'].default_value = 0.25
            b.inputs['Coat Roughness'].default_value = 0.04
            mats[k] = m
    prop = ob.data.materials[0]
    ob.data.materials.clear()
    for m in (mats['shell'], mats['band'], mats['teal'], prop):
        ob.data.materials.append(m)
    idx = {'cream': 0, 'black': 1, 'ring': 1, 'teal': 2, 'prop': 3}
    for p, c in zip(ob.data.polygons, cls):
        p.material_index = idx[c]
        p.use_smooth = True


def canonical(ob, c, n, roll_deg, is_left) -> None:
    ob.data.transform(Matrix.Translation(-c))
    q = n.rotation_difference(Vector((1, 0, 0)))
    ob.data.transform(q.to_matrix().to_4x4())
    if not is_left:  # a right hand as modelled: reflect across the finger axis to make it a left hand
        ob.data.transform(Matrix.Scale(-1, 4, Vector((0, 1, 0))))
        ob.data.flip_normals()
    ob.data.transform(Matrix.Rotation(math.radians(roll_deg), 4, 'X'))


def cuff_width() -> float:
    with bpy.data.libraries.load(str(BODY), link=False) as (src, dst):
        dst.objects = ['body_forearm_L']
    ob = dst.objects[0]
    meta = json.loads(BODY.with_suffix('.json').read_text())['joints_m']
    a, b = Vector(meta['elbow_L']), Vector(meta['wrist_L'])
    axis = (b - a).normalized()
    pts = [Vector(v.co) for v in ob.data.vertices]
    radial = [((p - a) - axis * (p - a).dot(axis)) for p in pts]
    u = axis.orthogonal().normalized()
    v = axis.cross(u)
    wu = max(r.dot(u) for r in radial) - min(r.dot(u) for r in radial)
    wv = max(r.dot(v) for r in radial) - min(r.dot(v) for r in radial)
    bpy.data.objects.remove(ob)
    return min(wu, wv)


def main() -> None:
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    bpy.ops.wm.read_factory_settings(use_empty=True)
    cuff = cuff_width()
    hands, ring_r = {}, {}
    for pose, (file, is_left, roll, pick) in HANDS.items():
        ob = load(SRC / f'{file}.glb')
        cls = classes(ob)
        c, n, r = frame(ob, cls, pick)
        recolour(ob, cls)
        canonical(ob, c, n, roll, is_left)
        ob.name = ob.data.name = f'hand_{pose}_L'
        hands[pose], ring_r[pose] = ob, r
    # one scale: the fist FIST_TO_CUFF times the cuff's width (width across the knuckles = the Y extent, palm down).
    # hands1 (open, thumbs up, peace, OK, fist) share a wrist-ring design, so they take the fist's ring size. The
    # cuffed models (cupped, pinch, book) measure their teal cuff instead: the cupped hand is set to the fist's
    # width across the knuckles (shell only, the cuff left out), and the others take its cuff size.
    def shell_width(ob):
        ys = [ob.data.vertices[i].co.y for p in ob.data.polygons if p.material_index == 0 for i in p.vertices]
        return max(ys) - min(ys)
    fist_w = shell_width(hands['fist'])
    target_w = FIST_TO_CUFF * cuff
    ring_target = ring_r['fist'] * target_w / fist_w
    cuff_target = ring_r['cupped'] * target_w / shell_width(hands['cupped'])
    cuffed = {p for p, (f, *_rest) in HANDS.items() if not f.startswith('hands1')}
    report = {'cuff_width_m': round(cuff, 4), 'fist_width_m': round(target_w, 4)}
    for pose, ob in hands.items():
        s = (cuff_target if pose in cuffed else ring_target) / ring_r[pose]
        ob.data.transform(Matrix.Scale(s, 4))
        right = ob.copy()
        right.data = ob.data.copy()
        right.data.transform(Matrix.Scale(-1, 4, Vector((1, 0, 0))))
        right.data.flip_normals()
        right.name = right.data.name = f'hand_{pose}_R'
        bpy.context.scene.collection.objects.link(right)
        dims = ob.dimensions
        report[pose] = {'scale': round(s, 4), 'size_m': [round(d, 3) for d in dims]}
    for img in bpy.data.images:
        img.pack()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT), compress=True)
    print('HANDS', json.dumps(report))
    if '--views' in args:
        views(Path(args[args.index('--views') + 1]))


def views(out: Path) -> None:
    """Each left hand from the front (-Y), from above (+Z) and from the fingertips, on a canonical axis grid."""
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.display.shading.color_type = 'MATERIAL'
    sc.render.resolution_x = sc.render.resolution_y = 260
    sc.world = bpy.data.worlds.new('w')
    sc.world.color = (0.85, 0.85, 0.9)
    cam = bpy.data.objects.new('c', bpy.data.cameras.new('c'))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.type = 'ORTHO'
    left = [o for o in sc.objects if o.name.endswith('_L')]
    for axis, colour, off in (('+X fingers', (1, 0, 0, 1), (0.08, 0, 0)), ('-Y thumb', (0, 0.8, 0, 1), (0, -0.08, 0)),
                              ('-Z palm', (0, 0, 1, 1), (0, 0, -0.08))):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.012, location=off)
        m = bpy.data.materials.new(axis)
        m.diffuse_color = colour
        bpy.context.active_object.data.materials.append(m)
        bpy.context.active_object.name = f'marker {axis}'
    for o in sc.objects:
        o.hide_render = not o.name.startswith('marker')
    for o in left:
        o.hide_render = False
        cam.data.ortho_scale = max(o.dimensions) * 1.3
        c = Vector([(max(v.co[i] for v in o.data.vertices) + min(v.co[i] for v in o.data.vertices)) / 2
                    for i in range(3)])
        for k, (d, rot) in {'front': ((0, -1, 0), (math.pi / 2, 0, 0)), 'top': ((0, 0, 1), (0, 0, 0)),
                            'tips': ((1, 0, 0), (math.pi / 2, 0, math.pi / 2))}.items():
            cam.location = c + Vector(d) * 3
            cam.rotation_euler = rot
            sc.render.filepath = str(out / f'{o.name}__{k}.png')
            bpy.ops.render.render(write_still=True)
        o.hide_render = True


main()
