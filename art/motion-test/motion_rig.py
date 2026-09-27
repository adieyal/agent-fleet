"""Put a Mixamo or HY-Motion clip on the robot built from the parts library (robot_parts.py).

Retargeting keeps each source skeleton but moves its joints to the robot's proportions (read off
robot-sheet.png: ~1.0 m tall, helmet ~1/3 of it, short legs, hands at hip height). Only joint positions
change; every bone keeps its rest orientation, so the clip's local rotations apply unchanged. The root's
motion is scaled by the leg-length ratio, walks are pinned in place (linear drift removed), and each clip is
turned to face -Y and moved so its reference frame's pelvis sits at the origin. Each part is parented
rigidly to one bone (no skinning); left-side parts are mirrored copies of the right.
"""

import math
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

LIB = Path.home() / '.cache/fleet-motion-test/robot_parts.blend'

# robot proportions in sheet units (1.0 = the sheet robot at a 0.42 m helmet), scaled by S to the floor
# robot's size: art/build/robot/robot.blend stands 1.33 m, and the bench desk (0.27 m above the seat) is out
# of reach for a seated robot any smaller.
S = 1.3
ANKLE_H, SHIN, THIGH, HIP_X = (S * v for v in (0.075, 0.125, 0.135, 0.075))
SPINE, NECK, SHOULDER_X = (S * v for v in (0.22, 0.03, 0.15))
UPPER, FORE, HAND = (S * v for v in (0.115, 0.11, 0.085))
SRC_ANKLE_H = 0.085  # human ankle height above the floor

ROLES = {  # role: (mixamo, smpl-h)
    'root': ('Hips', 'Pelvis'), 'spine1': ('Spine', 'Spine1'), 'spine2': ('Spine1', 'Spine2'),
    'spine3': ('Spine2', 'Spine3'), 'neck': ('Neck', 'Neck'), 'head': ('Head', 'Head'),
    **{f'{r}_{s}': (f'{m}{side}{mb}', f'{s}_{sb}') for s, side in (('L', 'Left'), ('R', 'Right'))
       for r, mb, sb, m in (('collar', 'Shoulder', 'Collar', ''), ('arm', 'Arm', 'Shoulder', ''),
                            ('fore', 'ForeArm', 'Elbow', ''), ('hand', 'Hand', 'Wrist', ''),
                            ('thigh', 'UpLeg', 'Hip', ''), ('shin', 'Leg', 'Knee', ''),
                            ('foot', 'Foot', 'Ankle', ''), ('toe', 'ToeBase', 'Foot', ''))},
}
for k, (m, s) in ROLES.items():
    ROLES[k] = ('mixamorig:' + m, s)

# part key -> (role it rides on, which joint it sits at: 'head' of that role's bone)
MOUNTS = [
    ('pelvis', 'root'), ('waist', 'spine2'), ('chest', 'spine3'), ('chest_light', 'spine3'), ('neck', 'neck'),
    ('helmet', 'head'), ('visor', 'head'), ('ear', 'head'),
    ('shoulder', 'arm'), ('upper_arm', 'arm'), ('elbow', 'fore'), ('forearm', 'fore'), ('hand', 'hand'),
    ('hip', 'thigh'), ('thigh', 'thigh'), ('knee', 'shin'), ('shin', 'shin'), ('ankle', 'foot'), ('foot', 'foot'),
]
SIDED = {'ear', 'shoulder', 'upper_arm', 'elbow', 'forearm', 'hand', 'hip', 'thigh', 'knee', 'shin', 'ankle', 'foot'}


def bone(arm, kind: str, role: str) -> str:
    return ROLES[role][0 if kind == 'mixamo' else 1]


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
    kind = 'mixamo' if 'mixamorig:Hips' in arm.data.bones else 'smpl'
    arm.name = f'{path.stem}_{kind}'
    return arm, kind


def frames(arm) -> range:
    a, b = arm.animation_data.action.frame_range
    return range(int(a), int(b) + 1)


def sample(arm, names: list[str]) -> dict:
    """World head position of each named bone at every frame."""
    sc = bpy.context.scene
    out = {n: [] for n in names}
    for f in frames(arm):
        sc.frame_set(f)
        for n in names:
            out[n].append(arm.matrix_world @ arm.pose.bones[n].head)
    return out


