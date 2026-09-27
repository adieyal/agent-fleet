"""Build the robot from the whole-body Hunyuan3D model (outside the repo) as rigid pieces for motion_rig.py.

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/robot_body.py [-- --report <json>]

Source: ~/.local/state/fleet/renovation/robot-rebuild/teal-robot-apose.glb, generated from robot-apose-front.png
(which matches robot-sheet.png). Steps:
  1. weld the UV-seam splits, drop floating islands, recalculate normals, subdivide once (so colour borders have
     vertices to run along), scale to the height in robot_scale.json with the soles on z = 0;
  2. colour regions: each vertex reads the baked texture and votes teal / cream / black / glow; the votes are
     smoothed over the mesh, so each border is a smooth iso-line rather than a staircase of faces. One material
     draws them with flat sheet colours (palette.py) through a sharp threshold, teal on the 'host_tint' node;
  3. cut the mesh into rigid pieces at the design's seams: head with neck, torso, waist band with pelvis, and per
     side upper arm with shoulder shell, forearm cuff, thigh, shin with boot (the model's hands are dropped:
     robot_hands.py replaces them). Loose fragments are deleted, every cut rim is smoothed and capped in black;
  4. model the parts the scan draws too roughly: the face plate (a glossy black shell fitted to the helmet
     opening), the eyes, and the ear discs (black centre, cyan ring, cream ring), each its own object;
  5. close each cut with a black ball joint sized to cover its seam: neck, shoulders, elbows, hips, knees.
Writes ~/.cache/fleet-motion-test/robot_body.blend (objects body_<piece>) and robot_body.json (joint positions in
metres in the A-pose, the pieces and each piece's bone role).
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
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palette import PALETTE, lin  # noqa: E402

HERE = Path(__file__).resolve().parent
SRC = Path.home() / '.local/state/fleet/renovation/robot-rebuild/teal-robot-apose.glb'
OUT = Path.home() / '.cache/fleet-motion-test/robot_body.blend'
SCALE_FILE = HERE / 'robot_scale.json'  # {"height_m": ...}, solved by render_motion.py fit against l2
HEIGHT = json.loads(SCALE_FILE.read_text())['height_m'] if SCALE_FILE.exists() else 1.05
GLOSS = dict(rough=0.3, spec=0.1, coat=0.25, coat_rough=0.04)
EYE = '#2fd6ee'  # emissive: saturated, so the glow reads cyan rather than white
CLASSES = ('teal', 'cream', 'black', 'glow')

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
SOLE_TOP = -0.95   # the boot's black sole band
ANKLE_CREASE = (-0.83, -0.78)  # the black line where the boot's foot meets its shin
# the helmet opening (face plate) as a superellipse in x, z; and the ear discs (measured from the colour regions)
PLATE = dict(a=0.305, zc=0.551, b=0.194, n=3.2)
EAR = dict(c=(0.436, -0.04, 0.565), r_black=0.10, r_glow=0.122, r_cream=0.185, r_rim=0.198)
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
# ball joints: the pieces meeting there, the smallest radius (source units), the bone it rides on
BALLS = {
    'neck': (('head', 'torso'), 0.07, 'neck', False),
    'shoulder': (('torso', 'upper_arm'), 0.075, 'arm', True),
    'elbow': (('upper_arm', 'forearm'), 0.07, 'fore', True),
    'hip': (('pelvis', 'thigh'), 0.08, 'thigh', True),
    'knee': (('thigh', 'shin'), 0.08, 'shin', True),
}
BALL_MAX = 0.13


def srcj(name: str, side: int = 1) -> Vector:
    x, y, z = J_SRC[name]
    return Vector((x * side, y, z))


def islands(bm) -> list:
    left, out = set(bm.faces), []
    while left:
        f0 = left.pop()
        group, stack = [f0], [f0]
        while stack:
            for e in stack.pop().edges:
                for g in e.link_faces:
                    if g in left:
                        left.discard(g)
                        group.append(g)
                        stack.append(g)
        out.append(group)
    return out


def weld_and_clean(ob) -> dict:
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    n0 = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    welded = n0 - len(bm.verts)
    parts = islands(bm)
    small = [g for g in parts if len(g) < 0.01 * len(bm.faces)]
    bmesh.ops.delete(bm, geom=[f for g in small for f in g], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    boundary = sum(e.is_boundary for e in bm.edges)
    bm.to_mesh(ob.data)
    bm.free()
    return {'welded_verts': welded, 'islands': len(parts), 'floaters_removed': len(small),
            'boundary_edges_after': boundary}


def apply_mod(ob, kind: str, **props) -> None:
    m = ob.modifiers.new(kind.lower(), kind)
    for k, v in props.items():
        setattr(m, k, v)
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.modifier_apply(modifier=m.name)


def vertex_weights(ob) -> np.ndarray:
    """Per vertex, one vote per class from the base-colour texture (averaged over the vertex's UV corners)."""
    me = ob.data
    mat = me.materials[0]
    base = next(l.from_node.image for l in mat.node_tree.links
                if l.to_socket.name == 'Base Color' and l.from_node.type == 'TEX_IMAGE')
    w, h = base.size
    px = np.array(base.pixels[:], dtype=np.float32).reshape(h, w, 4)[:, :, :3]
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get('uv', uv)
    uv = uv.reshape(-1, 2)
    lv = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get('vertex_index', lv)
    col = px[np.clip((uv[:, 1] * h).astype(int), 0, h - 1), np.clip((uv[:, 0] * w).astype(int), 0, w - 1)]
    acc = np.zeros((len(me.vertices), 3))
    cnt = np.zeros(len(me.vertices))
    np.add.at(acc, lv, col)
    np.add.at(cnt, lv, 1)
    rgb = acc / np.maximum(cnt, 1)[:, None]
    mx, mn = rgb.max(1), rgb.min(1)
    v = mx
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    hsv_h = np.array([colorsys.rgb_to_hsv(*c)[0] for c in rgb])
    cyan = (hsv_h > 0.42) & (hsv_h < 0.58)
    W = np.zeros((len(rgb), 4))
    black = v < 0.22
    teal = ~black & cyan & (s > 0.45) & (v < 0.8)
    glow = ~black & ~teal & cyan & (s > 0.2)
    cream = ~(black | teal | glow)
    for i, m in enumerate((teal, cream, black, glow)):
        W[m, i] = 1.0
    return W


def smooth_weights(ob, W: np.ndarray, rounds: int) -> np.ndarray:
    e = np.empty(len(ob.data.edges) * 2, dtype=np.int64)
    ob.data.edges.foreach_get('vertices', e)
    a, b = e[0::2], e[1::2]
    deg = np.bincount(a, minlength=len(W)) + np.bincount(b, minlength=len(W))
    for _ in range(rounds):
        S = np.zeros_like(W)
        np.add.at(S, a, W[b])
        np.add.at(S, b, W[a])
        W = 0.5 * W + 0.5 * S / np.maximum(deg, 1)[:, None]
    return W


def overrides(co: np.ndarray, W: np.ndarray) -> np.ndarray:
    """Regions drawn by modelled parts or by rule: under the face plate black, under the ear discs teal (the
    modelled disc carries the cream ring), the boot soles black. co in source units."""
    x, y, z = co[:, 0], co[:, 1], co[:, 2]
    p = PLATE
    plate = (y < -0.12) & ((np.abs(x) / p['a']) ** p['n'] + (np.abs(z - p['zc']) / p['b']) ** p['n'] < 1.12)
    c = np.array(EAR['c'])
    ear = np.zeros(len(co), bool)
    for sx in (1, -1):
        d = co - c * np.array([sx, 1, 1])
        ear |= (np.abs(x) > 0.34) & (np.hypot(d[:, 1], d[:, 2]) < EAR['r_rim'] * 1.05) & (np.abs(d[:, 0]) < 0.08)
    sole = z < SOLE_TOP
    for mask, k in ((plate, 2), (ear, 0), (sole, 2)):  # under the ears teal: the modelled disc has the cream ring
        W[mask] = 0
        W[mask, k] = 1
    # baked shadows the texture reads as black: where the hands hung against the shins (teal between the knee and
    # the boot's ankle crease) and where the arms shaded the torso's sides (cream between the arm sockets and the
    # waist band). The sheet has neither.
    black = W[:, 2] > 0.5
    # the boots: all teal from below the shin shell's top to the black sole (the scan's ankle crease and the shadows
    # the hands cast read as black smudges on the boots)
    leg_r = np.hypot(np.abs(x) - J_SRC['knee'][0], y - J_SRC['knee'][1])  # distance from the leg's axis
    shin = black & (((z < -0.50) & (z > ANKLE_CREASE[1]) & (leg_r > 0.105)) | ((z <= ANKLE_CREASE[1]) & (z > SOLE_TOP))) \
        & (np.abs(x) < 0.42)
    side = black & (np.abs(x) > 0.16) & (np.abs(x) < 0.30) & (z < 0.05) & (z > WAIST_TOP + 0.03) & (y > -0.3)
    for mask, k in ((shin, 0), (side, 1)):
        W[mask] = 0
        W[mask, k] = 1
    return W


def face_class(ob, W) -> list[str]:
    fv = np.empty(len(ob.data.loops), dtype=np.int64)
    ob.data.loops.foreach_get('vertex_index', fv)
    out = []
    for p in ob.data.polygons:
        w = W[list(p.vertices)].mean(0)
        out.append(CLASSES[int(np.argmax(w))])
    return out


def seg_dist(p: Vector, a: Vector, b: Vector) -> float:
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


def piece_of(p: Vector, cls: str) -> str:
    """The design's seams, in source units: neck seam, top of the waist band, knee; the arm chain by segment."""
    ax, side = abs(p.x), 'L' if p.x > 0 else 'R'
    if p.z > NECK_SEAM:
        return 'head'
    if ax > 0.30 and -0.72 < p.z < -0.44:  # the hand below the teal cuff (dropped: robot_hands.py replaces it)
        sx = 1 if p.x > 0 else -1
        hand = seg_dist(p, srcj('wrist', sx), srcj('hand_tip', sx)) / SEGMENTS['hand'][2]
        shin = seg_dist(p, srcj('knee', sx), srcj('toe', sx)) / SEGMENTS['shin'][2]
        if hand < shin:
            return f'hand_{side}'
    if (ax > 0.29 and p.z >= -0.50) or (ax > 0.24 and p.z > -0.05 and cls != 'cream'):  # cuff rim included
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


# --- materials ---------------------------------------------------------------------------------

def principled(m):
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Roughness'].default_value = GLOSS['rough']
    b.inputs['Specular IOR Level'].default_value = GLOSS['spec']
    b.inputs['Coat Weight'].default_value = GLOSS['coat']
    b.inputs['Coat Roughness'].default_value = GLOSS['coat_rough']
    return b


def rgb_node(nt, name, colour):
    n = nt.nodes.new('ShaderNodeRGB')
    n.name = n.label = name
    n.outputs[0].default_value = lin(colour)
    return n.outputs[0]


def body_material() -> bpy.types.Material:
    """Flat sheet colours from the smoothed class attribute 'cls' (R teal, G cream, B black, A glow), each through a
    sharp threshold so borders are crisp iso-lines. The teal comes from the 'host_tint' node (the tint mask is the
    same threshold on R)."""
    m = bpy.data.materials.get('mat_body') or bpy.data.materials.new('mat_body')
    b = principled(m)
    nt = m.node_tree
    at = nt.nodes.new('ShaderNodeAttribute')
    at.attribute_name = 'cls'
    sep = nt.nodes.new('ShaderNodeSeparateColor')
    nt.links.new(at.outputs['Color'], sep.inputs[0])

    def sharp(sock):
        mr = nt.nodes.new('ShaderNodeMapRange')
        mr.inputs['From Min'].default_value, mr.inputs['From Max'].default_value = 0.47, 0.53
        nt.links.new(sock, mr.inputs['Value'])
        return mr.outputs['Result']

    def mix(a, b_, fac):
        n = nt.nodes.new('ShaderNodeMix')
        n.data_type = 'RGBA'
        nt.links.new(fac, n.inputs['Factor'])
        nt.links.new(a, n.inputs[6])
        nt.links.new(b_, n.inputs[7])
        return n.outputs[2]

    col = rgb_node(nt, 'cream', PALETTE['cream'])
    col = mix(col, rgb_node(nt, 'host_tint', PALETTE['teal']), sharp(sep.outputs[0]))
    col = mix(col, rgb_node(nt, 'black', PALETTE['black']), sharp(sep.outputs[2]))
    glow = sharp(at.outputs['Alpha'])
    col = mix(col, rgb_node(nt, 'glow', EYE), glow)
    nt.links.new(col, b.inputs['Base Color'])
    nt.links.new(rgb_node(nt, 'glow_e', EYE), b.inputs['Emission Color'])
    em = nt.nodes.new('ShaderNodeMath')
    em.operation = 'MULTIPLY'
    em.inputs[1].default_value = 2.5
    nt.links.new(glow, em.inputs[0])
    nt.links.new(em.outputs[0], b.inputs['Emission Strength'])
    return m


def flat_material(name: str, colour: str, glossy=False, emit=0.0) -> bpy.types.Material:
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    b = principled(m)
    c = rgb_node(m.node_tree, 'host_tint' if name == 'mat_teal' else name, colour)
    m.node_tree.links.new(c, b.inputs['Base Color'])
    if glossy:  # the black visor glass
        b.inputs['Roughness'].default_value = 0.08
        b.inputs['Coat Weight'].default_value = 1.0
    if emit:
        m.node_tree.links.new(c, b.inputs['Emission Color'])
        b.inputs['Emission Strength'].default_value = emit
    return m


# --- pieces --------------------------------------------------------------------------------------

def split(ob, label: list[str]) -> dict:
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


def clean_and_cap(ob) -> dict:
    """Delete loose fragments; smooth each cut rim along itself; cap it (fill, triangulate, black material)."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    parts = islands(bm)
    keep = max(parts, key=len)
    drop = [f for g in parts if g is not keep and len(g) < max(60, 0.05 * len(keep)) for f in g]
    bmesh.ops.delete(bm, geom=drop, context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    # dangling spikes on the rim: faces with two or more boundary edges, peeled a few times
    for _ in range(4):
        spikes = [f for f in bm.faces if sum(e.is_boundary for e in f.edges) >= 2]
        if not spikes:
            break
        bmesh.ops.delete(bm, geom=spikes, context='FACES')
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    rim = [v for v in bm.verts if v.is_boundary]
    for _ in range(8):  # relax the rim along itself
        new = {}
        for v in rim:
            nb = [e.other_vert(v) for e in v.link_edges if e.is_boundary]
            if len(nb) == 2:
                new[v] = v.co * 0.5 + (nb[0].co + nb[1].co) * 0.25
        for v, c in new.items():
            v.co = c
    boundary = [e for e in bm.edges if e.is_boundary]
    rim_pts = [v.co.copy() for v in rim]
    caps = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)['faces']
    tri = bmesh.ops.triangulate(bm, faces=caps, quad_method='BEAUTY', ngon_method='BEAUTY')['faces']
    for f in tri:
        f.material_index = 1
        f.smooth = False
    bm.normal_update()
    bm.to_mesh(ob.data)
    bm.free()
    return {'fragments_deleted': len(parts) - 1 - sum(1 for g in parts if g is not keep and len(g) >= max(60, 0.05 * len(keep))),
            'caps': len(caps), 'rim_pts': rim_pts}


def ball(name: str, at: Vector, r: float, squash: float = 1.0) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=r,
                              matrix=Matrix.Translation(at) @ Matrix.Diagonal((1, 1, squash, 1)))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(flat_material('mat_black', PALETTE['black']))
    o = bpy.data.objects.new(name, me)  # at the origin, like every piece: the mesh holds the position
    bpy.context.scene.collection.objects.link(o)
    return o


# --- modelled parts --------------------------------------------------------------------------------

def superellipse(t: float, a: float, b: float, n: float) -> tuple:
    c, s = math.cos(t), math.sin(t)
    return a * math.copysign(abs(c) ** (2 / n), c), b * math.copysign(abs(s) ** (2 / n), s)


def plate_surface(bvh: BVHTree):
    """The face plate's surface, y(x, z), as a smooth quadric fitted to the scan's front inside the opening: the
    scan itself is bumpy there, which read as seams and steps."""
    p = PLATE
    pts = []
    for i in range(1, 9):
        for j in range(24):
            x, dz = superellipse(2 * math.pi * j / 24, p['a'] * i / 9, p['b'] * i / 9, p['n'])
            hit = bvh.ray_cast(Vector((x, -3.0, p['zc'] + dz)), Vector((0, 1, 0)))[0]
            if hit:
                pts.append((x, dz, hit.y))
    P = np.array(pts)
    A = np.c_[np.ones(len(P)), P[:, 0] ** 2, P[:, 1] ** 2, P[:, 1]]
    coef = np.linalg.lstsq(A, P[:, 2], rcond=None)[0]
    return lambda x, dz: float(coef[0] + coef[1] * x * x + coef[2] * dz * dz + coef[3] * dz)


def face_plate(bvh: BVHTree, to_m: Matrix) -> bpy.types.Object:
    """One smooth glossy black rounded-rectangle plate over the helmet opening (a superellipse on the fitted plate
    surface, 8 mm proud of it), framed by a thin, even teal rim: a tube along its outline, on the host_tint node."""
    p = PLATE
    surf = plate_surface(bvh)
    rings, segs = 16, 128
    bm = bmesh.new()
    grid = []
    for i in range(rings + 1):
        r = i / rings
        row = []
        for j in range(segs if i else 1):
            x, dz = superellipse(2 * math.pi * j / segs, p['a'] * r, p['b'] * r, p['n'])
            row.append(bm.verts.new(to_m @ Vector((x, surf(x, dz) - 0.016, p['zc'] + dz))))
        grid.append(row)
    for j in range(segs):
        bm.faces.new((grid[0][0], grid[1][j], grid[1][(j + 1) % segs]))
    for i in range(1, rings):
        for j in range(segs):
            bm.faces.new((grid[i][j], grid[i + 1][j], grid[i + 1][(j + 1) % segs], grid[i][(j + 1) % segs]))
    plate_faces = list(bm.faces)
    # the rim: a tube around the outline, sitting on the plate's edge and the helmet
    tube_r, tsegs = 0.024, 12
    ring_pts = []
    for j in range(segs):
        x, dz = superellipse(2 * math.pi * j / segs, p['a'] * 1.03, p['b'] * 1.03 + 0.012, p['n'])
        ring_pts.append(Vector((x, surf(x, dz) - 0.012, p['zc'] + dz)))
    loops = []
    for j, c in enumerate(ring_pts):
        t = (ring_pts[(j + 1) % segs] - ring_pts[j - 1]).normalized()
        out = Vector((c.x, 0, c.z - p['zc']))
        out = (out - t * out.dot(t)).normalized()
        fwd = t.cross(out).normalized()
        if fwd.y > 0:
            fwd = -fwd
        loops.append([bm.verts.new(to_m @ (c + (out * math.cos(a) + fwd * math.sin(a)) * tube_r))
                      for a in (2 * math.pi * k / tsegs for k in range(tsegs))])
    for j in range(segs):
        a, b = loops[j], loops[(j + 1) % segs]
        for k in range(tsegs):
            f = bm.faces.new((a[k], a[(k + 1) % tsegs], b[(k + 1) % tsegs], b[k]))
            f.material_index = 1
    bmesh.ops.recalc_face_normals(bm, faces=plate_faces)
    if plate_faces[len(plate_faces) // 2].normal.y > 0:
        bmesh.ops.reverse_faces(bm, faces=plate_faces)
    bmesh.ops.recalc_face_normals(bm, faces=[f for f in bm.faces if f.material_index == 1])
    me = bpy.data.meshes.new('body_faceplate')
    bm.to_mesh(me)
    bm.free()
    for f in me.polygons:
        f.use_smooth = True
    me.materials.append(flat_material('mat_visor', PALETTE['black'], glossy=True))
    me.materials.append(flat_material('mat_teal', PALETTE['teal']))
    o = bpy.data.objects.new('body_faceplate', me)
    bpy.context.scene.collection.objects.link(o)
    return o


def ear_disc(side: int, bvh: BVHTree, to_m: Matrix) -> bpy.types.Object:
    """Black centre, thin cyan ring, cream ring and a black rim: one lathed profile on the helmet's side, facing out
    along x, standing just proud of the scan's outermost point there, with a skirt back into the helmet."""
    e = EAR
    c = Vector(e['c']) * Vector((side, 1, 1))
    hit = bvh.ray_cast(c + Vector((side * 1.0, 0, 0)), Vector((-side, 0, 0)))[0]
    x0 = (hit.x if hit else c.x) + side * 0.004
    rb, rg, rc, rr = e['r_black'], e['r_glow'], e['r_cream'], e['r_rim']
    # (radius, height above the helmet, material of the band from this point to the next)
    profile = [(0.0, 0.012, 'black'), (rb, 0.012, 'black'), (rb, 0.016, 'glow'), (rg, 0.020, 'glow'),
               (rg, 0.026, 'cream'), ((rg + rc) / 2, 0.032, 'cream'), (rc, 0.024, 'black'), (rr, 0.012, 'black'),
               (rr, -0.030, None)]
    mats = {'black': 0, 'glow': 1, 'cream': 2}
    segs = 72
    bm = bmesh.new()
    centre = bm.verts.new(to_m @ Vector((x0 + side * profile[0][1], c.y, c.z)))
    prev = None
    for k, (r, h, kind) in enumerate(profile[1:], 1):
        loop = [bm.verts.new(to_m @ Vector((x0 + side * h, c.y + r * math.cos(2 * math.pi * j / segs),
                                            c.z + r * math.sin(2 * math.pi * j / segs)))) for j in range(segs)]
        band = profile[k - 1][2]
        for j in range(segs):
            if prev is None:
                f = bm.faces.new((centre, loop[j], loop[(j + 1) % segs]))
            else:
                f = bm.faces.new((prev[j], loop[j], loop[(j + 1) % segs], prev[(j + 1) % segs]))
            f.material_index = mats[band]
        prev = loop
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(f'body_ear_{"L" if side > 0 else "R"}')
    bm.to_mesh(me)
    bm.free()
    for f in me.polygons:
        f.use_smooth = True
    for kind in ('black', 'glow', 'cream'):
        me.materials.append(flat_material(f'mat_{kind}', EYE if kind == 'glow' else PALETTE[kind],
                                          emit=2.5 if kind == 'glow' else 0.0))
    o = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(o)
    return o


# the sheet's eyes (robot-apose-front.png): rounded capsules 1.73 times as tall as wide, as fractions of the plate:
# width 0.133 and height 0.343 of the plate's, centres 0.233 of its width either side and 0.05 of its height below
EYE_SHAPE = dict(w=0.133 * 1.12, h=0.343 * 1.12, dx=0.233, dz=-0.05)  # 12% larger, towards robot-sheet.png's eyes


def eyes(bvh: BVHTree, to_m: Matrix) -> bpy.types.Object:
    """Two glowing capsules on the face plate, domed slightly, their own layer (the Codex eyes reuse it)."""
    p, e = PLATE, EYE_SHAPE
    surf = plate_surface(bvh)
    w, h = e['w'] * 2 * p['a'], e['h'] * 2 * p['b']
    r, half = w / 2, e['h'] * p['b'] - w / 2  # capsule radius; half-length of its straight sides
    bm = bmesh.new()
    n = 48
    for sx in (1, -1):
        cx, cz = sx * e['dx'] * 2 * p['a'], p['zc'] + e['dz'] * 2 * p['b']
        outline = []
        for k in range(n):
            a = 2 * math.pi * k / n
            x, z = r * math.cos(a), r * math.sin(a)
            z += half if z >= 0 else -half
            outline.append((x, z))
        rings = []
        for t in (1.0, 0.75, 0.45, 0.0):  # outer to centre, rising into a low dome
            ring = []
            for x, z in outline:
                X, Z = cx + x * t, cz + z * t if t else cz
                lift = 0.004 + 0.006 * (1 - t * t)
                ring.append(bm.verts.new(to_m @ Vector((X, surf(X, Z - p['zc']) - 0.016 - lift, Z))))
            rings.append(ring)
        for a, b in zip(rings, rings[1:]):
            for k in range(n):
                bm.faces.new((a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new('body_eyes')
    bm.to_mesh(me)
    bm.free()
    for f in me.polygons:
        f.use_smooth = True
    me.materials.append(flat_material('mat_eye', EYE, emit=2.0))
    o = bpy.data.objects.new('body_eyes', me)
    bpy.context.scene.collection.objects.link(o)
    return o


# the Claude face: a band across the plate at the eyes' height, as fractions of the plate (width, height)
BAND = dict(w=0.72, h=0.2)


def face_band(bvh: BVHTree, to_m: Matrix) -> bpy.types.Object:
    """A glowing capsule band across the face plate where the eyes sit (the Claude face layer; the runtime colours
    it), on the plate's fitted surface, just proud of it."""
    p, e, b = PLATE, EYE_SHAPE, BAND
    surf = plate_surface(bvh)
    half_w, r = b['w'] * p['a'], b['h'] * p['b']
    cz = p['zc'] + e['dz'] * 2 * p['b']
    n = 64
    outline = []
    for k in range(n):
        a = 2 * math.pi * k / n
        x, z = r * math.cos(a), r * math.sin(a)
        x += (half_w - r) if x >= 0 else -(half_w - r)
        outline.append((x, z))
    bm = bmesh.new()
    rings = []
    for t in (1.0, 0.7, 0.0):
        ring = [bm.verts.new(to_m @ Vector((x * t, surf(x * t, z * t + cz - p['zc']) - 0.022 - 0.004 * (1 - t * t),
                                            cz + z * t))) for x, z in outline]
        rings.append(ring)
    for a_, b_ in zip(rings, rings[1:]):
        for k in range(n):
            bm.faces.new((a_[k], a_[(k + 1) % n], b_[(k + 1) % n], b_[k]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new('body_band')
    bm.to_mesh(me)
    bm.free()
    for f in me.polygons:
        f.use_smooth = True
    me.materials.append(flat_material('mat_face', '#ffffff', emit=2.0))
    o = bpy.data.objects.new('body_band', me)
    bpy.context.scene.collection.objects.link(o)
    return o


def back_dot(bvh: BVHTree, to_m: Matrix) -> bpy.types.Object:
    """A small glowing dot of the agent's colour on the back of the helmet, so the agent reads from behind; part of
    both face layers."""
    z = PLATE['zc'] + 0.06
    hit, normal, *_ = bvh.ray_cast(Vector((0, 3.0, z)), Vector((0, -1, 0)))
    c = hit + normal * 0.006
    bm = bmesh.new()
    rot = normal.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=1.0,
                              matrix=Matrix.Translation(c) @ rot @ Matrix.Diagonal((0.045, 0.045, 0.012, 1)))
    bm.transform(to_m)
    me = bpy.data.meshes.new('body_back_dot')
    bm.to_mesh(me)
    bm.free()
    for f in me.polygons:
        f.use_smooth = True
    me.materials.append(flat_material('mat_face', '#ffffff', emit=2.0))
    o = bpy.data.objects.new('body_back_dot', me)
    bpy.context.scene.collection.objects.link(o)
    return o


def eye_marks(ob, W) -> list:
    """Centre and size of each eye's glow on the face plate (source units), from the raw texture votes."""
    co = np.array([v.co for v in ob.data.vertices])
    out = []
    for sx in (1, -1):
        m = (W[:, 3] > 0.5) & (co[:, 1] < -0.15) & (co[:, 2] > 0.35) & (co[:, 2] < 0.75) & (co[:, 0] * sx > 0) & \
            (co[:, 0] * sx < 0.35)
        pts = co[m]
        lo, hi = pts.min(0), pts.max(0)
        out.append((Vector((lo + hi) / 2), Vector(hi - lo)))
    (c0, s0), (c1, s1) = out
    c = Vector((abs(c0.x) + abs(c1.x), c0.y + c1.y, c0.z + c1.z)) / 2
    size = (s0 + s1) / 2
    return [(Vector((c.x, c.y, c.z)), size), (Vector((-c.x, c.y, c.z)), size)]


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(SRC))
    ob = next(o for o in bpy.context.scene.objects if o.type == 'MESH')
    ob.data.transform(ob.matrix_world)
    ob.parent = None
    ob.matrix_world = Matrix()
    report = {'clean': weld_and_clean(ob)}
    apply_mod(ob, 'SUBSURF', subdivision_type='SIMPLE', levels=1, uv_smooth='PRESERVE_BOUNDARIES')
    raw = vertex_weights(ob)
    eyes_src = eye_marks(ob, raw)
    co = np.array([v.co for v in ob.data.vertices])
    W = smooth_weights(ob, overrides(co, smooth_weights(ob, overrides(co, raw), 6)), 2)
    cls = face_class(ob, W)
    bvh = BVHTree.FromObject(ob, bpy.context.evaluated_depsgraph_get())
    label = [piece_of(p.center, c) for p, c in zip(ob.data.polygons, cls)]
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    s = HEIGHT / (zmax - zmin)
    to_m = Matrix.Scale(s, 4) @ Matrix.Translation((0, 0, -zmin))
    ob.data.transform(to_m)
    attr = ob.data.color_attributes.new('cls', 'FLOAT_COLOR', 'POINT')
    attr.data.foreach_set('color', W.astype(np.float32).ravel())
    ob.data.materials.clear()
    ob.data.materials.append(body_material())
    ob.data.materials.append(flat_material('mat_black', PALETTE['black']))  # slot 1: the caps
    for p in ob.data.polygons:
        p.use_smooth = True
    pieces = split(ob, label)
    for k in [k for k in pieces if k.startswith('hand_')]:
        bpy.data.objects.remove(pieces.pop(k))
    bpy.data.objects.remove(ob)
    rims = {}
    report['pieces'] = {}
    for k, o in pieces.items():
        r = clean_and_cap(o)
        rims[k] = r.pop('rim_pts')
        report['pieces'][k] = r | {'faces': len(o.data.polygons)}
    pieces['faceplate'] = face_plate(bvh, to_m)
    pieces['eyes'] = eyes(bvh, to_m)
    pieces['band'] = face_band(bvh, to_m)
    pieces['back_dot'] = back_dot(bvh, to_m)
    for side, name in ((1, 'ear_L'), (-1, 'ear_R')):
        pieces[name] = ear_disc(side, bvh, to_m)
    joints = {k: list(to_m @ srcj(k)) for k in J_SRC}
    for side, sx in (('L', 1), ('R', -1)):
        for k in ('shoulder', 'elbow', 'wrist', 'hand_tip', 'hip', 'knee', 'ankle', 'toe'):
            joints[f'{k}_{side}'] = list(to_m @ srcj(k, sx))
    for k in ('shoulder', 'elbow', 'wrist', 'hand_tip', 'hip', 'knee', 'ankle', 'toe'):
        del joints[k]
    # ball joints sized to the seam: the rim points of the two pieces meeting there, near the joint
    balls = {}
    for k, (meet, r_min, role, sided) in BALLS.items():
        for side in (('L', 'R') if sided else (None,)):
            jk = f'{k}_{side}' if side else k
            c = Vector(joints[jk])
            near = []
            for piece in meet:
                pk = piece if piece in ('head', 'torso', 'pelvis') else f'{piece}_{side}'
                near += [(p - c).length for p in rims.get(pk, []) if (p - c).length < BALL_MAX * 1.6 * s]
            name = f'ball_{jk}'
            if k == 'neck':  # a flattened black collar over the whole torso-top opening, not a ball in it
                rim = [q for piece in meet for q in rims.get(piece, []) if abs(q.z - c.z) < 0.06]
                r = 1.06 * max((Vector((q.x - c.x, q.y - c.y, 0))).length for q in rim)
                pieces[name] = ball(f'body_{name}', c, r, squash=0.45)
                balls[name] = {'radius_m': round(r, 4), 'rim_points': len(rim), 'squash': 0.45}
                continue
            r = max([r_min * s] + [d * 0.92 for d in near])
            r = min(r, BALL_MAX * s)
            pieces[name] = ball(f'body_{name}', c, r)
            balls[name] = {'radius_m': round(r, 4), 'rim_points': len(near)}
    report['balls'] = balls
    roles = {'head': 'head', 'faceplate': 'head', 'eyes': 'head', 'band': 'head', 'back_dot': 'head', 'ear_L': 'head', 'ear_R': 'head',
             'torso': 'spine3', 'pelvis': 'root', 'ball_neck': 'neck'}
    for key, (_, _, _, role) in SEGMENTS.items():
        for side in 'LR':
            roles[f'{key}_{side}'] = role
    for k, (_, _, role, sided) in BALLS.items():
        if sided:
            for side in 'LR':
                roles[f'ball_{k}_{side}'] = role
    report |= {'scale_m_per_unit': round(s, 4), 'height_m': HEIGHT}
    meta = {'height_m': HEIGHT, 'joints_m': {k: [round(c, 4) for c in v] for k, v in joints.items()},
            'pieces': {k: {'role': roles[k], 'side': k[-1] if k[-2:] in ('_L', '_R') and k.split('_')[0] != 'ear'
                           else None} for k in pieces}}
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT), compress=True)
    OUT.with_suffix('.json').write_text(json.dumps(meta, indent=1))
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if '--report' in args:
        Path(args[args.index('--report') + 1]).write_text(json.dumps(report, indent=1))
    print('REPORT', json.dumps({k: v for k, v in report.items() if k != 'pieces'}))
    print('SAVED', OUT)


main()
