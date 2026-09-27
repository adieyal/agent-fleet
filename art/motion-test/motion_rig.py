"""Put a Mixamo clip on the robot built from the parts library (robot_parts.py).

Retargeting keeps the Mixamo skeleton but moves its joints to the robot's proportions. Only joint positions
change; every bone keeps its rest orientation, so the clip's local rotations apply unchanged. Root motion is
scaled by leg length (horizontal) and hip height (vertical), then every frame is grounded: the robot is
raised or lowered so its lowest point touches the floor. There is no seat lift: with these legs a seated
robot's feet reach the floor from the floor kit's 0.47 m chair. Walks can be pinned in place (linear drift
removed). Each part is parented rigidly to one bone (no skinning); left parts are mirrored copies of the right.
"""

import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import palette  # noqa: E402

LIB = Path.home() / '.cache/fleet-motion-test/robot_parts.blend'

# robot proportions, metres. The upper body follows robot-sheet.png (helmet about as wide as the shoulders,
# torso a little taller than the helmet, hands at hip height). The legs are longer than the sheet's so that a
# seated robot's feet reach the floor from a 0.47 m seat; SHIN is solved against the seated typing clip.
ANKLE_H, SHIN, THIGH, HIP_X = 0.10, 0.423, 0.21, 0.09
SPINE, NECK, SHOULDER_X = 0.28, 0.05, 0.175
UPPER, FORE, HAND = 0.17, 0.15, 0.19
KNUCKLE = 0.57       # the fingers piece starts this far along the hand (robot_parts.KNUCKLE_Y)
SRC_ANKLE_H = 0.085  # Mixamo ankle height above the floor
JOINT_D = {'arm': 0.105, 'fore': 0.09, 'thigh': 0.11, 'shin': 0.10}  # ball joint diameters

ROLES = {'root': 'Hips', 'spine1': 'Spine', 'spine2': 'Spine1', 'spine3': 'Spine2', 'neck': 'Neck', 'head': 'Head'}
for _s, _side in (('L', 'Left'), ('R', 'Right')):
    for _r, _b in (('collar', 'Shoulder'), ('arm', 'Arm'), ('fore', 'ForeArm'), ('hand', 'Hand'),
                   ('thumb', 'HandThumb1'), ('fingers', 'HandMiddle1'),
                   ('thigh', 'UpLeg'), ('shin', 'Leg'), ('foot', 'Foot'), ('toe', 'ToeBase')):
        ROLES[f'{_r}_{_s}'] = _side + _b
ROLES = {k: 'mixamorig:' + v for k, v in ROLES.items()}

# part, the role it rides on, offset from that role's joint (right-side rest frame, metres; None = special)
MOUNTS = [
    ('pelvis', 'root', None),
    ('waist', 'spine2', None), ('chest', 'spine3', None), ('chest_light', 'spine3', None),
    ('neck', 'neck', None), ('helmet', 'head', None), ('visor', 'head', None), ('ear', 'head', None),
    ('joint', 'arm', (0, 0, 0)), ('upper_arm', 'arm', (-0.055, 0, 0)),
    ('joint', 'fore', (0, 0, 0)), ('forearm', 'fore', (-0.03, 0, 0)),
    ('palm', 'hand', (0.02, 0, 0)), ('fingers', 'fingers', None), ('thumb', 'thumb', None),
    ('joint', 'thigh', (0, 0, 0)), ('thigh', 'thigh', (0, 0, -0.03)),
    ('joint', 'shin', (0, 0, 0)), ('shin', 'shin', (0, 0, -0.05)),
    ('foot', 'foot', (0, 0, 0)),
]
SIDED_ROLES = {'arm', 'fore', 'hand', 'fingers', 'thumb', 'thigh', 'shin', 'foot'}


def sided(role: str, side: str | None) -> str:
    return f'{role}_{side}' if side and f'{role}_{side}' in ROLES else role


