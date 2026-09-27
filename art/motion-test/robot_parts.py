"""Build the robot parts library from the user's AI-generated GLBs (outside the repo).

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/robot_parts.py [-- --report <json>]

Reads ~/.local/state/fleet/renovation/robot-parts/*.glb. Each part is cleaned up (see FIX), decimated,
rotated into the robot's frame (facing -Y, Z up; limb parts as mounted on the robot's RIGHT side, -X),
scaled to metres, and given its origin at its joint pivot. Left-side parts are mirrored at assembly time.
Writes ~/.cache/fleet-motion-test/robot_parts.blend, one object per part.

Colours are robot-sheet.png's, calibrated so a Cycles render under the B1 studio reproduces the sheet's
mid-tones (calibrate_colours.py). Tintable = every material with a 'host_tint' node (the teal shells, and
the teal side of the foot); replacing that node's colour recolours the host without touching the rest.
"""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

SRC = Path.home() / '.local/state/fleet/renovation/robot-parts'
OUT = Path.home() / '.cache/fleet-motion-test/robot_parts.blend'

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palette import PALETTE, lin  # noqa: E402
# the sheet's glazed plastic: small sharp highlights over saturated colour, so a thin sharp coat and a low
# broad specular (a strong one washes the teal out under the white studio HDRI)
GLOSS = dict(rough=0.3, spec=0.1, coat=0.25, coat_rough=0.04)

# key: (glb or None for a primitive, euler deg XYZ, (size axis, metres), pivot as bbox fractions, faces, material)
PARTS = {
    'helmet':      ('helmet', (0, 0, 0), ('x', 0.43), (0.5, 0.5, 0.0), 14000, 'shell'),
    'visor':       ('visor', (0, 0, 0), ('x', 0.305), (0.5, 0.5, 0.5), 3000, 'visor'),
    'ear':         ('ear cap', (0, 0, -90), ('z', 0.10), (1.0, 0.5, 0.5), 2500, 'cream_tex'),
    'neck':        ('neck joint', (0, 0, 0), ('x', 0.13), (0.5, 0.5, 0.5), 1500, 'black'),
    'chest':       ('chest', (0, 0, 0), ('x', 0.36), (0.5, 0.5, 0.0), 8000, 'cream_tex'),
    'chest_light': ('chest light', (0, 0, 0), ('x', 0.065), (0.5, 1.0, 0.5), 1500, 'glow_tex'),
    'waist':       ('waist ring', (0, 0, 0), ('x', 0.25), (0.5, 0.5, 0.5), 2500, 'black'),
    'pelvis':      ('pelvis', (0, 0, 0), ('x', 0.29), (0.5, 0.5, 0.6), 6000, 'cream_tex'),
    'joint':       ('ball joint', (0, 0, 0), ('z', 1.0), (0.5, 0.5, 0.5), 1500, 'black'),  # scaled per joint
    'upper_arm':   ('upper arm', (0, 0, 0), ('x', 0.20), (0.80, 0.5, 0.5), 5000, 'shell'),
    'forearm':     ('forearm', (0, 0, -90), ('x', 0.17), (1.0, 0.5, 0.5), 4000, 'shell'),
    'palm':        ('hand back side', (0, 0, -90), ('x', 0.19), (1.0, 0.5, 0.5), 3000, 'black'),
    'fingers':     ('hand back side', (0, 0, -90), ('x', 0.19), (1.0, 0.5, 0.5), 3000, 'black'),
    'thumb':       ('hand back side', (0, 0, -90), ('x', 0.19), (1.0, 0.5, 0.5), 1500, 'black'),
    'thigh':       (None, (0, 0, 0), ('z', 0.235), (0.5, 0.5, 0.93), 4000, 'shell'),
    'shin':        ('shin', (0, 0, 0), ('z', 0.47), (0.5, 0.5, 0.95), 5000, 'shell'),
    'foot':        ('foot', (0, 0, 0), ('y', 0.27), (0.5, 0.62, None), 5000, 'shell_sole'),
}
ANKLE_H = 0.10  # sole to ankle pivot; shared with motion_rig.py
# the sheet's limb shells and mitts are chunkier than the generated parts: widen across the length axis
GIRTH = {'upper_arm': 1.25, 'forearm': 1.55, 'shin': 0.85, 'foot': 1.15, 'palm': 1.8, 'fingers': 1.8, 'thumb': 1.8}
# per-part repairs, applied to the raw mesh before decimation (see fix_*)
FIX = {
    'helmet': 'remesh',           # panel-line creases and texture seams read as cracks: one smooth dome
    'pelvis': 'mirror_half',      # asymmetric: an extra socket on one side of the back; keep the clean +X half
    'upper_arm': 'fill_hinge',    # a through-hole in the shoulder bulb shows front and back
    'forearm': 'largest_island',  # a loose collar ring floats off the elbow end
    'shin': 'remesh',             # moulded pins and seams at the ankle; smooth shell as on the sheet
    'foot': 'remesh',             # heel socket and ankle-pin holes read as rings from behind
    'palm': 'mitt', 'fingers': 'mitt', 'thumb': 'mitt',  # separate thin fingers -> the sheet's chunky mitt
}
# flatten along Z after scaling (the waist ring's two stacked bands read as a thick double ring)
ZSCALE = {'waist': 0.55}
UNUSED = {
    'helmet large': 'replaced by the regular helmet (too big for the body; creases read as cracks)',
    'hand palm side': 'a second complete hand, not a half; hand back side is split into palm, fingers, thumb',
    'thigh': 'wrong shape: socket holes on three sides; substituted by a smooth tapered capsule',
    'shoulder joint': 'flanged joint read as a stack of rings; the ball joint is used at every joint',
    'elbow joint': 'as shoulder joint', 'socket joint 1': 'as shoulder joint', 'socket joint 2': 'as shoulder joint',
}
# hand split in the raw GLB frame (fingers toward -Y, thumb on +X, back of hand +Z)
THUMB_X, KNUCKLE_Y = 0.245, -0.12