def resize(arm, kind: str) -> float:
    """Move joints to robot proportions in edit mode; returns the leg-length ratio (robot / source)."""
    B = lambda r: bone(arm, kind, r)  # noqa: E731
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones
    Mw = arm.matrix_world
    old = {b.name: Mw @ b.head for b in eb}
    d = lambda a, b: (old[B(b)] - old[B(a)]).length  # noqa: E731
    hand_len = max((old[c.name] - old[B('hand_L')]).length for c in eb[B('hand_L')].children_recursive) * 1.05
    scale = {  # scale of the offset from a bone's parent to the bone, by the bone's role
        'spine1': SPINE / d('root', 'neck'), 'spine2': SPINE / d('root', 'neck'),
        'spine3': SPINE / d('root', 'neck'), 'neck': SPINE / d('root', 'neck'), 'head': NECK / d('neck', 'head'),
        'collar': SHOULDER_X / abs(old[B('arm_L')].x - old[B('root')].x),
        'arm': SHOULDER_X / abs(old[B('arm_L')].x - old[B('root')].x),
        'fore': UPPER / d('arm_L', 'fore_L'), 'hand': FORE / d('fore_L', 'hand_L'),
        'thigh': HIP_X / abs(old[B('thigh_L')].x - old[B('root')].x),
        'shin': THIGH / d('thigh_L', 'shin_L'), 'foot': SHIN / d('shin_L', 'foot_L'),
        'toe': 0.08 * S / d('foot_L', 'toe_L'),
    }
    by_name = {}
    for role in ROLES:
        by_name[B(role)] = role.split('_')[0]
    inherit = {'hand': HAND / hand_len, 'head': 0.6, 'toe': scale['toe'], 'neck': scale['head']}

    def seg_scale(b) -> float:
        role = by_name.get(b.name)
        if role and role != 'root':
            return scale[role]
        p = b.parent
        while p is not None and by_name.get(p.name) is None:
            p = p.parent
        return inherit.get(by_name.get(p.name) if p else None, 1.0)

    new = {}
    root = eb[B('root')]
    # solve root height so the ankles land at ANKLE_H
    ank = old[B('foot_L')]
    drop = (scale['thigh'] * (old[B('thigh_L')].z - old[B('root')].z)
            + scale['shin'] * (old[B('shin_L')].z - old[B('thigh_L')].z)
            + scale['foot'] * (old[B('foot_L')].z - old[B('shin_L')].z))
    new[root.name] = Vector((old[root.name].x, old[root.name].y, ANKLE_H - drop))

    def walk(b):
        for c in b.children:
            new[c.name] = new[b.name] + seg_scale(c) * (old[c.name] - old[b.name])
            walk(c)
    walk(root)
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
    rn = B('root')  # edit bones are invalid once back in object mode
    src_leg = old[rn].z - ank.z + SRC_ANKLE_H
    return new[rn].z / src_leg


def strip_channels(arm, kind: str) -> int:
    """Drop location/scale keys on all bones but the root (the rigs are rotation-driven)."""
    root = bone(arm, kind, 'root')
    act = arm.animation_data.action
    drop = [fc for fc in act.fcurves if fc.data_path.startswith('pose.bones')
            and (fc.data_path.endswith('.scale') or (fc.data_path.endswith('.location') and f'"{root}"' not in fc.data_path))]
    for fc in drop:
        act.fcurves.remove(fc)
    return len(drop)


def retarget(path: Path, pin: bool, ref: str = 'first'):
    """Load a clip and fit it to the robot. ref: 'first' or 'lowest' (the most seated frame) sets the origin."""
    arm, kind = load_clip(path)
    B = lambda r: bone(arm, kind, r)  # noqa: E731
    pos = sample(arm, [B('root'), B('thigh_L'), B('thigh_R')])
    zs = [p.z for p in pos[B('root')]]
    i_ref = 0 if ref == 'first' else min(range(len(zs)), key=zs.__getitem__)
    hips = pos[B('thigh_L')][i_ref] - pos[B('thigh_R')][i_ref]
    fwd = hips.cross(Vector((0, 0, 1)))
    yaw = math.atan2(-1, 0) - math.atan2(fwd.y, fwd.x)  # turn so the reference frame faces -Y
    R = Matrix.Rotation(yaw, 4, 'Z')
    stripped = strip_channels(arm, kind)
    k = resize(arm, kind)
    P = [R @ p for p in pos[B('root')]]
    P = [Vector((p.x * k, p.y * k, p.z * k)) for p in P]
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
    # write root location keys
    pb = arm.pose.bones[B('root')]
    rest = pb.bone.matrix_local
    Mi = arm.matrix_world.inverted()
    locs = [rest.to_3x3().transposed() @ ((Mi @ p) - rest.translation) for p in P]
    act = arm.animation_data.action
    dp = f'pose.bones["{pb.name}"].location'
    f0 = frames(arm).start
    for ax in range(3):
        fc = act.fcurves.find(dp, index=ax) or act.fcurves.new(dp, index=ax, action_group=pb.name)
        fc.keyframe_points.clear()
        fc.keyframe_points.add(n)
        fc.keyframe_points.foreach_set('co', [c for i, l in enumerate(locs) for c in (f0 + i, l[ax])])
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.update()
    info = {'kind': kind, 'leg_ratio': round(k, 3), 'stripped_curves': stripped, 'frames': n,
            'yaw_fix_deg': round(math.degrees(yaw), 1), 'ref_frame': i_ref,
            'pinned_drift_ms': [round(v, 3) for v in drift]}
    return arm, kind, info


