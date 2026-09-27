"""Put a Mixamo clip on the robot rebuilt from the whole-body model (robot_body.py, robot_hands.py).

Retargeting keeps the Mixamo skeleton but moves its joints onto the robot: every mapped joint goes to the
position measured on the model (robot_body.json), with the arms laid out straight in Mixamo's T-pose at the
model's upper-arm and forearm lengths. Only joint positions change; every bone keeps its rest orientation, so
the clip's local rotations apply unchanged. Root motion is scaled by leg length (horizontal) and hip height
(vertical), then every frame is grounded: the robot is raised or lowered so its lowest point touches the floor.
Walks can be pinned in place (linear drift removed).

Pieces are rigid, each parented to one bone (no skinning). The model stands in an A-pose, so the arm bones are
first posed to the model's arm directions and the arm pieces parented there; the posed hands are canonical
T-pose hands and are parented at rest. The hands are swapped per clip (HAND_POSE).
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import palette  # noqa: E402

CACHE = Path.home() / '.cache/fleet-motion-test'
BODY, HANDS = CACHE / 'robot_body.blend', CACHE / 'robot_hands.blend'
SRC_ANKLE_H = 0.085  # Mixamo ankle height above the floor

ROLES = {'root': 'Hips', 'spine1': 'Spine', 'spine2': 'Spine1', 'spine3': 'Spine2', 'neck': 'Neck', 'head': 'Head'}
for _s, _side in (('L', 'Left'), ('R', 'Right')):
    for _r, _b in (('collar', 'Shoulder'), ('arm', 'Arm'), ('fore', 'ForeArm'), ('hand', 'Hand'),
                   ('thigh', 'UpLeg'), ('shin', 'Leg'), ('foot', 'Foot'), ('toe', 'ToeBase')):
        ROLES[f'{_r}_{_s}'] = _side + _b
ROLES = {k: 'mixamorig:' + v for k, v in ROLES.items()}

# hand pose per Mixamo clip (robot_hands.HANDS); anything not listed holds the open hand
HAND_POSE = {
    'walk': 'fist', 'walk-normal': 'fist', 'box-idle': 'cupped', 'box-walk-arc': 'cupped',
    'thumbs-up-standing': 'thumbs_up', 'thumbs-up-sitting': 'thumbs_up', 'writing-seated': 'pinch',
    'standing-reading-phone': 'book', 'walking-reading-phone': 'book', 'waving': 'open', 'typing': 'open',
}
TWO_HANDED = {'book'}  # one model holds both hands: carried by the right hand bone


def body_meta() -> dict:
    return json.loads(BODY.with_suffix('.json').read_text())


def targets() -> dict:
    """Mixamo rest (T-pose) joint targets in metres, robot frame (facing -Y, soles at z = 0)."""
    J = {k: Vector(v) for k, v in body_meta()['joints_m'].items()}
    t = {'root': J['root'], 'spine1': J['spine'], 'spine2': (J['spine'] + J['chest']) / 2, 'spine3': J['chest'],
         'neck': J['neck'], 'head': J['head']}
    for s, sx in (('L', 1), ('R', -1)):
        sh = J[f'shoulder_{s}']
        upper = (J[f'elbow_{s}'] - sh).length
        fore = (J[f'wrist_{s}'] - J[f'elbow_{s}']).length
        t[f'collar_{s}'] = Vector((sh.x * 0.35, sh.y, sh.z + 0.01))
        t[f'arm_{s}'] = sh
        t[f'fore_{s}'] = sh + Vector((sx * upper, 0, 0))
        t[f'hand_{s}'] = t[f'fore_{s}'] + Vector((sx * fore, 0, 0))
        t[f'thigh_{s}'], t[f'shin_{s}'] = J[f'hip_{s}'], J[f'knee_{s}']
        t[f'foot_{s}'], t[f'toe_{s}'] = J[f'ankle_{s}'], J[f'toe_{s}']
    return t


def load_clip(path: Path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    for o in new:
        if o is not arm:
            bpy.data.objects.remove(o)
    arm.name = path.stem
    arm['clip'] = path.stem
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
    """Move the mapped joints to the robot's targets in edit mode (world robot frame, before any yaw), and
    the unmapped ones (fingers, end bones) by their nearest mapped ancestor's scale. Returns the (vertical,
    horizontal) root-motion scales."""
    T = targets()
    B = ROLES.__getitem__
    role_of = {v: k for k, v in ROLES.items()}
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones
    Mw = arm.matrix_world
    old = {b.name: Mw @ b.head for b in eb}
    new = {}

    def ratio(role) -> float:
        """How much the segment ending at this role's joint was scaled (for unmapped children)."""
        b = eb[B(role)]
        p = b.parent
        if p is None or p.name not in role_of:
            return 1.0
        o = (old[b.name] - old[p.name]).length
        return (T[role] - T[role_of[p.name]]).length / o if o > 1e-6 else 1.0

    def walk(b, parent_ratio):
        role = role_of.get(b.name)
        if role:
            new[b.name] = T[role].copy()
            r = ratio(role) if role != 'root' else 1.0
        else:
            new[b.name] = new[b.parent.name] + parent_ratio * (old[b.name] - old[b.parent.name])
            r = parent_ratio
        for c in b.children:
            walk(c, r)
    walk(eb[B('root')], 1.0)
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
    root = B('root')
    src_hip = old[root].z - old[B('foot_L')].z + SRC_ANKLE_H
    src_leg = (old[B('shin_L')] - old[B('thigh_L')]).length + (old[B('foot_L')] - old[B('shin_L')]).length
    leg = (T['shin_L'] - T['thigh_L']).length + (T['foot_L'] - T['shin_L']).length
    return T['root'].z / src_hip, leg / src_leg


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


