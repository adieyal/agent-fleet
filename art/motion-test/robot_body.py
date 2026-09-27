"""Build the robot from the whole-body Hunyuan3D model (outside the repo) as rigid pieces for motion_rig.py.

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/robot_body.py [-- --report <json>]

Source: ~/.local/state/fleet/renovation/robot-rebuild/teal-robot-apose.glb, generated from robot-apose-front.png
(which matches robot-sheet.png). Steps:
  1. weld the UV-seam splits, drop floating islands, recalculate normals, scale to HEIGHT with the soles on z = 0;
  2. read each face's colour from the baked texture and give it a flat sheet colour (palette.py): teal on the
     'host_tint' node, cream, black, and the glow of the eyes and chest light;
  3. cut the mesh into rigid pieces at the design's seams: head with neck, torso, waist band with pelvis, and per
     side upper arm with shoulder shell, forearm cuff, hand, thigh, shin with boot. Faces go to the nearest limb
     segment (distance over the segment's radius); the head is everything above the neck seam and cream faces
     above the waist stay with the torso. The face plate and the eyes are split off the head into their own
     objects, so other faces can replace them;
  4. add a black ball joint at each shoulder, elbow, hip and knee to close the cuts.
Writes ~/.cache/fleet-motion-test/robot_body.blend (objects named body_<piece>) and robot_body.json (joint
positions in metres in the A-pose, the pieces, and each piece's bone role).
"""

import colorsys
import json
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palette import PALETTE, lin  # noqa: E402

SRC = Path.home() / '.local/state/fleet/renovation/robot-rebuild/teal-robot-apose.glb'
OUT = Path.home() / '.cache/fleet-motion-test/robot_body.blend'
HEIGHT = 1.05  # metres: l2's robots read about 1.0-1.1 m at the floor scale
GLOSS = dict(rough=0.3, spec=0.1, coat=0.25, coat_rough=0.04)  # as robot_parts.py (calibrated with it)
EYE = '#2fd6ee'  # emissive: saturated, so the glow reads cyan rather than white

# Joints in the source model's units (z up, facing -Y, soles at z = -1.0, 1.974 tall), left side (+X); measured
# from its front and side views and the centroids of its colour regions (black upper arm, teal cuff, black thigh)
J_SRC = {
    'root': (0, 0, -0.29), 'spine': (0, 0, -0.20), 'chest': (0, 0, 0.10), 'neck': (0, 0, 0.235), 'head': (0, 0, 0.30),
    'shoulder': (0.30, 0.0, 0.10), 'elbow': (0.49, -0.01, -0.11), 'wrist': (0.57, -0.02, -0.41),
    'hand_tip': (0.60, -0.05, -0.66),
    'hip': (0.185, 0.0, -0.33), 'knee': (0.19, -0.01, -0.555), 'ankle': (0.20, 0.0, -0.82), 'toe': (0.21, -0.24, -0.95),
}
NECK_SEAM = 0.235  # everything above is head (helmet and neck)
WAIST_TOP, CROTCH, CROTCH_LOW = -0.15, -0.28, -0.46  # waist band top; crotch line; the pelvis's lowest point
# piece: (segment start joint, end joint, radius in source units, bone role in motion_rig.ROLES)
SEGMENTS = {
    'torso': ('spine', 'neck', 0.28, 'spine3'),
    'pelvis': ('root', 'spine', 0.24, 'root'),
    'upper_arm': ('shoulder', 'elbow', 0.13, 'arm'),
    'forearm': ('elbow', 'wrist', 0.15, 'fore'),
    'hand': ('wrist', 'hand_tip', 0.12, 'hand'),
    'thigh': ('hip', 'knee', 0.11, 'thigh'),
    'shin': ('knee', 'toe', 0.20, 'shin'),
}
BALLS = {'shoulder': (0.075, 'arm'), 'elbow': (0.07, 'fore'), 'hip': (0.08, 'thigh'), 'knee': (0.08, 'shin')}


def srcj(name: str, side: int = 1) -> Vector:
    x, y, z = J_SRC[name]
    return Vector((x * side, y, z))