def import_part(glb: str) -> bpy.types.Object:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(SRC / f'{glb}.glb'))
    new = [o for o in bpy.data.objects if o not in before]
    mesh = next(o for o in new if o.type == 'MESH')
    mesh.data.transform(mesh.matrix_world)
    mesh.parent = None
    mesh.matrix_world = Matrix()
    for o in new:
        if o is not mesh:
            bpy.data.objects.remove(o)
    return mesh


def audit(ob) -> dict:
    """Non-manifold edges, boundary (open) edges, loose parts and near-duplicate vertices."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    boundary = sum(e.is_boundary for e in bm.edges)
    nonman = sum(not e.is_manifold for e in bm.edges) - boundary
    dup = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    dup -= len(bm.verts)
    islands, seen = 0, set()
    for v in bm.verts:
        if v.index in seen:
            continue
        islands += 1
        stack = [v]
        while stack:
            x = stack.pop()
            if x.index in seen:
                continue
            seen.add(x.index)
            stack += [e.other_vert(x) for e in x.link_edges]
    bm.free()
    return {'faces': len(ob.data.polygons), 'boundary_edges': boundary, 'nonmanifold_edges': nonman,
            'duplicate_verts': dup, 'islands': islands}


def apply_mod(ob, kind: str, **props) -> None:
    m = ob.modifiers.new(kind.lower(), kind)
    for k, v in props.items():
        setattr(m, k, v)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_apply(modifier=m.name)


def fix_remesh(ob) -> None:
    """Voxel remesh (closes cracks, merges seams, rebuilds normals) then relax the voxel stair-steps."""
    size = max(ob.dimensions)
    apply_mod(ob, 'REMESH', mode='VOXEL', voxel_size=size / 260, use_smooth_shade=True)
    apply_mod(ob, 'CORRECTIVE_SMOOTH', iterations=12, smooth_type='LENGTH_WEIGHTED', use_only_smooth=True)
    fix_largest_island(ob)  # voxel remesh leaves small inner shells behind


def fix_mitt(ob) -> None:
    """Coarse voxel remesh: merges the fingers' gaps into one mitten surface, then relax it."""
    size = max(ob.dimensions)
    apply_mod(ob, 'REMESH', mode='VOXEL', voxel_size=size / 45, use_smooth_shade=True)
    apply_mod(ob, 'CORRECTIVE_SMOOTH', iterations=20, smooth_type='LENGTH_WEIGHTED', use_only_smooth=True)
    fix_largest_island(ob)


def fix_mirror_half(ob) -> None:
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, 0), plane_no=(1, 0, 0), clear_inner=True)
    bm.to_mesh(ob.data)
    bm.free()
    apply_mod(ob, 'MIRROR', use_axis=(True, False, False), use_clip=True, use_mirror_merge=True,
              merge_threshold=0.01)


def fix_fill_hinge(ob) -> None:
    """Union a sphere into the shoulder bulb (the +X end) so its through-hole closes, then voxel remesh."""
    co = [v.co for v in ob.data.vertices]
    x1 = max(c.x for c in co)
    r = 0.5 * (max(c.z for c in co) - min(c.z for c in co)) * 0.99
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=(x1 - r / 0.99, 0, 0), segments=48, ring_count=24)
    ball = bpy.context.active_object
    ball.data.transform(ball.matrix_world)
    ball.matrix_world = Matrix()
    bpy.ops.object.select_all(action='DESELECT')
    ball.select_set(True)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.join()
    fix_remesh(ob)


