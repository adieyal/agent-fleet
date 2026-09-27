"""Render Mixamo clips on the rebuilt robot with the floor's one camera (orthographic, pitch 28, yaw 33;
docs/design/sprite-world.md on renovate/floor), through bakeoff.py's framing and studio, against the floor kit's
chair and bench desk at their normal size.

    B=~/.local/bin/blender; S=art/motion-test/render_motion.py
    $B -b --factory-startup --python $S -- fit <robot_scale.json>  # the height that matches l2's seated robots
    $B -b --factory-startup --python $S -- review <out>            # the review set (see cmd_review)
    $B -b --factory-startup --python $S -- calib <out.png>         # standing robot at the sheet's angle
"""

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'scripts')]
import artlib as A  # noqa: E402
import bakeoff as K  # noqa: E402
import build_workbench as W  # noqa: E402
import motion_rig as R  # noqa: E402

MIX = Path.home() / '.local/state/fleet/renovation/mixamo'
FLOOR_VIEW = dict(pitch=28.0, yaw=33.0)  # the floor's camera since floor review 1
SCALE_FILE = HERE / 'robot_scale.json'
# where the kit's normal-size chair and desk stand around the seat point (the pelvis's rest), fitted to the robot by
# cmd_fit: the chair's centre (y) and how far ahead the desk's far edge is. Defaults: build_robot's human layout.
BENCH = {'chair_y': 0.2, 'desk_edge': 0.14, 'seat_h': W.SEAT_H} | \
    ((json.loads(SCALE_FILE.read_text()).get('bench') if SCALE_FILE.exists() else None) or {})
DESK_EDGE, CHAIR_Y, SEAT_H = BENCH['desk_edge'], BENCH['chair_y'], BENCH['seat_h']  # SEAT_H: the chair's gas lift
KEYS = Vector((0.0, -DESK_EDGE - 0.2, W.DESK_H))  # the keyboard's centre on the desk top, at the forearms' reach
KEY_SPREAD = 0.085  # each wrist this far from the keyboard's centre (times the robot's scale over 1.05 m)
# l2 (docs/images/concept/l2.png, 171.5 px/m, the same camera), measured with a pixel ruler on the upright blue and
# green robots (teal is hunched on its hand): how far above the desk's far edge, in the robot's own screen column,
# the helmet top rises (blue 110, green 115) and the chest emblem sits (blue 25, green 17)
L2_RISE_PX = (110 + 115) / 2
L2_CHEST_RISE_PX = (25 + 17) / 2


def chest_light(torso) -> Vector:
    """World centre of the torso's glowing chest light (the 'cls' attribute's glow channel)."""
    a = np.empty(len(torso.data.vertices) * 4, dtype=np.float32)
    torso.data.color_attributes['cls'].data.foreach_get('color', a)
    glow = a.reshape(-1, 4)[:, 3] > 0.5
    return Vector(verts(torso)[glow].mean(0))


def world_pts(objs) -> list:
    return [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]