def weld_and_clean(ob) -> dict:
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    n0 = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    welded = n0 - len(bm.verts)
    # floating islands: keep only islands with at least 1% of the faces
    left, islands = set(bm.faces), []
    while left:
        f0 = left.pop()
        group, stack = {f0}, [f0]
        while stack:
            for e in stack.pop().edges:
                for g in e.link_faces:
                    if g in left:
                        left.discard(g)
                        group.add(g)
                        stack.append(g)
        islands.append(group)
    small = [g for g in islands if len(g) < 0.01 * len(bm.faces)]
    bmesh.ops.delete(bm, geom=[f for g in small for f in g], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    boundary = sum(e.is_boundary for e in bm.edges)
    bm.to_mesh(ob.data)
    bm.free()
    return {'welded_verts': welded, 'islands': len(islands), 'floaters_removed': len(small),
            'faces_removed': sum(len(g) for g in small), 'boundary_edges_after': boundary}


def face_classes(ob) -> list[str]:
    """teal / cream / black / glow per face, from the base-colour texture at the face's UV centre."""
    me = ob.data
    nodes = me.materials[0].node_tree.nodes
    base = next(l.from_node.image for l in me.materials[0].node_tree.links
                if l.to_socket.name == 'Base Color' and l.from_node.type == 'TEX_IMAGE')
    w, h = base.size
    px = np.array(base.pixels[:]).reshape(h, w, 4)
    uv = me.uv_layers.active.data
    out = []
    for p in me.polygons:
        u = sum((uv[i].uv for i in p.loop_indices), Vector((0, 0))) / len(p.loop_indices)
        c = px[min(h - 1, max(0, int(u.y * h))), min(w - 1, max(0, int(u.x * w))), :3]
        hh, s, v = colorsys.rgb_to_hsv(*c)
        if v < 0.22:
            out.append('black')
        elif 0.42 < hh < 0.58 and s > 0.45 and v < 0.8:
            out.append('teal')
        elif 0.42 < hh < 0.58 and s > 0.2:
            out.append('glow')
        else:
            out.append('cream' if s < 0.25 else 'teal' if 0.4 < hh < 0.6 else 'cream')
    del nodes
    return out


def smooth_classes(ob, cls: list[str], rounds: int = 2) -> list[str]:
    """Majority filter over edge neighbours: the texture read per face is noisy at colour borders."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    nb = [[g.index for e in f.edges for g in e.link_faces if g.index != f.index] for f in bm.faces]
    bm.free()
    for _ in range(rounds):
        new = list(cls)
        for i, ns in enumerate(nb):
            votes = {}
            for j in ns:
                votes[cls[j]] = votes.get(cls[j], 0) + 1
            best = max(votes, key=votes.get) if votes else cls[i]
            if best != cls[i] and votes[best] > len(ns) / 2 and cls[i] != 'glow':
                new[i] = best
        cls = new
    return cls


def seg_dist(p: Vector, a: Vector, b: Vector) -> float:
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


def piece_of(p: Vector, cls: str) -> str:
    """The design's seams, in source units: neck seam, top of the waist band, knee; the arm chain by segment."""
    ax, side = abs(p.x), 'L' if p.x > 0 else 'R'
    if p.z > NECK_SEAM:
        return 'head'
    if cls == 'black' and ax > 0.33 and -0.70 < p.z < -0.43:  # the black hand below the teal cuff
        return f'hand_{side}'
    if (ax > 0.29 and p.z >= -0.50) or (ax > 0.24 and p.z > -0.05 and cls != 'cream'):  # cuff rim included
        # along the arm: the shoulder shell and black upper arm, then the teal cuff from the elbow down
        sx = 1 if p.x > 0 else -1
        a, b = srcj('shoulder', sx), srcj('elbow', sx)
        t = (p - a).dot(b - a) / (b - a).length_squared
        if t < 0.8 or (t < 1.15 and cls == 'black'):
            return f'upper_arm_{side}'
        return f'forearm_{side}'
    if ax > 0.28 and -0.75 < p.z < -0.43:  # below the cuff: cuff-rim scraps or the boot's top, whichever is nearer
        sx = 1 if p.x > 0 else -1
        fore = seg_dist(p, srcj('elbow', sx), srcj('wrist', sx)) / SEGMENTS['forearm'][2]
        shin = seg_dist(p, srcj('knee', sx), srcj('toe', sx)) / SEGMENTS['shin'][2]
        if fore < shin:
            return f'forearm_{side}'
    if p.z > WAIST_TOP:
        return 'torso'
    if p.z > CROTCH or (p.z > CROTCH_LOW and (cls == 'cream' or ax < 0.06)):  # boot highlights read cream too
        return 'pelvis'
    return f'thigh_{side}' if p.z > J_SRC['knee'][2] + 0.01 else f'shin_{side}'


def head_layer(p: Vector, cls: str) -> str:
    """The face plate: the black visor front inside the helmet opening, eye glow included (the eyes are
    rebuilt as their own layer)."""
    # inside the teal rim: an ellipse over the helmet opening, whatever the (noisy) texture says
    if p.y < -0.15 and (p.x / 0.345) ** 2 + ((p.z - 0.562) / 0.185) ** 2 < 1.0:
        return 'faceplate'
    return 'head'


def eye_marks(ob, cls) -> list:
    """Centre and size (x, z extents) of each eye's glow on the face plate, in source units."""
    out = []
    for sx in (1, -1):
        pts = [p.center for p, c in zip(ob.data.polygons, cls)
               if c == 'glow' and p.center.y < -0.15 and 0.35 < p.center.z < 0.75 and 0 < p.center.x * sx < 0.35]
        lo = Vector([min(q[i] for q in pts) for i in range(3)])
        hi = Vector([max(q[i] for q in pts) for i in range(3)])
        out.append(((lo + hi) / 2, hi - lo))
    # the design is symmetric: one size, mirrored centres
    (c0, s0), (c1, s1) = out
    c = Vector((abs(c0.x) + abs(c1.x), c0.y + c1.y, c0.z + c1.z)) / 2
    size = (s0 + s1) / 2
    return [(Vector((c.x, c.y, c.z)), size), (Vector((-c.x, c.y, c.z)), size)]


def material(name: str, kind: str) -> bpy.types.Material:
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']
    b.inputs['Roughness'].default_value = GLOSS['rough']
    b.inputs['Specular IOR Level'].default_value = GLOSS['spec']
    b.inputs['Coat Weight'].default_value = GLOSS['coat']
    b.inputs['Coat Roughness'].default_value = GLOSS['coat_rough']
    rgb = nt.nodes.new('ShaderNodeRGB')
    colour = {'teal': 'teal', 'cream': 'cream', 'black': 'black', 'glow': 'glow', 'faceplate': 'black'}[kind]
    rgb.outputs[0].default_value = lin(PALETTE[colour])
    rgb.name = rgb.label = 'host_tint' if kind == 'teal' else colour
    nt.links.new(rgb.outputs[0], b.inputs['Base Color'])
    if kind == 'glow':
        nt.links.new(rgb.outputs[0], b.inputs['Emission Color'])
        rgb.outputs[0].default_value = lin(EYE)  # the lit cyan of the sheet's eyes and chest light
        b.inputs['Emission Strength'].default_value = 2.5
    if kind == 'faceplate':  # the glossy black visor glass
        b.inputs['Roughness'].default_value = 0.08
        b.inputs['Coat Weight'].default_value = 1.0
    return m


KINDS = ['teal', 'cream', 'black', 'glow', 'faceplate']


def split(ob, label: list[str]) -> dict:
    """One object per label, sharing the source's materials."""
    out = {}
    for name in sorted(set(label)):
        me = ob.data.copy()
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if label[f.index] != name], context='FACES')
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(f'body_{name}', me)
        bpy.context.scene.collection.objects.link(o)
        out[name] = o
    return out