def fix_largest_island(ob) -> None:
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    left, parts = set(bm.faces), []
    while left:
        f0 = left.pop()
        group, stack = {f0}, [f0]
        while stack:
            f = stack.pop()
            for e in f.edges:
                for g in e.link_faces:
                    if g in left:
                        left.discard(g)
                        group.add(g)
                        stack.append(g)
        parts.append(group)
    keep = max(parts, key=len)
    bmesh.ops.delete(bm, geom=[f for p in parts if p is not keep for f in p], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bm.to_mesh(ob.data)
    bm.free()


def hand_piece(ob, key: str) -> None:
    """Keep one region of the hand (raw frame) and cap the cut so it stays closed."""
    def region(c: Vector) -> str:
        if c.x > THUMB_X and -0.55 < c.y < 0.38:
            return 'thumb'
        return 'fingers' if c.y < KNUCKLE_Y else 'palm'
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    drop = [f for f in bm.faces if region(f.calc_center_median()) != key]
    bmesh.ops.delete(bm, geom=drop, context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()


def capsule(name: str) -> bpy.types.Object:
    """Smooth tapered capsule standing on Z (the thigh substitute), 2 units long before scaling."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=40, v_segments=24, radius=1.0)
    for v in bm.verts:
        v.co.z = v.co.z * 0.55 + (0.45 if v.co.z > 0 else -0.45)  # stretch the middle into a cylinder
        t = (v.co.z + 1) / 2                                          # 0 at the knee, 1 at the hip
        s = 0.52 * (0.82 + 0.18 * t)
        v.co.x *= s * 1.05
        v.co.y *= s
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    apply_mod(ob, 'SUBSURF', levels=1)
    return ob


# --- materials -----------------------------------------------------------------------------

def material(ob, kind: str, tex=None) -> None:
    m = bpy.data.materials.new(f'{ob.name}_{kind}')
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']
    b.inputs['Roughness'].default_value = GLOSS['rough']
    b.inputs['Coat Weight'].default_value = GLOSS['coat']
    b.inputs['Coat Roughness'].default_value = GLOSS['coat_rough']
    b.inputs['Specular IOR Level'].default_value = GLOSS['spec']

    def rgb(name, hex_):
        n = nt.nodes.new('ShaderNodeRGB')
        n.name = n.label = name
        n.outputs[0].default_value = lin(hex_)
        return n.outputs[0]

    def masked(dark, light, lo=0.22, hi=0.34):
        """texture luminance -> dark (black trims) or light; lo/hi bracket the trim threshold"""
        img = nt.nodes.new('ShaderNodeTexImage')
        img.image = tex
        bw = nt.nodes.new('ShaderNodeRGBToBW')
        nt.links.new(img.outputs['Color'], bw.inputs[0])
        mr = nt.nodes.new('ShaderNodeMapRange')
        mr.inputs['From Min'].default_value, mr.inputs['From Max'].default_value = lo, hi
        nt.links.new(bw.outputs[0], mr.inputs['Value'])
        mix = nt.nodes.new('ShaderNodeMix')
        mix.data_type = 'RGBA'
        nt.links.new(mr.outputs['Result'], mix.inputs['Factor'])
        nt.links.new(dark, mix.inputs[6])
        nt.links.new(light, mix.inputs[7])
        return mix.outputs[2], mr.outputs['Result']

    if kind == 'shell':
        nt.links.new(rgb('host_tint', PALETTE['teal']), b.inputs['Base Color'])
    elif kind == 'shell_tex':
        col, _ = masked(rgb('black', PALETTE['black']), rgb('host_tint', PALETTE['teal']))
        nt.links.new(col, b.inputs['Base Color'])
    elif kind == 'shell_sole':  # boot: teal (tintable) with a black sole band, masked by height
        tc = nt.nodes.new('ShaderNodeTexCoord')
        sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        nt.links.new(tc.outputs['Object'], sep.inputs[0])
        mr = nt.nodes.new('ShaderNodeMapRange')
        mr.inputs['From Min'].default_value = -ANKLE_H + 0.022  # object origin = ankle pivot
        mr.inputs['From Max'].default_value = -ANKLE_H + 0.026
        nt.links.new(sep.outputs['Z'], mr.inputs['Value'])
        mix = nt.nodes.new('ShaderNodeMix')
        mix.data_type = 'RGBA'
        nt.links.new(mr.outputs['Result'], mix.inputs['Factor'])
        nt.links.new(rgb('black', PALETTE['black']), mix.inputs[6])
        nt.links.new(rgb('host_tint', PALETTE['teal']), mix.inputs[7])
        nt.links.new(mix.outputs[2], b.inputs['Base Color'])
    elif kind == 'cream_tex':
        col, _ = masked(rgb('black', PALETTE['black']), rgb('cream', PALETTE['cream']))
        nt.links.new(col, b.inputs['Base Color'])
    elif kind in ('black', 'visor'):
        nt.links.new(rgb('black', PALETTE['black']), b.inputs['Base Color'])
        if kind == 'visor':
            b.inputs['Roughness'].default_value = 0.08
            b.inputs['Coat Weight'].default_value = 1.0
    elif kind == 'glow_tex':  # the light's bright centre glows, its rim is a black bezel
        col, fac = masked(rgb('black', PALETTE['black']), rgb('glow', PALETTE['glow']), 0.45, 0.6)
        nt.links.new(col, b.inputs['Base Color'])
        nt.links.new(rgb('glow_e', PALETTE['glow']), b.inputs['Emission Color'])
        ms = nt.nodes.new('ShaderNodeMath')
        ms.operation = 'MULTIPLY'
        ms.inputs[1].default_value = 3.0
        nt.links.new(fac, ms.inputs[0])
        nt.links.new(ms.outputs[0], b.inputs['Emission Strength'])
    ob.data.materials.clear()
    ob.data.materials.append(m)


def build(key: str, spec) -> tuple[bpy.types.Object, dict]:
    glb, rot, (axis, size), pivot, faces, kind = spec
    ob = import_part(glb) if glb else capsule(key)
    ob.name = ob.data.name = key
    tex = None
    if glb and ob.data.materials:
        tex = next((n.image for n in ob.data.materials[0].node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None)
    before = audit(ob)
    if glb:  # the glTF import splits vertices along UV seams; decimating that opens cracks. Weld first
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        bm.to_mesh(ob.data)
        bm.free()
    fix = FIX.get(key)
    if key in ('palm', 'fingers', 'thumb'):
        hand_piece(ob, key)
        fix_mitt(ob)
    elif fix:
        globals()[f'fix_{fix}'](ob)
    if len(ob.data.polygons) > faces:
        apply_mod(ob, 'DECIMATE', ratio=faces / len(ob.data.polygons))
    ob.data.transform(Euler([math.radians(a) for a in rot]).to_matrix().to_4x4())
    co = [v.co for v in ob.data.vertices]
    lo = Vector([min(c[i] for c in co) for i in range(3)])
    hi = Vector([max(c[i] for c in co) for i in range(3)])
    dims = hi - lo
    if key in ('palm', 'fingers', 'thumb'):  # one hand, one scale and pivot (the wrist), three pieces
        lo, dims = HAND_FRAME
    s = size / dims['xyz'.index(axis)]
    fz = pivot[2] if pivot[2] is not None else ANKLE_H / (dims.z * s)  # foot: sole sits ANKLE_H below pivot
    piv = Vector([lo[i] + dims[i] * f for i, f in enumerate((pivot[0], pivot[1], fz))])
    ob.data.transform(Matrix.Scale(s, 4) @ Matrix.Translation(-piv))
    g = GIRTH.get(key, 1.0)
    ob.data.transform(Matrix.Diagonal([1.0 if a == axis else g for a in 'xyz']).to_4x4())
    if key in ZSCALE:
        ob.data.transform(Matrix.Diagonal((1, 1, ZSCALE[key])).to_4x4())
    ob.data.validate()
    ob.data.update()
    for p in ob.data.polygons:
        p.use_smooth = True
    material(ob, kind, tex)
    ob['kind'] = kind
    co = [v.co for v in ob.data.vertices]
    ob['lo_m'] = [min(c[i] for c in co) for i in range(3)]
    ob['hi_m'] = [max(c[i] for c in co) for i in range(3)]
    return ob, {'fix': fix, 'raw': before, 'final': audit(ob)}


HAND_FRAME = None


def hand_frame() -> tuple:
    """Bounding box of the whole (rotated) hand, so the three hand pieces share one scale and pivot."""
    ob = import_part('hand back side')
    ob.data.transform(Euler([0, 0, math.radians(-90)]).to_matrix().to_4x4())
    co = [v.co for v in ob.data.vertices]
    lo = Vector([min(c[i] for c in co) for i in range(3)])
    hi = Vector([max(c[i] for c in co) for i in range(3)])
    # pivot inside the wrist cuff rather than at its open end
    bpy.data.objects.remove(ob)
    return lo, hi - lo


def main() -> None:
    global HAND_FRAME
    bpy.ops.wm.read_factory_settings(use_empty=True)
    HAND_FRAME = hand_frame()
    report = {}
    for key, spec in PARTS.items():
        ob, rep = build(key, spec)
        report[key] = rep
        print('PART', key, json.dumps(rep))
    for img in bpy.data.images:
        img.pack()
    for m in [m for m in bpy.data.materials if m.users == 0]:
        bpy.data.materials.remove(m)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT), compress=True)
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if '--report' in args:
        Path(args[args.index('--report') + 1]).write_text(json.dumps({'parts': report, 'unused': UNUSED}, indent=1))
    print('SAVED', OUT)


main()