def verts(ob, step: int = 1) -> np.ndarray:
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get('co', co)
    co = np.c_[co.reshape(-1, 3)[::step], np.ones(len(co) // 3)[::step]]
    return (np.array(ob.matrix_world) @ co.T)[:3].T


# seated clips keep this much of their spine, neck and head lean (motion_rig.upright): typing sits straight,
# writing leans a little, reading lowers the head just enough to look at the book
UPRIGHT = {'typing': 0.0, 'writing-seated': 0.08, 'reading-seated': 0.15}
UPRIGHT_OTHER = 0.35
UPRIGHT_HIPS = {'writing-seated': 0.4}  # writing also tilts the pelvis forward
SEAT_FRONT = CHAIR_Y - 0.24  # the chair seat's front edge (y): the seat is 0.48 m deep


DESK_CLIPS = {'typing', 'writing-seated'}  # arms laid on the desk top (motion_rig.desk_arms)


def goals(clip: str) -> tuple[dict, float | None]:
    """IK goals for the seated desk clips, scaled with the robot: (side -> (wrist point, follow), rest height)."""
    k = R.body_meta()['height_m'] / 1.05
    if clip == 'typing':  # both hands on the keyboard, keeping half the clip's own finger-work
        w = KEYS + Vector((0, 0.06 * k, 0))  # wrists behind the keys, so the fists rest on them
        return {'L': (w + Vector((KEY_SPREAD * k, 0, 0)), 0.3),
                'R': (w - Vector((KEY_SPREAD * k, 0, 0)), 0.3)}, W.DESK_H
    if clip == 'writing-seated':  # the pinch hand writing just past the keyboard's left end, the other hand resting
        return {'R': (Vector((-0.09 * k, KEYS.y + 0.04 * k, W.DESK_H)), 0.4),
                'L': (Vector((0.11 * k, KEYS.y + 0.06 * k, W.DESK_H)), 0.2)}, W.DESK_H
    return {}, None


def robot(clip: str, tag: str, pin=False, seated=False, hand_pose=None):
    ref = 'lowest' if seated else 'first'
    arm, info = R.retarget(MIX / f'{clip}.fbx', pin=pin, ref=ref)
    if seated:
        R.upright(arm, UPRIGHT.get(clip, UPRIGHT_OTHER), UPRIGHT_HIPS.get(clip, 1.0))
    parts = R.attach(arm, tag, hand_pose)
    info |= R.seat(arm, parts, SEAT_H, SEAT_FRONT) if seated else R.ground(arm, parts)
    g, rest = goals(clip) if seated else ({}, None)
    if g and clip in DESK_CLIPS:
        info |= R.desk_arms(arm, parts, g, W.DESK_H, over_y=-DESK_EDGE + 0.02)
    elif g:
        info |= R.reach(arm, parts, g, rest, over_y=-DESK_EDGE)
    pose = arm['hand_pose']
    k = R.body_meta()['height_m'] / 1.05
    root = arm.pose.bones[R.ROLES['root']]
    chest = arm.pose.bones[R.ROLES['spine3']]
    if pose == 'book':  # held up at chest height, tilted back towards the face, clear of the desk
        def centre(f, a=arm):
            c = a.matrix_world @ chest.head
            return Vector((c.x, c.y - 0.2 * k, max(c.z + 0.08 * k, W.DESK_H + 0.16)))
        parts += R.hold(arm, parts, tag, 'book', centre, tilt_deg=-20)
    elif pose == 'box':  # carried in front of the belly
        def centre(f, a=arm):
            c = a.matrix_world @ root.head
            return Vector((c.x, c.y - 0.2 * k, c.z + 0.1 * k))
        parts += R.hold(arm, parts, tag, 'box', centre)
    if clip.startswith('thumbs-up'):
        info |= R.thumbs_up(arm)
    info['hand_pose'] = pose
    return arm, parts, info


def ref_frame(arm, info) -> int:
    return int(info['ref_frame']) + R.frames(arm).start


def furniture() -> dict:
    """The floor kit's task chair and bench desk at their normal size (build_workbench), laid out as bench() does:
    the seat point at the origin, the chair centred CHAIR_Y behind it, the desk's far edge DESK_EDGE ahead."""
    m = W.materials()
    before = set(bpy.data.objects)
    kit_seat, W.SEAT_H = W.SEAT_H, SEAT_H  # the task chair's gas lift raised to SEAT_H (its column grows)
    W.chair(m, 'chair', 0.0, CHAIR_Y, 0.0)
    W.SEAT_H = kit_seat
    chair = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
    before = set(bpy.data.objects)
    W.desk_frame(m, 'desk', -W.DESK_W / 2, -DESK_EDGE - W.DESK_D / 2)
    A.box('keyboard', (0.30, 0.11, 0.018), (KEYS.x, KEYS.y, W.DESK_H), m['black'], bevel=0.004)
    desk = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
    bpy.context.view_layer.update()
    return {'chair': chair, 'desk': desk}


def clipping(arm, parts) -> dict:
    """Robot vertices inside the desk top, the desk's legs and rail, or the chair's seat and back, over the clip."""
    boxes = {
        'desk_top': ((-W.DESK_W / 2, -DESK_EDGE - W.DESK_D, W.DESK_H - W.TOP_T), (W.DESK_W / 2, -DESK_EDGE, W.DESK_H)),
        'chair_seat': ((-0.25, CHAIR_Y - 0.24, SEAT_H - 0.08), (0.25, CHAIR_Y + 0.24, SEAT_H)),
        'chair_back': ((-0.23, CHAIR_Y + 0.21, SEAT_H + 0.08), (0.23, CHAIR_Y + 0.27, SEAT_H + 0.58)),
    }
    sc = bpy.context.scene
    fr = list(R.frames(arm))
    worst = {k: 0.0 for k in boxes}
    hits = {k: 0 for k in boxes}
    who = {k: set() for k in boxes}
    body = [p for p in parts if '_hand_' not in p.name and 'sheet' not in p.name]  # hands rest on the desk
    for f in fr[:: max(1, len(fr) // 40)]:
        sc.frame_set(f)
        per = [(p.name.split('_', 1)[1], verts(p, 3)) for p in body]
        pts = np.concatenate([v for _, v in per])
        owner = np.concatenate([[n] * len(v) for n, v in per])
        for k, (lo, hi) in boxes.items():
            inside = np.all((pts > np.array(lo)) & (pts < np.array(hi)), axis=1)
            if inside.any():
                who[k] |= set(owner[inside])
                hits[k] += int(inside.sum())
                d = np.minimum(pts[inside] - np.array(lo), np.array(hi) - pts[inside]).min(1)
                worst[k] = max(worst[k], float(d.max()))
    return {k: {'vertex_hits': hits[k], 'deepest_m': round(worst[k], 3), 'pieces': sorted(who[k])} for k in boxes}


def rise_px(head_top: Vector) -> float:
    """Screen pixels (1x) from the desk's far edge up to a point, in the point's own screen column."""
    right, up, _ = K.axes()
    # the far edge is the line (x, -DESK_EDGE, DESK_H); find x with the same screen u as the point
    e0 = Vector((0, -DESK_EDGE, W.DESK_H))
    x = (head_top.dot(right) - e0.dot(right)) / right.x
    edge = e0 + Vector((x, 0, 0))
    return K.PX_PER_M * (head_top.dot(up) - edge.dot(up))


def cmd_fit(dest: Path) -> None:
    """Fit the kit's normal-size chair and desk around the robot so its seated silhouette matches l2's.
    Seat height (the chair's gas lift): the chest light rises L2_CHEST_RISE_PX above the desk's far edge at 1x, as
    l2's chest emblems do; the robot rises one for one with the seat, so solve directly. Layout (seated typing,
    upright): the seat's front edge 1 cm in front of the hanging shins, and the desk's far edge 3 cm in front of
    the belly. Seat and spacing move each other a little, so run fit twice (the second run confirms)."""
    global DESK_EDGE
    A.reset()
    view(**FLOOR_VIEW)
    arm, parts, info = robot('typing', 'r', seated=True)
    bpy.context.scene.frame_set(ref_frame(arm, info))
    V = {p.name[2:]: verts(p) for p in parts if p.type == 'MESH'}
    torso_front = float(V['torso'][:, 1].min())
    legs = np.concatenate([V[k] for k in ('shin_L', 'shin_R', 'ball_knee_L', 'ball_knee_R')])
    shins_back = float(legs[legs[:, 2] < SEAT_H][:, 1].max())
    DESK_EDGE = round(-(torso_front - 0.03), 3)
    chest = chest_light(next(p for p in parts if p.name == 'r_torso'))
    helmet = Vector(V['head'][V['head'][:, 2].argmax()])
    r_chest, r_helmet = rise_px(chest), rise_px(helmet)
    per_m = K.PX_PER_M * K.axes()[1].z  # screen px per metre of height at 1x
    seat = SEAT_H + (L2_CHEST_RISE_PX - r_chest) / per_m
    out = {'height_m': R.body_meta()['height_m'],
           'bench': {'seat_h': round(seat, 3), 'chair_y': round(shins_back + 0.01 + 0.24, 3), 'desk_edge': DESK_EDGE},
           'fit': {'l2_chest_rise_px': L2_CHEST_RISE_PX, 'l2_helmet_rise_px': L2_RISE_PX,
                   'at_seat_h': SEAT_H, 'chest_rise_px_now': round(r_chest, 1), 'helmet_rise_px_now': round(r_helmet, 1),
                   'helmet_rise_px_fitted': round(r_helmet + (seat - SEAT_H) * per_m, 1),
                   'kit_seat_h': W.SEAT_H, 'gas_lift_raise_m': round(seat - W.SEAT_H, 3)}}
    dest.write_text(json.dumps(out, indent=1) + '\n')
    print('FIT', json.dumps(out))


def render(path: Path, samples: int = 64) -> None:
    s = bpy.context.scene
    s.render.film_transparent = True
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'
    s.cycles.samples = samples
    s.cycles.use_denoising = True
    s.render.filepath = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)


def only(visible: list) -> None:
    keep = set(visible)
    for o in bpy.data.objects:
        if o.type == 'MESH' and o.name != 'catcher':
            o.hide_render = o not in keep


def view(pitch: float, yaw: float) -> None:
    K.VIEW.update(pitch=pitch, yaw=yaw)


def studio() -> None:
    view(**FLOOR_VIEW)
    K.studio(Vector((0, 0, 0.5)))
    K.shadow_catcher()


def a_pose(arm) -> None:
    """Hold the model's own A-pose (arms along the model's), for comparing with robot-apose-front.png."""
    arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix()
    R.pose_arms_to_model(arm)


SEATED_CLIPS = {'writing-seated', 'reading-seated', 'typing', 'thumbs-up-sitting'}


def peak_frame(arm, parts, side_hint=None) -> int:
    """The thumbs-up clip's frame with its raised hand highest."""
    sc = bpy.context.scene
    hands = [p for p in parts if '_hand_' in p.name]
    fr = list(R.frames(arm))
    return max(fr, key=lambda g: (sc.frame_set(g), max(verts(h, 7)[:, 2].max() for h in hands))[1])


def cmd_review(out: Path) -> None:
    """Round 3: the head beside the sheet's face; seated typing, writing and reading at the bench (1x, 2x); thumbs
    up standing and sitting; walking with the box. Writes <out>/info.json with fit and clipping checks."""
    A.reset()
    info = {}
    stand, sparts, info['standing'] = robot('standing-idle', 'stand')
    seated = {}
    for clip, tag in (('typing', 'type'), ('writing-seated', 'write'), ('reading-seated', 'read')):
        seated[clip] = robot(clip, tag, seated=True)
        info[clip] = seated[clip][2]
    th_s = robot('thumbs-up-standing', 'ths')
    th_c = robot('thumbs-up-sitting', 'thc', seated=True)
    info['thumbs-up-standing'], info['thumbs-up-sitting'] = th_s[2], th_c[2]
    box = robot('box-walk-arc', 'box', pin=True)
    info['box-walk-arc'] = box[2]
    kit = furniture()
    bench = kit['chair'] + kit['desk']
    studio()
    sc = bpy.context.scene
    for clip, (arm, parts, _) in seated.items():
        info[clip]['clipping'] = clipping(arm, parts)
        sc.frame_set(ref_frame(arm, info[clip]))
        hv = verts(next(p for p in parts if p.name.endswith('_head')))
        info[clip]['helmet_rise_px_1x'] = round(rise_px(Vector(hv[hv[:, 2].argmax()])), 1)
        info[clip]['chest_rise_px_1x'] = round(rise_px(chest_light(next(p for p in parts if p.name.endswith('_torso')))), 1)
    info['l2'] = {'helmet_rise_px': L2_RISE_PX, 'chest_rise_px': L2_CHEST_RISE_PX}
    info['bench'] = BENCH
    info['height_m'] = R.body_meta()['height_m']

    # the head: front (as the sheet's face) and at the floor camera
    sc.frame_set(1)
    only(sparts)
    head = [p for p in sparts if any(k in p.name for k in ('_head', 'faceplate', 'eyes', '_ear_', 'ball_neck'))]
    for name, (pitch, yaw, mult) in {'head_front': (4, 0, 14), 'head_floor': (28, 33, 14),
                                     'head_sheet_angle': (14, 24, 14)}.items():
        view(pitch, yaw)
        K.frame_camera(world_pts(head), Vector(), mult, margin=0.02)
        render(out / f'{name}.png', 128)
    view(**FLOOR_VIEW)

    # the bench: each seated clip at its reference frame, framed on the desk and chair, at 1x and 2x
    frame_box = world_pts(kit['chair']) + [Vector(p) for p in ((-0.9, -DESK_EDGE - 0.8, 0), (0.9, -DESK_EDGE - 0.8, 0),
                                                               (0.9, -DESK_EDGE, W.DESK_H))]
    for clip, (arm, parts, cinfo) in seated.items():
        f = ref_frame(arm, cinfo) if clip != 'reading-seated' else list(R.frames(arm))[len(R.frames(arm)) // 2]
        sc.frame_set(f)
        only(parts + bench)
        for mult in (1, 2):
            K.frame_camera(frame_box + world_pts(parts), Vector(), mult, margin=0.12)
            render(out / f'bench_{clip}_{mult}x.png', 96)
        K.frame_camera(world_pts(parts), Vector(), 6, margin=0.05)
        render(out / f'close_{clip}.png', 96)

    # thumbs up, standing and sitting, at the gesture's peak: whole robot (4x) and the hand with the face (8x)
    for name, (arm, parts, tinfo), with_bench in (('thumbs_standing', th_s, False), ('thumbs_sitting', th_c, True)):
        f = peak_frame(arm, parts)
        info['thumbs-up-standing' if name == 'thumbs_standing' else 'thumbs-up-sitting']['peak_frame'] = f
        sc.frame_set(f)
        only(parts + (bench if with_bench else []))
        K.frame_camera(world_pts(parts), Vector(), 4, margin=0.05)
        render(out / f'{name}.png')
        view(8, 15)
        K.frame_camera(world_pts(parts), Vector(), 4, margin=0.05)
        render(out / f'{name}_front.png')
        view(**FLOOR_VIEW)

    # walking with the box: four frames across the cycle (4x) and the middle frame at 1x and 2x
    arm, parts, _ = box
    fr = list(R.frames(arm))
    only(parts)
    pts = []
    for f in fr[::4]:
        sc.frame_set(f)
        pts += world_pts(parts)
    for k in range(4):
        sc.frame_set(fr[k * (len(fr) - 1) // 3])
        K.frame_camera(pts, Vector(), 4, margin=0.05)
        render(out / f'box_walk_{k}.png')
    sc.frame_set(fr[len(fr) // 2])
    for mult in (1, 2):
        K.frame_camera(pts, Vector(), mult, margin=0.08)
        render(out / f'box_walk_{mult}x.png', 96)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'info.json').write_text(json.dumps(info, indent=1, default=str))
    print('DONE', out)


def cmd_calib(dest: Path) -> None:
    A.reset()
    stand, parts, _ = robot('standing-idle', 'stand')
    studio()
    bpy.context.scene.frame_set(1)
    view(14, 24)
    K.frame_camera(world_pts(parts), Vector(), 2, margin=0.05)
    render(dest, 48)


def main() -> None:
    args = sys.argv[sys.argv.index('--') + 1:]
    {'fit': cmd_fit, 'review': cmd_review, 'calib': cmd_calib}[args[0]](Path(args[1]))


main()