def ball(name: str, at: Vector, r: float) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=r, matrix=Matrix.Translation(at))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(material('mat_black', 'black'))
    o = bpy.data.objects.new(name, me)  # at the origin, like every piece: the mesh holds the position
    bpy.context.scene.collection.objects.link(o)
    return o


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(SRC))
    ob = next(o for o in bpy.context.scene.objects if o.type == 'MESH')
    ob.data.transform(ob.matrix_world)
    ob.parent = None
    ob.matrix_world = Matrix()
    report = {'clean': weld_and_clean(ob)}
    cls = face_classes(ob)
    eyes_src = eye_marks(ob, cls)
    cls = smooth_classes(ob, cls)
    # labels in source units, then scale: soles to z = 0, pelvis over the origin
    label = []
    for p, c in zip(ob.data.polygons, cls):
        piece = piece_of(p.center, c)
        label.append(head_layer(p.center, c) if piece == 'head' else piece)
    zmin = min(v.co.z for v in ob.data.vertices)
    zmax = max(v.co.z for v in ob.data.vertices)
    s = HEIGHT / (zmax - zmin)
    to_m = Matrix.Scale(s, 4) @ Matrix.Translation((0, 0, -zmin))
    ob.data.transform(to_m)
    # flat sheet colours: one material slot per colour class
    ob.data.materials.clear()
    for k in KINDS:
        ob.data.materials.append(material(f'mat_{k}' if k != 'teal' else 'mat_teal', k))
    for p, c, lab in zip(ob.data.polygons, cls, label):
        p.material_index = KINDS.index('faceplate' if lab == 'faceplate' else 'glow' if lab == 'eyes' else c)
        p.use_smooth = True
    pieces = split(ob, label)
    bpy.data.objects.remove(ob)
    # the eyes: the texture's glow is ragged at this mesh density, so each eye is a clean rounded capsule
    # standing just proud of the face plate where the glow was (their own layer: other eyes can replace them)
    bm = bmesh.new()
    for c, size in eyes_src:
        m = Matrix.Translation(to_m @ c + Vector((0, -0.004, 0))) @ Matrix.Diagonal(
            (size.x * s * 0.5, 0.012, size.z * s * 0.5, 1))
        bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0, matrix=m)
    me = bpy.data.meshes.new('body_eyes')
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(material('mat_glow', 'glow'))
    pieces['eyes'] = bpy.data.objects.new('body_eyes', me)
    bpy.context.scene.collection.objects.link(pieces['eyes'])
    joints = {k: list(to_m @ srcj(k)) for k in J_SRC}
    for side, sx in (('L', 1), ('R', -1)):
        for k in ('shoulder', 'elbow', 'wrist', 'hand_tip', 'hip', 'knee', 'ankle', 'toe'):
            joints[f'{k}_{side}'] = list(to_m @ srcj(k, sx))
    for k in ('shoulder', 'elbow', 'wrist', 'hand_tip', 'hip', 'knee', 'ankle', 'toe'):
        del joints[k]
    for k, (r, role) in BALLS.items():
        for side in 'LR':
            pieces[f'ball_{k}_{side}'] = ball(f'body_ball_{k}_{side}', Vector(joints[f'{k}_{side}']), r * s)
    roles = {'head': 'head', 'faceplate': 'head', 'eyes': 'head'}
    for key, (_, _, _, role) in SEGMENTS.items():
        for side in 'LR':
            roles[f'{key}_{side}'] = role
    roles |= {'torso': 'spine3', 'pelvis': 'root'}
    for k, (_, role) in BALLS.items():
        for side in 'LR':
            roles[f'ball_{k}_{side}'] = role
    counts = {k: len(o.data.polygons) for k, o in pieces.items()}
    report |= {'scale_m_per_unit': round(s, 4), 'height_m': HEIGHT, 'faces': counts}
    meta = {'height_m': HEIGHT, 'joints_m': {k: [round(c, 4) for c in v] for k, v in joints.items()},
            'pieces': {k: {'role': roles[k], 'side': k[-1] if k[-2:] in ('_L', '_R') else None} for k in pieces}}
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT), compress=True)
    OUT.with_suffix('.json').write_text(json.dumps(meta, indent=1))
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if '--report' in args:
        Path(args[args.index('--report') + 1]).write_text(json.dumps(report, indent=1))
    print('REPORT', json.dumps(report))
    print('SAVED', OUT)


main()