def attach(arm, kind: str, tag: str) -> list:
    """Append the parts library, place each part at its joint in the rest pose and parent it to its bone."""
    with bpy.data.libraries.load(str(LIB), link=False) as (src, dst):
        dst.objects = list(src.objects)
    lib = {o.name.split('.')[0]: o for o in dst.objects}
    yawed = arm.matrix_world.copy()  # parts are placed in the rest frame, which faces -Y before the yaw fix
    arm.matrix_world = Matrix.Rotation(-arm.get('yaw', 0.0), 4, 'Z') @ yawed
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    Mw = arm.matrix_world
    J = {r: Mw @ arm.data.bones[bone(arm, kind, r)].head_local for r in ROLES}
    helmet_dims = Vector(lib['helmet']['dims_m']) * S
    chest_dims = Vector(lib['chest']['dims_m']) * S
    parts = []

    def place(key: str, role: str, side: str | None, at: Vector, mirror: bool):
        ob = lib[key].copy()
        ob.data = lib[key].data.copy() if mirror else lib[key].data
        if mirror:
            ob.data.transform(Matrix.Scale(-1, 4, Vector((1, 0, 0))))
            ob.data.flip_normals()
        ob.name = f'{tag}_{key}' + (f'_{side}' if side else '')
        bpy.context.scene.collection.objects.link(ob)
        ob.parent = arm
        ob.parent_type = 'BONE'
        ob.parent_bone = bone(arm, kind, sided(role, side))
        bpy.context.view_layer.update()
        ob.matrix_world = Matrix.Translation(at) @ Matrix.Scale(S, 4)
        parts.append(ob)

    head = J['neck'] + Vector((0, 0, 0.035 * S))
    for key, role in MOUNTS:
        sides = ('R', 'L') if key in SIDED else (None,)
        for side in sides:
            mirror = side == 'L'
            sx = -1 if side == 'R' else 1
            j = J[sided(role, side)]
            at = j.copy()
            if key == 'chest':
                at = Vector((J['root'].x, J['root'].y, J['spine3'].z - 0.06 * S))
            elif key == 'chest_light':
                at = Vector((J['root'].x, J['root'].y - chest_dims.y * 0.42, J['spine3'].z + 0.03 * S))
            elif key == 'waist':
                at = Vector((J['root'].x, J['root'].y, (J['spine3'].z + J['root'].z + S * (0.035 - 0.06)) / 2))
            elif key == 'neck':
                at = J['neck'] + Vector((0, 0, 0.0))
            elif key == 'helmet':
                at = head
            elif key == 'visor':
                at = head + Vector((0, -helmet_dims.y * 0.34, helmet_dims.z * 0.45))
            elif key == 'ear':
                at = head + Vector((sx * helmet_dims.x * 0.47, 0.02 * S, helmet_dims.z * 0.47))
            elif key == 'pelvis':
                at = J['root'] + Vector((0, 0, -0.01 * S))
            place(key, role, side, at, mirror)
    for side, sx in (('L', 1), ('R', -1)):  # eyes: the visor has none, so two glowing capsules
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.022 * S, segments=16, ring_count=8)
        eye = bpy.context.active_object
        eye.scale = (0.75, 0.35, 1.3)
        bpy.ops.object.transform_apply(scale=True)
        eye.name = f'{tag}_eye_{side}'
        eye.data.materials.append(eye_material())
        eye.parent, eye.parent_type, eye.parent_bone = arm, 'BONE', bone(arm, kind, 'head')
        bpy.context.view_layer.update()
        eye.matrix_world = Matrix.Translation(head + Vector((sx * 0.055 * S, -helmet_dims.y * 0.34 - 0.062 * S,
                                                             helmet_dims.z * 0.47)))
        parts.append(eye)
    for o in dst.objects:
        if o.users_collection == ():
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
    b.inputs['Base Color'].default_value = (0.3, 0.9, 1.0, 1)
    b.inputs['Emission Color'].default_value = (0.3, 0.9, 1.0, 1)
    b.inputs['Emission Strength'].default_value = 6.0
    return m


def tint(hex_colour: str) -> None:
    c = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    for m in bpy.data.materials:
        if m.use_nodes and 'host_tint' in m.node_tree.nodes:
            m.node_tree.nodes['host_tint'].inputs[7].default_value = (*lin, 1)  # colour B