def append(path: Path, names: list[str]) -> dict:
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n in names]
    return {o.name: o for o in dst.objects}


def pose_arms_to_model(arm) -> None:
    """Pose the upper-arm and forearm bones along the model's A-pose arms (elbow and wrist directions)."""
    J = {k: Vector(v) for k, v in body_meta()['joints_m'].items()}
    Mi = arm.matrix_world.inverted()
    for s in 'LR':
        for role, a, b in (('arm', 'shoulder', 'elbow'), ('fore', 'elbow', 'wrist')):
            pb = arm.pose.bones[ROLES[f'{role}_{s}']]
            bpy.context.view_layer.update()
            child = arm.pose.bones[ROLES[f'{"fore" if role == "arm" else "hand"}_{s}']]
            cur = (child.head - pb.head).normalized()          # armature space, as posed so far
            want = ((Mi @ J[f'{b}_{s}']) - (Mi @ J[f'{a}_{s}'])).normalized()
            q = cur.rotation_difference(want)
            m = pb.matrix.copy()
            head = m.translation.copy()
            m = q.to_matrix().to_4x4() @ Matrix.Translation(-head) @ m
            m.translation = head
            pb.matrix = m
    bpy.context.view_layer.update()


def attach(arm, tag: str, hand_pose: str | None = None) -> list:
    """Parent the body pieces (arms in the model's A-pose) and the clip's posed hands (at rest) to their bones."""
    meta = body_meta()
    pose = hand_pose or HAND_POSE.get(arm.get('clip', ''), 'open')
    pieces = {k: v for k, v in meta['pieces'].items() if not k.startswith('hand_')}  # the model's own mitts go
    lib = append(BODY, [f'body_{k}' for k in pieces])
    yawed = arm.matrix_world.copy()  # the pieces sit in the robot frame, which faces -Y before the yaw fix
    arm.matrix_world = Matrix.Rotation(-arm.get('yaw', 0.0), 4, 'Z') @ yawed
    action = arm.animation_data.action
    arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix()
    bpy.context.view_layer.update()
    parts = []

    def parent(ob, role_key):
        bpy.context.scene.collection.objects.link(ob)
        mw = ob.matrix_world.copy()
        ob.parent, ob.parent_type, ob.parent_bone = arm, 'BONE', ROLES[role_key]
        bpy.context.view_layer.update()
        ob.matrix_world = mw
        parts.append(ob)

    # hands first, at rest (T-pose): canonical hands sit at the wrist, fingers along the arm
    hands = append(HANDS, [f'hand_{pose}_{s}' for s in 'LR'])
    for s in ('R',) if pose in TWO_HANDED else ('L', 'R'):
        h = hands[f'hand_{pose}_{s}']
        h.name = f'{tag}_hand_{pose}_{s}'
        h.matrix_world = Matrix.Translation(arm.matrix_world @ arm.data.bones[ROLES[f'hand_{s}']].head_local)
        parent(h, f'hand_{s}')
    pose_arms_to_model(arm)
    for key, info in pieces.items():
        ob = lib[f'body_{key}']
        ob.name = f'{tag}_{key}'
        role = info['role'] + (f'_{info["side"]}' if info['side'] else '')
        parent(ob, role)
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix()
    arm.animation_data.action = action
    arm.matrix_world = yawed
    arm.hide_render = True
    arm['hand_pose'] = pose
    return parts


def tint(hex_colour: str) -> None:
    """Recolour the host: every 'host_tint' node (the teal shells) takes the new colour."""
    for m in bpy.data.materials:
        if m.use_nodes and 'host_tint' in m.node_tree.nodes:
            m.node_tree.nodes['host_tint'].outputs[0].default_value = palette.lin(hex_colour)