def load_clip(path: Path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    for o in new:
        if o is not arm:
            bpy.data.objects.remove(o)
    arm.name = path.stem
    return arm


def frames(arm) -> range:
    a, b = arm.animation_data.action.frame_range
    return range(int(a), int(b) + 1)


def sample(arm, names: list[str]) -> dict:
    sc = bpy.context.scene
    out = {n: [] for n in names}
    for f in frames(arm):
        sc.frame_set(f)
        for n in names:
            out[n].append(arm.matrix_world @ arm.pose.bones[n].head)
    return out


def resize(arm) -> tuple[float, float]:
    """Move joints to robot proportions in edit mode. Returns (vertical, horizontal) root-motion scales."""
    B = ROLES.__getitem__
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones
    Mw = arm.matrix_world
    old = {b.name: Mw @ b.head for b in eb}
    d = lambda a, b: (old[B(b)] - old[B(a)]).length  # noqa: E731
    lat = lambda r: abs(old[B(r)].x - old[B('root')].x)  # noqa: E731
    spine = SPINE / d('root', 'neck')
    scale = {  # scale of the offset from a bone's parent to the bone, by the bone's role
        'spine1': spine, 'spine2': spine, 'spine3': spine, 'neck': spine, 'head': NECK / d('neck', 'head'),
        'collar': SHOULDER_X / lat('arm_L'), 'arm': SHOULDER_X / lat('arm_L'),
        'fore': UPPER / d('arm_L', 'fore_L'), 'hand': FORE / d('fore_L', 'hand_L'),
        'fingers': KNUCKLE * HAND / d('hand_L', 'fingers_L'), 'thumb': KNUCKLE * HAND / d('hand_L', 'fingers_L'),
        'thigh': HIP_X / lat('thigh_L'), 'shin': THIGH / d('thigh_L', 'shin_L'),
        'foot': SHIN / d('shin_L', 'foot_L'), 'toe': 0.10 / d('foot_L', 'toe_L'),
    }
    role_of = {v: k.split('_')[0] for k, v in ROLES.items()}

    def seg_scale(b) -> float:
        role = role_of.get(b.name)
        if role and role != 'root':
            return scale[role]
        p = b.parent  # unmapped bones (finger segments, end bones) follow their nearest mapped ancestor
        while p is not None and p.name not in role_of:
            p = p.parent
        return scale.get(role_of[p.name], 0.6) if p is not None else 0.6

    root = B('root')
    new = {}
    drop = (scale['thigh'] * (old[B('thigh_L')].z - old[root].z)
            + scale['shin'] * (old[B('shin_L')].z - old[B('thigh_L')].z)
            + scale['foot'] * (old[B('foot_L')].z - old[B('shin_L')].z))
    new[root] = Vector((old[root].x, old[root].y, ANKLE_H - drop))

    def walk(b):
        for c in b.children:
            new[c.name] = new[b.name] + seg_scale(c) * (old[c.name] - old[b.name])
            walk(c)
    walk(eb[root])
    Mi = Mw.inverted()
    for b in eb:
        b.use_connect = False
    for b in eb:
        m = b.matrix.copy()
        length = b.length
        m.translation = Mi @ new[b.name]
        b.matrix = m
        b.length = length
    bpy.ops.object.mode_set(mode='OBJECT')
    src_hip = old[root].z - old[B('foot_L')].z + SRC_ANKLE_H
    src_leg = d('thigh_L', 'shin_L') + d('shin_L', 'foot_L')
    return new[root].z / src_hip, (THIGH + SHIN) / src_leg


def strip_channels(arm) -> int:
    """Drop location/scale keys on all bones but the root (the rig is rotation-driven)."""
    root = ROLES['root']
    act = arm.animation_data.action
    drop = [fc for fc in act.fcurves if fc.data_path.startswith('pose.bones')
            and (fc.data_path.endswith('.scale')
                 or (fc.data_path.endswith('.location') and f'"{root}"' not in fc.data_path))]
    for fc in drop:
        act.fcurves.remove(fc)
    return len(drop)


def write_root(arm, P: list) -> None:
    """Key the root bone so its head follows the world positions P (one per frame)."""
    pb = arm.pose.bones[ROLES['root']]
    rest = pb.bone.matrix_local
    Mi = arm.matrix_world.inverted()
    locs = [rest.to_3x3().transposed() @ ((Mi @ p) - rest.translation) for p in P]
    act = arm.animation_data.action
    dp = f'pose.bones["{pb.name}"].location'
    f0 = frames(arm).start
    for ax in range(3):
        fc = act.fcurves.find(dp, index=ax) or act.fcurves.new(dp, index=ax, action_group=pb.name)
        fc.keyframe_points.clear()
        fc.keyframe_points.add(len(locs))
        fc.keyframe_points.foreach_set('co', [c for i, l in enumerate(locs) for c in (f0 + i, l[ax])])
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.update()


def retarget(path: Path, pin: bool, ref: str = 'first'):
    """Load a clip and fit it to the robot. ref 'first' or 'lowest' (most seated frame) sets facing and origin."""
    arm = load_clip(path)
    B = ROLES.__getitem__
    pos = sample(arm, [B('root'), B('thigh_L'), B('thigh_R')])
    zs = [p.z for p in pos[B('root')]]
    i_ref = 0 if ref == 'first' else min(range(len(zs)), key=zs.__getitem__)
    hips = pos[B('thigh_L')][i_ref] - pos[B('thigh_R')][i_ref]
    fwd = hips.cross(Vector((0, 0, 1)))
    yaw = math.atan2(-1, 0) - math.atan2(fwd.y, fwd.x)  # turn so the reference frame faces -Y
    R = Matrix.Rotation(yaw, 4, 'Z')
    stripped = strip_channels(arm)
    kv, kh = resize(arm)
    P = [R @ p for p in pos[B('root')]]
    P = [Vector((p.x * kh, p.y * kh, p.z * kv)) for p in P]
    n = len(P)
    drift = [0.0, 0.0]
    if pin:  # remove the mean horizontal velocity (least-squares line) -> walk in place
        t = [i - (n - 1) / 2 for i in range(n)]
        tt = sum(x * x for x in t) or 1
        for ax in (0, 1):
            m = sum(p[ax] for p in P) / n
            v = sum(ti * p[ax] for ti, p in zip(t, P)) / tt
            drift[ax] = v * 30  # m/s at 30 fps
            for ti, p in zip(t, P):
                p[ax] -= m + v * ti
    off = Vector((P[i_ref].x, P[i_ref].y, 0))
    P = [p - off for p in P]
    arm.matrix_world = R @ arm.matrix_world
    arm['yaw'] = yaw
    write_root(arm, P)
    info = {'root_scale_v': round(kv, 3), 'root_scale_h': round(kh, 3), 'stripped_curves': stripped, 'frames': n,
            'yaw_fix_deg': round(math.degrees(yaw), 1), 'ref_frame': i_ref,
            'pinned_drift_ms': [round(v, 3) for v in drift]}
    return arm, info


def ground(arm, parts) -> dict:
    """Per frame, shift the root vertically so the robot's lowest point sits on the floor (z = 0)."""
    sc = bpy.context.scene
    pts = []
    for p in parts:
        co = np.empty(len(p.data.vertices) * 3)
        p.data.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)[:: max(1, len(p.data.vertices) // 400)]
        pts.append((p, np.c_[co, np.ones(len(co))]))
    root = arm.pose.bones[ROLES['root']]
    P, shift = [], []
    for f in frames(arm):
        sc.frame_set(f)
        low = min((np.array(p.matrix_world) @ c.T)[2].min() for p, c in pts)
        P.append(arm.matrix_world @ root.head - Vector((0, 0, low)))
        shift.append(-low)
    write_root(arm, P)
    return {'ground_shift_m': [round(min(shift), 3), round(max(shift), 3)]}


def attach(arm, tag: str) -> list:
    """Append the parts library, place each part at its joint in the rest pose and parent it to its bone."""
    with bpy.data.libraries.load(str(LIB), link=False) as (src, dst):
        dst.objects = list(src.objects)
    lib = {o.name.split('.')[0]: o for o in dst.objects}
    yawed = arm.matrix_world.copy()  # parts are placed in the rest frame, which faces -Y before the yaw fix
    arm.matrix_world = Matrix.Rotation(-arm.get('yaw', 0.0), 4, 'Z') @ yawed
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    Mw = arm.matrix_world
    J = {r: Mw @ arm.data.bones[b].head_local for r, b in ROLES.items()}
    dims = {k: Vector(o['hi_m']) - Vector(o['lo_m']) for k, o in lib.items()}
    parts = []

    def place(key: str, role: str, side: str | None, at: Vector, size: float = 1.0):
        mirror = side == 'L' and key != 'joint'
        ob = lib[key].copy()
        ob.data = lib[key].data.copy() if mirror else lib[key].data
        if mirror:
            ob.data.transform(Matrix.Scale(-1, 4, Vector((1, 0, 0))))
            ob.data.flip_normals()
        ob.name = f'{tag}_{key}_{role}' + (f'_{side}' if side else '')
        bpy.context.scene.collection.objects.link(ob)
        ob.parent, ob.parent_type, ob.parent_bone = arm, 'BONE', ROLES[sided(role, side)]
        bpy.context.view_layer.update()
        ob.matrix_world = Matrix.Translation(at) @ Matrix.Scale(size, 4)
        parts.append(ob)

    hd, cd, vd = dims['helmet'], dims['chest'], dims['visor']
    chest_bottom = J['arm_L'].z - 0.53 * cd.z  # the arm sockets sit about half-way up the chest
    head = Vector((J['root'].x, J['root'].y, chest_bottom + cd.z - 0.02))  # helmet rim sits on the chest
    waist_z = chest_bottom + 0.012 - lib['waist']['hi_m'][2]  # waist band's top 1.2 cm up inside the chest
    for key, role, off in MOUNTS:
        sides = ('R', 'L') if role in SIDED_ROLES or key == 'ear' else (None,)
        for side in sides:
            sx = -1 if side == 'R' else 1
            j = J[sided(role, side)]
            size = JOINT_D[role] if key == 'joint' else 1.0
            if off is not None:
                at = j + Vector((-sx * off[0], off[1], off[2]))  # offsets are written for the right side
            elif key == 'chest':
                at = Vector((J['root'].x, J['root'].y, chest_bottom))
            elif key == 'chest_light':
                at = Vector((J['root'].x, J['root'].y - cd.y * 0.40, chest_bottom + cd.z * 0.52))
            elif key == 'waist':
                at = Vector((J['root'].x, J['root'].y, waist_z))
            elif key == 'pelvis':  # its top tucks 1.5 cm under the waist band
                at = Vector((J['root'].x, J['root'].y, waist_z + lib['waist']['lo_m'][2] + 0.015
                             - lib['pelvis']['hi_m'][2]))
            elif key == 'neck':
                at = head + Vector((0, 0, -0.005))
            elif key == 'helmet':
                at = head
            elif key == 'visor':
                at = head + Vector((0, -hd.y * 0.30, hd.z * 0.46))
            elif key == 'ear':
                at = head + Vector((sx * hd.x * 0.485, 0.01, hd.z * 0.45))
            else:  # fingers, thumb: the palm's rest placement, pivoting on their own bones
                at = J[sided('hand', side)] + Vector((-sx * 0.02, 0, 0))
            place(key, role, side, at, size)
    for side, sx in (('L', 1), ('R', -1)):  # the visor has no eyes: two glowing capsules
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.03, segments=24, ring_count=12)
        eye = bpy.context.active_object
        eye.scale = (0.7, 0.3, 1.35)
        bpy.ops.object.transform_apply(scale=True)
        for pl in eye.data.polygons:
            pl.use_smooth = True
        eye.name = f'{tag}_eye_{side}'
        eye.data.materials.append(eye_material())
        eye.parent, eye.parent_type, eye.parent_bone = arm, 'BONE', ROLES['head']
        bpy.context.view_layer.update()
        eye.matrix_world = Matrix.Translation(head + Vector((sx * 0.062, -hd.y * 0.30 - vd.y * 0.5 + 0.004,
                                                             hd.z * 0.47)))
        parts.append(eye)
    for o in dst.objects:
        if not o.users_collection:
            bpy.data.objects.remove(o)
    arm.data.pose_position = 'POSE'
    arm.matrix_world = yawed
    arm.hide_render = True
    return parts


def eye_material() -> bpy.types.Material:
    m = bpy.data.materials.get('eye_glow')
    if m:
        return m
    m = bpy.data.materials.new('eye_glow')
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = palette.lin('glow')
    b.inputs['Emission Color'].default_value = palette.lin('glow')
    b.inputs['Emission Strength'].default_value = 1.0
    return m


def tint(hex_colour: str) -> None:
    """Recolour the host: every 'host_tint' node (the teal shells) takes the new colour."""
    for m in bpy.data.materials:
        if m.use_nodes and 'host_tint' in m.node_tree.nodes:
            m.node_tree.nodes['host_tint'].outputs[0].default_value = palette.lin(hex_colour)
