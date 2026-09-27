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

# hand pose per Mixamo clip (robot_hands.HANDS); anything not listed (idle, walking, typing) holds the fist. The
# spread open hand reads as a claw, so it is kept for waving only.
HAND_POSE = {
    'waving': 'open', 'box-idle': 'box', 'box-walk-arc': 'box',
    'thumbs-up-standing': 'thumbs_up', 'thumbs-up-sitting': 'thumbs_up', 'writing-seated': 'pinch',
    'standing-reading-phone': 'sheet', 'walking-reading-phone': 'sheet', 'reading-seated': 'book',
}
HELD = {'book': 'pinch', 'box': 'cupped'}  # hand poses that hold a modelled prop (motion_rig.hold): the hands used
SHEET = dict(paper='#f2efe6', ink='#8f9090', aspect=1.35, curl=0.012)  # the modelled sheet between pinch hands
# layered clips: a seated base with the arms, neck and head of another clip
LAYERED = {'reading-seated': ('sitting-idle', 'standing-reading-phone')}
UPPER = ['Neck', 'Head'] + [f'{s}{b}' for s in ('Left', 'Right') for b in
                              ('Shoulder', 'Arm', 'ForeArm', 'Hand')]


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


def import_armature(path: Path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    for o in new:
        if o is not arm:
            bpy.data.objects.remove(o)
    return arm


def load_clip(path: Path):
    """A Mixamo clip, or a layered one (LAYERED, named by a path stem that is not a file): the seated base clip
    with the other clip's upper-body rotations copied over it, looped to the base's length."""
    name = path.stem
    base, over = LAYERED.get(name, (name, None))
    arm = import_armature(path.with_name(f'{base}.fbx'))
    if over:
        src = import_armature(path.with_name(f'{over}.fbx'))
        act, oact = arm.animation_data.action, src.animation_data.action
        f0, f1 = (int(f) for f in act.frame_range)
        o0, o1 = (int(f) for f in oact.frame_range)
        for b in UPPER:
            bone = 'mixamorig:' + b
            for fc in [fc for fc in act.fcurves if fc.data_path.startswith(f'pose.bones["{bone}"].rotation')]:
                act.fcurves.remove(fc)
            for ofc in [fc for fc in oact.fcurves if fc.data_path.startswith(f'pose.bones["{bone}"].rotation')]:
                fc = act.fcurves.new(ofc.data_path, index=ofc.array_index, action_group=bone)
                vals = [ofc.evaluate(o0 + (f - f0) % (o1 - o0 + 1)) for f in range(f0, f1 + 1)]
                fc.keyframe_points.add(len(vals))
                fc.keyframe_points.foreach_set('co', [c for i, v in enumerate(vals) for c in (f0 + i, v)])
                fc.update()
        bpy.data.objects.remove(src)
    arm.name = name
    arm['clip'] = name
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


def upright(arm, keep: float, hips: float = 1.0) -> None:
    """Keep only part of the clip's spine, neck and head rotation (slerp towards rest): Mixamo's seated clips lean
    over a human desk, which buries this big-headed robot's face in the desk. hips < 1 also eases the pelvis's
    forward tilt (the root keeps its facing: retarget turns the whole armature, not the root bone)."""
    act = arm.animation_data.action
    for b in ('Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head'):
        k = hips if b == 'Hips' else keep
        if k >= 1.0:
            continue
        dp = f'pose.bones["mixamorig:{b}"].rotation_quaternion'
        fcs = [act.fcurves.find(dp, index=i) for i in range(4)]
        if not all(fcs):
            continue
        from mathutils import Quaternion
        for f in frames(arm):
            q = Quaternion([fc.evaluate(f) for fc in fcs])
            q = Quaternion().slerp(q, k)
            for i, fc in enumerate(fcs):
                fc.keyframe_points.insert(f, q[i], options={'FAST'})
        for fc in fcs:
            fc.update()


def seat(arm, parts, seat_z: float, seat_front_y: float) -> dict:
    """Per frame, shift the root vertically so the lowest point over the seat (pelvis, thighs and their ball joints,
    behind the seat's front edge) rests on the seat top; the lower legs hang."""
    sc = bpy.context.scene
    over = [p for p in parts if p.name.endswith('_pelvis') or '_thigh_' in p.name or '_ball_hip' in p.name
            or '_ball_knee' in p.name]
    cos = []
    for p in over:
        co = np.empty(len(p.data.vertices) * 3)
        p.data.vertices.foreach_get('co', co)
        cos.append((p, np.c_[co.reshape(-1, 3)[::2], np.ones(len(co) // 3)[::2]]))
    root = arm.pose.bones[ROLES['root']]
    P, shift = [], []
    for f in frames(arm):
        sc.frame_set(f)
        low = min(float(w[2][w[1] > seat_front_y].min()) if (w[1] > seat_front_y).any() else 9.0
                  for w in ((np.array(p.matrix_world) @ c.T) for p, c in cos))
        P.append(arm.matrix_world @ root.head + Vector((0, 0, seat_z - low)))
        shift.append(seat_z - low)
    write_root(arm, P)
    return {'seat_shift_m': [round(min(shift), 3), round(max(shift), 3)]}


def reach(arm, parts, goals: dict, rest_z: float | None = None, over_y: float = 1e9) -> dict:
    """IK the arms (upper arm and forearm) to goals. goals: side -> (point, follow): the wrist goes to the point
    plus `follow` times the clip's own wrist motion (about its mean). The elbows are drawn out, back and down,
    behind a desk's far edge where its top hides them from the floor camera. With rest_z, each frame's goals are
    raised or lowered until the hands' lowest point over the desk (y < over_y) sits at rest_z: hands on the desk."""
    sc = bpy.context.scene
    fr = list(frames(arm))
    Mw = arm.matrix_world
    wrist = {s: [] for s in goals}
    shoulder = {s: [] for s in goals}
    for f in fr:
        sc.frame_set(f)
        for s in goals:
            wrist[s].append(Mw @ arm.pose.bones[ROLES[f'hand_{s}']].head)
            shoulder[s].append(Mw @ arm.pose.bones[ROLES[f'arm_{s}']].head)
    empties = {}
    for s, (point, follow) in goals.items():
        sx = 1 if s == 'L' else -1
        mean = sum(wrist[s], Vector()) / len(fr)
        tgt = bpy.data.objects.new(f'ik_{arm.name}_{s}', None)
        pole = bpy.data.objects.new(f'pole_{arm.name}_{s}', None)
        for o in (tgt, pole):
            sc.collection.objects.link(o)
        for f, w, sh in zip(fr, wrist[s], shoulder[s]):
            tgt.location = point + (w - mean) * follow
            tgt.keyframe_insert('location', frame=f)
            pole.location = sh + Vector((sx * 0.3, 0.3, -0.25))  # elbows out, back and down, behind the desk edge
            pole.keyframe_insert('location', frame=f)
        c = arm.pose.bones[ROLES[f'fore_{s}']].constraints.new('IK')
        c.target, c.pole_target, c.chain_count, c.pole_angle = tgt, pole, 2, math.radians(-90)
        empties[s] = tgt
    info = {}
    if rest_z is not None:
        arms = [p for p in parts if '_hand_' in p.name]
        idx = {p: np.arange(0, len(p.data.vertices), 6) for p in arms}
        co = {p: np.c_[np.array([p.data.vertices[i].co for i in idx[p]]), np.ones(len(idx[p]))] for p in arms}
        fixes = []
        for _ in range(2):  # per frame; the arm's pose shifts with the goal, so settle twice
            zfc = [next(fc for fc in e.animation_data.action.fcurves if fc.array_index == 2) for e in empties.values()]
            for k, f in enumerate(fr):
                sc.frame_set(f)
                w = np.concatenate([(np.array(p.matrix_world) @ co[p].T)[:3].T for p in arms])
                over = w[w[:, 1] < over_y]
                d = rest_z - float(over[:, 2].min()) if len(over) else 0.0
                for fc in zfc:
                    fc.keyframe_points[k].co.y += d
                fixes.append(d)
            for fc in zfc:
                fc.update()
        info['rest_fix_m'] = [round(min(fixes), 3), round(max(fixes), 3)]
    return info


def desk_arms(arm, parts, goals: dict, desk_z: float, over_y: float) -> dict:
    """Arms on a desk, posed directly per frame (these short arms have their shoulders at desk height, so an IK
    chain folds them upright): the upper arm reaches forward to an elbow at the desk's near edge (over_y), the
    forearm lies along the desk top towards the wrist goal, and the hand continues it, palm down. goals: side ->
    (wrist point on the desk, follow), the wrist following `follow` times the clip's own wrist motion. Each frame
    the forearm and hand are raised or lowered until their lowest point over the desk rests on its top."""
    sc = bpy.context.scene
    fr = list(frames(arm))
    Mw = arm.matrix_world
    B = ROLES
    wrist = {s: [] for s in goals}
    for f in fr:
        sc.frame_set(f)
        for s in goals:
            wrist[s].append(Mw @ arm.pose.bones[B[f'hand_{s}']].head)
    means = {s: sum(wrist[s], Vector()) / len(fr) for s in goals}
    rest = {}
    for s in goals:  # rest (T-pose) world frames: each bone's direction to its child, and the world's down
        for role, child in (('arm', 'fore'), ('fore', 'hand'), ('hand', 'fingers')):
            b = arm.data.bones[B[f'{role}_{s}']]
            head = Mw @ b.head_local
            tip = Mw @ arm.data.bones[B[f'{child}_{s}']].head_local if f'{child}_{s}' in B else Mw @ b.tail_local
            rest[(role, s)] = ((Mw @ b.matrix_local).to_3x3(), (tip - head).normalized())
    by_side = {s: [p for p in parts if p.name[-2:] == f'_{s}' and any(k in p.name for k in
               ('_upper_arm_', '_ball_elbow_', '_forearm_', '_hand_'))] for s in goals}
    cos = {}
    for side in by_side.values():
        for p in side:
            c = np.array([p.data.vertices[i].co for i in range(0, len(p.data.vertices), 5)])
            cos[p] = np.c_[c, np.ones(len(c))]
    down = Vector((0, 0, -1))

    def frame_for(d: Vector, R_rest, u_rest: Vector):
        n_r = (down - u_rest * down.dot(u_rest)).normalized()
        n_d = (down - d * down.dot(d)).normalized()
        A_r = Matrix((u_rest, n_r, u_rest.cross(n_r))).transposed()
        A_d = Matrix((d, n_d, d.cross(n_d))).transposed()
        return A_d @ A_r.transposed() @ R_rest

    def set_bone(role, s, d):
        pb = arm.pose.bones[B[f'{role}_{s}']]
        R_rest, u_rest = rest[(role, s)]
        head = Mw @ pb.head
        W = frame_for(d.normalized(), R_rest, u_rest).to_4x4()
        W.translation = head
        pb.matrix = Mw.inverted() @ W
        bpy.context.view_layer.update()

    lifts = []
    for k, f in enumerate(fr):
        sc.frame_set(f)
        for s, (point, follow) in goals.items():
            sx = 1 if s == 'L' else -1
            sh = Mw @ arm.pose.bones[B[f'arm_{s}']].head
            upper = (Mw @ arm.data.bones[B[f'fore_{s}']].head_local - Mw @ arm.data.bones[B[f'arm_{s}']].head_local).length
            w = point + (wrist[s][k] - means[s]) * follow
            lift = 0.0
            for _ in range(4):
                # the elbow at the desk's near edge, out to the side; the forearm from there to the wrist goal
                e_goal = Vector((sh.x + sx * 0.03, over_y + 0.01, desk_z + 0.05 + lift))
                set_bone('arm', s, e_goal - sh)
                e = Mw @ arm.pose.bones[B[f'fore_{s}']].head
                wpt = Vector((w.x, w.y, desk_z + 0.045 + lift))
                set_bone('fore', s, wpt - e)
                # the hand points ahead, pitched down until its lowest point touches the desk top
                hand = [p for p in by_side[s] if '_hand_' in p.name]
                pitch = 0.0
                for _ in range(6):
                    set_bone('hand', s, Vector((0, -math.cos(pitch), -math.sin(pitch))))
                    hz = min(float((np.array(p.matrix_world) @ cos[p].T)[2].min()) for p in hand)
                    if abs(hz - desk_z) < 0.003:
                        break
                    pitch = max(-0.3, min(0.9, pitch + (hz - desk_z) / 0.08))
                low = 9.0
                for p in by_side[s]:
                    if '_upper_arm_' in p.name:  # it reaches the desk at its edge, hidden by the top
                        continue
                    v = (np.array(p.matrix_world) @ cos[p].T)[:3].T
                    v = v[v[:, 1] < over_y]
                    if len(v):
                        low = min(low, float(v[:, 2].min()))
                if abs(low - desk_z) < 0.003 or low == 9.0:
                    break
                lift += desk_z - low
            lifts.append(lift)
            for role in ('arm', 'fore', 'hand'):
                pb = arm.pose.bones[B[f'{role}_{s}']]
                pb.keyframe_insert('rotation_quaternion', frame=f)
    return {'desk_lift_m': [round(min(lifts), 3), round(max(lifts), 3)]}


# --- posing the arms directly -----------------------------------------------------------------------------

class ArmPoser:
    """Set the arm bones of a Mixamo armature from world-space directions. A bone's direction is its head-to-child
    axis; its roll comes from a second axis: a world vector at rest (default: down, which is the canonical hands'
    palm normal in the T-pose) is taken to a target world vector, kept perpendicular to the bone."""
    DOWN = Vector((0, 0, -1))

    def __init__(self, arm, sides='LR'):
        self.arm, self.Mw = arm, arm.matrix_world
        self.rest = {}
        for s in sides:
            for role, child in (('arm', 'fore'), ('fore', 'hand'), ('hand', 'fingers')):
                b = arm.data.bones[ROLES[f'{role}_{s}']]
                head = self.Mw @ b.head_local
                ck = f'{child}_{s}'
                tip = self.Mw @ arm.data.bones[ROLES[ck]].head_local if ck in ROLES else self.Mw @ b.tail_local
                self.rest[(role, s)] = ((self.Mw @ b.matrix_local).to_3x3(), (tip - head).normalized())

    def length(self, role, s) -> float:
        child = {'arm': 'fore', 'fore': 'hand'}[role]
        bones = self.arm.data.bones
        return (bones[ROLES[f'{child}_{s}']].head_local - bones[ROLES[f'{role}_{s}']].head_local).length * \
            self.Mw.to_scale()[0]

    def head(self, role, s) -> Vector:
        return self.Mw @ self.arm.pose.bones[ROLES[f'{role}_{s}']].head

    def set(self, role, s, d: Vector, second=None) -> None:
        R_rest, u_r = self.rest[(role, s)]
        a_r, a_t = second or (self.DOWN, self.DOWN)
        d = d.normalized()
        n_r = (a_r - u_r * a_r.dot(u_r)).normalized()
        n_d = (a_t - d * a_t.dot(d)).normalized()
        A_r = Matrix((u_r, n_r, u_r.cross(n_r))).transposed()
        A_d = Matrix((d, n_d, d.cross(n_d))).transposed()
        W = (A_d @ A_r.transposed() @ R_rest).to_4x4()
        W.translation = self.head(role, s)
        self.arm.pose.bones[ROLES[f'{role}_{s}']].matrix = self.Mw.inverted() @ W
        bpy.context.view_layer.update()

    def reach(self, s, wrist: Vector, out: Vector, hand_dir: Vector, hand_second=None) -> None:
        """Two-bone solve: the elbow bends towards `out`; the hand points along hand_dir."""
        sh = self.head('arm', s)
        u, f = self.length('arm', s), self.length('fore', s)
        t = wrist - sh
        dist = min(t.length, (u + f) * 0.999)
        axis = t.normalized()
        a = (u * u - f * f + dist * dist) / (2 * dist)
        h = math.sqrt(max(u * u - a * a, 0.0))
        o = (out - axis * out.dot(axis)).normalized()
        self.set('arm', s, axis * a + o * h)
        self.set('fore', s, sh + axis * dist - self.head('fore', s))
        self.set('hand', s, hand_dir, hand_second)

    def key(self, s, f, weight: float = 1.0, clip=None) -> None:
        """Key the arm's rotations at frame f, blended with the clip's (clip: role -> quaternion) by weight."""
        for role in ('arm', 'fore', 'hand'):
            pb = self.arm.pose.bones[ROLES[f'{role}_{s}']]
            if clip is not None and weight < 1.0:
                pb.rotation_quaternion = clip[role].slerp(pb.rotation_quaternion.copy(), weight)
            pb.keyframe_insert('rotation_quaternion', frame=f)


def clip_quats(arm, s) -> dict:
    return {r: arm.pose.bones[ROLES[f'{r}_{s}']].rotation_quaternion.copy() for r in ('arm', 'fore', 'hand')}


def thumbs_up(arm) -> dict:
    """Keep the thumbs-up hand off the face: while the clip raises it (weight by the wrist's height between the
    chest and the shoulder), the hand is held in front of the chest and out to the side, thumb up, fist facing
    forward. Blended with the clip's own arm at the ends of the gesture."""
    sc = bpy.context.scene
    fr = list(frames(arm))
    Mw = arm.matrix_world
    wz = {s: [] for s in 'LR'}
    for f in fr:
        sc.frame_set(f)
        for s in 'LR':
            wz[s].append((Mw @ arm.pose.bones[ROLES[f'hand_{s}']].head).z)
    s = max('LR', key=lambda k: max(wz[k]))
    sx = 1 if s == 'L' else -1
    P = ArmPoser(arm, s)
    peak = 0.0
    for k, f in enumerate(fr):
        sc.frame_set(f)
        sh = P.head('arm', s)
        chest = Mw @ arm.pose.bones[ROLES['spine3']].head
        w = min(1.0, max(0.0, (wz[s][k] - chest.z) / max(1e-3, (sh.z - chest.z) * 0.8)))
        w = w * w * (3 - 2 * w)
        if w <= 0.0:
            continue
        q = clip_quats(arm, s)
        wrist = sh + Vector((sx * 0.07, -0.22, -0.07))
        fwd = Vector((-sx * 0.25, -1, 0.15)).normalized()
        P.reach(s, wrist, Vector((sx, 0.3, -0.6)), fwd, (Vector((0, -1, 0)), Vector((0, 0, 1))))
        P.key(s, f, w, q)
        peak = max(peak, w)
    return {'thumbs_side': s, 'thumbs_blend_peak': round(peak, 2)}


PROPS = {
    'book': dict(size=(0.15, 0.03, 0.2), cover='#2f5f8a', pages='#efe8d6', hands='pinch'),
    'box': dict(size=(0.3, 0.24, 0.22), cover='#b98a55', tape='#d9c39a', hands='cupped'),
}


def prop_mesh(kind: str, tag: str) -> bpy.types.Object:
    """A closed book (cover, cream page block on three sides) or a cardboard box with a tape strip, centred on
    the origin, width along x, depth along y, height along z."""
    import bmesh
    spec = PROPS[kind]
    w, d, h = spec['size']
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((w, d, h, 1)))
    for f in bm.faces:
        f.material_index = 0
    if kind == 'book':  # the page block: inset slightly on the open sides, cream
        blk = bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((0.004, 0, 0)) @
                                    Matrix.Diagonal((w - 0.004, d * 0.8, h - 0.01, 1)))
        for v in blk['verts']:
            for f in v.link_faces:
                f.material_index = 1
    else:  # a tape strip over the top and down the front and back
        t = bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((0.06, d + 0.004, h + 0.004, 1)))
        for v in t['verts']:
            for f in v.link_faces:
                f.material_index = 1
    me = bpy.data.meshes.new(f'{tag}_{kind}')
    bm.to_mesh(me)
    bm.free()
    for key, colour in (('cover', spec['cover']), ('pages' if kind == 'book' else 'tape', spec.get('pages') or spec['tape'])):
        m = bpy.data.materials.get(f'prop_{kind}_{key}') or bpy.data.materials.new(f'prop_{kind}_{key}')
        m.use_nodes = True
        b = m.node_tree.nodes['Principled BSDF']
        b.inputs['Base Color'].default_value = palette.lin(colour)
        b.inputs['Roughness'].default_value = 0.7 if kind == 'box' else 0.45
        me.materials.append(m)
    o = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(o)
    return o


def hold(arm, parts, tag: str, kind: str, centre, tilt_deg: float = 0.0) -> list:
    """Hold a book or box in both hands. centre(f) gives the prop's world centre per frame (in front of the body);
    the prop rides on the root bone (placed at the middle frame) and each frame the wrists are posed onto its
    sides, fingers forward, palms facing it."""
    sc = bpy.context.scene
    fr = list(frames(arm))
    w, d, h = PROPS[kind]['size']
    prop = prop_mesh(kind, tag)
    P = ArmPoser(arm)
    Mw = arm.matrix_world
    hand = {s: next(p for p in parts if '_hand_' in p.name and p.name.endswith(f'_{s}')) for s in 'LR'}
    half = {s: 0.75 * min(hand[s].dimensions) for s in 'LR'}  # the hand's half-thickness across the palm, and a margin
    tilt = Matrix.Rotation(math.radians(tilt_deg), 4, 'X')
    for f in fr:
        sc.frame_set(f)
        c = centre(f)
        for s in 'LR':
            sx = 1 if s == 'L' else -1
            side = (tilt @ Vector((sx, 0, 0)).to_4d()).to_3d()
            wrist = c + side * (w / 2 + half[s]) + (tilt @ Vector((0, d * 0.1, -h * 0.1)).to_4d()).to_3d()
            fwd = (tilt @ Vector((0, -1, 0)).to_4d()).to_3d()
            P.reach(s, wrist, Vector((sx, 0.4, -0.5)), fwd, (ArmPoser.DOWN, -side))
            P.key(s, f)
    mid = fr[len(fr) // 2]
    sc.frame_set(mid)
    prop.matrix_world = Matrix.Translation(centre(mid)) @ tilt
    mw = prop.matrix_world.copy()
    prop.parent, prop.parent_type, prop.parent_bone = arm, 'BONE', ROLES['root']
    bpy.context.view_layer.update()
    prop.matrix_world = mw
    return [prop]


def build_sheet(arm, parts, tag: str) -> list:
    """The 'sheet' pose: a pinch hand on each side and a modelled sheet of paper held between them. At the clip's
    middle frame the left hand and the paper are frozen relative to the right hand and ride on its bone."""
    sc = bpy.context.scene
    fr = list(frames(arm))
    sc.frame_set(fr[len(fr) // 2])
    hands = {s: next(p for p in parts if p.name == f'{tag}_hand_sheet_{s}') for s in 'LR'}

    def pinch_point(h):
        v = np.array([h.matrix_world @ x.co for x in h.data.vertices])
        loc = np.array([x.co for x in h.data.vertices])
        reach = np.abs(loc[:, 0])  # canonical hands: fingers along +-X
        return Vector(v[reach > reach.max() * 0.85].mean(0))
    pR, pL = pinch_point(hands['R']), pinch_point(hands['L'])
    head = arm.matrix_world @ arm.pose.bones[ROLES['head']].head
    e = (pL - pR).normalized()
    width = (pL - pR).length * 1.08
    down = Vector((0, 0, -1))
    down = (down - e * down.dot(e)).normalized()
    n = e.cross(down)
    if n.dot(head - (pR + pL) / 2) < 0:  # the printed side faces the reader
        n = -n
    height = width * SHEET['aspect']
    top = (pR + pL) / 2 - down * height * 0.12
    import bmesh
    bm = bmesh.new()
    nu, nv = 12, 16
    grid = []
    for j in range(nv + 1):
        row = []
        for i in range(nu + 1):
            u, v = i / nu - 0.5, j / nv
            bow = SHEET['curl'] * (1 - (2 * u) ** 2)  # a slight curl across the width, towards the reader
            row.append(bm.verts.new(top + e * (u * width) + down * (v * height) - n * bow))
        grid.append(row)
    for j in range(nv):
        for i in range(nu):
            bm.faces.new((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]))
    ink = []
    for k in range(6):  # a few grey lines of text, just proud of the printed side
        v0 = 0.14 + k * 0.11
        w0, w1 = -0.36, 0.36 if k % 3 != 2 else 0.1
        quad = []
        for uu, vv in ((w0, v0), (w1, v0), (w1, v0 + 0.03), (w0, v0 + 0.03)):
            bow = SHEET['curl'] * (1 - (2 * uu) ** 2)
            quad.append(bm.verts.new(top + e * (uu * width) + down * (vv * height) - n * (bow - 0.0015)))
        ink.append(bm.faces.new(quad))
    for f in ink:
        f.material_index = 1
    me = bpy.data.meshes.new(f'{tag}_sheet_paper')
    bm.to_mesh(me)
    bm.free()
    for kind, colour in (('paper', SHEET['paper']), ('ink', SHEET['ink'])):
        m = bpy.data.materials.get(f'sheet_{kind}') or bpy.data.materials.new(f'sheet_{kind}')
        m.use_nodes = True
        m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = palette.lin(colour)
        m.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = 0.8
        me.materials.append(m)
    paper = bpy.data.objects.new(me.name, me)
    sc.collection.objects.link(paper)
    rbone = ROLES['hand_R']
    for o in (paper, hands['L']):
        mw = o.matrix_world.copy()
        o.parent, o.parent_type, o.parent_bone = arm, 'BONE', rbone
        bpy.context.view_layer.update()
        o.matrix_world = mw
    return [paper]


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
    pose = hand_pose or HAND_POSE.get(arm.get('clip', ''), 'fist')
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
    model = 'pinch' if pose == 'sheet' else HELD.get(pose, pose)
    hands = append(HANDS, [f'hand_{model}_{s}' for s in 'LR'])
    for s in 'LR':
        hands[f'hand_{pose}_{s}'] = hands.pop(f'hand_{model}_{s}')
    for s in ('L', 'R'):
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
    if pose == 'sheet':
        parts += build_sheet(arm, parts, tag)
    return parts


def tint(hex_colour: str) -> None:
    """Recolour the host: every 'host_tint' node (the teal shells) takes the new colour."""
    for m in bpy.data.materials:
        if m.use_nodes and 'host_tint' in m.node_tree.nodes:
            m.node_tree.nodes['host_tint'].outputs[0].default_value = palette.lin(hex_colour)
