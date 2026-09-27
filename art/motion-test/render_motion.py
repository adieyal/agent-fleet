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
BENCH = (json.loads(SCALE_FILE.read_text()).get('bench') if SCALE_FILE.exists() else None) or \
    {'chair_y': 0.2, 'desk_edge': 0.14}
DESK_EDGE, CHAIR_Y = BENCH['desk_edge'], BENCH['chair_y']
KEYS = Vector((0.0, -DESK_EDGE - 0.2, W.DESK_H))  # the keyboard's centre on the desk top, at the forearms' reach
KEY_SPREAD = 0.085  # each wrist this far from the keyboard's centre (times the robot's scale over 1.05 m)
# l2 (docs/images/concept/l2.png, 171.5 px/m, the same camera): how far each seated robot's helmet top rises above
# the desk's far edge in its own screen column, in pixels: teal 98, blue 87, green 85
L2_RISE_PX = (98 + 87 + 85) / 3
# ... and its chest emblem, the landmark both designs share: teal +20, blue -3, green -8 (about at the edge)
L2_CHEST_RISE_PX = (20 - 3 - 8) / 3


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


UPRIGHT = 0.35  # seated clips keep this much of their spine, neck and head lean (motion_rig.upright)
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
    if clip == 'reading-seated':  # the book held up in front of the chest, clear of the desk
        c = Vector((0, -DESK_EDGE + 0.02, W.DESK_H + 0.2 * k))
        return {'R': (c - Vector((0.07 * k, 0, 0)), 0.3), 'L': (c + Vector((0.07 * k, 0, 0)), 0.3)}, W.DESK_H
    return {}, None


def robot(clip: str, tag: str, pin=False, seated=False, hand_pose=None):
    ref = 'lowest' if seated else 'first'
    arm, info = R.retarget(MIX / f'{clip}.fbx', pin=pin, ref=ref)
    if seated:
        R.upright(arm, UPRIGHT)
    parts = R.attach(arm, tag, hand_pose)
    info |= R.seat(arm, parts, W.SEAT_H, SEAT_FRONT) if seated else R.ground(arm, parts)
    g, rest = goals(clip) if seated else ({}, None)
    if g and clip in DESK_CLIPS:
        info |= R.desk_arms(arm, parts, g, W.DESK_H, over_y=-DESK_EDGE + 0.02)
    elif g:
        info |= R.reach(arm, parts, g, rest, over_y=-DESK_EDGE)
    info['hand_pose'] = arm['hand_pose']
    return arm, parts, info


def ref_frame(arm, info) -> int:
    return int(info['ref_frame']) + R.frames(arm).start


def furniture() -> dict:
    """The floor kit's task chair and bench desk at their normal size (build_workbench), laid out as bench() does:
    the seat point at the origin, the chair centred CHAIR_Y behind it, the desk's far edge DESK_EDGE ahead."""
    m = W.materials()
    before = set(bpy.data.objects)
    W.chair(m, 'chair', 0.0, CHAIR_Y, 0.0)
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
        'chair_seat': ((-0.25, CHAIR_Y - 0.24, W.SEAT_H - 0.08), (0.25, CHAIR_Y + 0.24, W.SEAT_H)),
        'chair_back': ((-0.23, CHAIR_Y + 0.21, W.SEAT_H + 0.08), (0.23, CHAIR_Y + 0.27, W.SEAT_H + 0.58)),
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
    """Fit the robot to l2 and lay the kit's normal-size chair and desk out around it.
    Layout (seated typing, upright): the seat's front edge 1 cm in front of the hanging shins, so the legs drop
    clear of the seat; the desk's far edge 3 cm in front of the belly, so the desk top misses the torso.
    Height: the chest light sits against that far edge as l2's robots' chest emblems do (L2_CHEST_RISE_PX at 1x),
    so shoulders and arms meet the desk as theirs do; the helmet then rises more than l2's, as the sheet's head is
    larger for its body (both are reported). The robot is
    pinned on the seat, so the rise is linear in the scale about the seat contact: measure here and at twice the
    size, and solve. The layout moves with the height, so run fit twice (the second run confirms)."""
    global DESK_EDGE
    A.reset()
    view(**FLOOR_VIEW)
    arm, parts, info = robot('typing', 'r', seated=True)
    bpy.context.scene.frame_set(ref_frame(arm, info))
    V = {p.name[2:]: verts(p) for p in parts}
    torso_front = float(V['torso'][:, 1].min())
    legs = np.concatenate([V[k] for k in ('shin_L', 'shin_R', 'ball_knee_L', 'ball_knee_R')])
    shins_back = float(legs[legs[:, 2] < W.SEAT_H][:, 1].max())
    seat_pts = np.concatenate([V[k] for k in ('pelvis', 'thigh_L', 'thigh_R')])
    contact = seat_pts[seat_pts[:, 2].argmin()]
    bench = {'chair_y': round(shins_back + 0.01 + 0.24, 3), 'desk_edge': round(-(torso_front - 0.03), 3)}
    DESK_EDGE = bench['desk_edge']
    marks = {'chest': chest_light(next(p for p in parts if p.name == 'r_torso')),
             'helmet': Vector(V['head'][V['head'][:, 2].argmax()])}
    c = Vector(contact)
    h0 = R.body_meta()['height_m']
    rise = {k: (rise_px(m), rise_px(c + (m - c) * 2.0)) for k, m in marks.items()}
    r0, r1 = rise['chest']
    k = 1 + (L2_CHEST_RISE_PX - r0) / (r1 - r0)
    h0r, h1r = rise['helmet']
    out = {'height_m': round(h0 * k, 3), 'bench': bench, 'fit': {
        'l2_chest_rise_px': round(L2_CHEST_RISE_PX, 1), 'l2_helmet_rise_px': round(L2_RISE_PX, 1),
        'chest_rise_px_now': round(r0, 1), 'helmet_rise_px_now': round(h0r, 1),
        'helmet_rise_px_fitted': round(h0r + (k - 1) * (h1r - h0r), 1), 'from_height_m': h0,
        'seat_contact_y': round(float(contact[1]), 3), 'seat_front_y': round(shins_back + 0.01, 3)}}
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


# hand close-ups: (clip, frame chooser, which hands)
HAND_SHOTS = {
    'fist': ('walk-normal', 'mid', 'R'), 'open': ('waving', 'mid', 'R'),
    'thumbs_up': ('thumbs-up-standing', 'thumb', 'R'), 'cupped': ('box-idle', 'mid', 'LR'),
    'pinch': ('writing-seated', 'mid', 'R'), 'book': ('reading-seated', 'mid', 'LR'),
    'sheet': ('walking-reading-phone', 'mid', 'LR'),
}
SEATED_CLIPS = {'writing-seated', 'reading-seated', 'typing'}


def cmd_review(out: Path) -> None:
    A.reset()
    info = {}
    stand, sparts, info['standing'] = robot('standing-idle', 'stand')
    apose, aparts, _ = robot('standing-idle', 'apose')
    typing, yparts, info['typing'] = robot('typing', 'type', seated=True)
    reading, rparts, info['reading'] = robot('reading-seated', 'read', seated=True)
    walk, wparts, info['walk'] = robot('walk-normal', 'walk', pin=True)
    shots = {}
    for pose, (clip, _, _) in HAND_SHOTS.items():
        if clip in ('walk-normal', 'reading-seated'):
            continue
        shots[pose] = robot(clip, f'h_{pose}', pin=clip.startswith('walk'), seated=clip in SEATED_CLIPS)
        info[f'hand_{pose}'] = shots[pose][2]
    shots['fist'] = (walk, wparts, info['walk'])
    shots['book'] = (reading, rparts, info['reading'])
    kit = furniture()
    bench = kit['chair'] + kit['desk']
    studio()
    sc = bpy.context.scene
    sc.frame_set(1)
    a_pose(apose)
    bpy.context.view_layer.update()
    for name, (arm, parts) in {'typing': (typing, yparts), 'reading': (reading, rparts)}.items():
        info[name]['clipping'] = clipping(arm, parts)
        sc.frame_set(ref_frame(arm, info[name]))
        head = next(p for p in parts if p.name.endswith('_head'))
        hv = verts(head)
        info[name]['rise_px_1x'] = round(rise_px(Vector(hv[hv[:, 2].argmax()])), 1)
        info[name]['chest_rise_px_1x'] = round(rise_px(chest_light(next(p for p in parts if p.name.endswith('_torso')))), 1)
    info['l2_rise_px'] = round(L2_RISE_PX, 1)
    info['height_m'] = R.body_meta()['height_m']

    # the references: the model's A-pose from the front, the standing robot at the sheet's angle
    sc.frame_set(1)
    only(aparts)
    view(0, 0)
    K.frame_camera(world_pts(aparts), Vector(), 4, margin=0.04)
    render(out / 'apose_front.png')
    only(sparts)
    view(14, 24)
    K.frame_camera(world_pts(sparts), Vector(), 4, margin=0.04)
    render(out / 'sheet_angle.png')

    # turnaround at the floor camera
    turn = bpy.data.objects.new('turn', None)
    sc.collection.objects.link(turn)
    m = stand.matrix_world.copy()
    stand.parent = turn
    stand.matrix_world = m

    def spin(deg):
        turn.rotation_euler.z = math.radians(deg)
        bpy.context.view_layer.update()
    view(**FLOOR_VIEW)
    pts = []
    for i in range(8):
        spin(45 * i)
        pts += world_pts(sparts)
    K.frame_camera(pts, Vector(), 4, margin=0.05)
    for i in range(8):
        spin(45 * i)
        render(out / f'turn_{i * 45:03d}.png')
    head = [p for p in sparts if any(k in p.name for k in ('_head', 'faceplate', 'eyes', '_ear_'))]
    for name, (pitch, yaw, deg) in {'head_floor': (28, 33, 0), 'head_front': (8, 20, 0),
                                    'head_side': (8, 90, 0), 'head_back': (28, 33, 180)}.items():
        view(pitch, yaw)
        spin(deg)
        K.frame_camera(world_pts(head), Vector(), 12, margin=0.03)
        render(out / f'close_{name}.png', 96)
    view(**FLOOR_VIEW)
    spin(180)
    K.frame_camera(world_pts(sparts), Vector(), 8, margin=0.04)
    render(out / 'close_back.png', 96)
    spin(0)

    # hands, every pose in use, in its clip: a close-up of the hand(s) and the whole robot
    for pose, (clip, when, sides) in HAND_SHOTS.items():
        arm, parts, _ = shots[pose]
        fr = list(R.frames(arm))
        if when == 'thumb':
            th = next(p for p in parts if '_hand_' in p.name and p.name.endswith('_R'))
            f = max(fr, key=lambda g: (sc.frame_set(g), verts(th, 5)[:, 2].max())[1])
        else:
            f = fr[len(fr) // 2]
        sc.frame_set(f)
        info.setdefault(f'hand_{pose}', {})['frame'] = f
        seated = clip in SEATED_CLIPS
        only(parts + (bench if seated else []))
        view(**FLOOR_VIEW)
        close = [p for p in parts if ('_hand_' in p.name or 'sheet' in p.name or 'forearm' in p.name)
                 and (p.name[-1] in sides or 'sheet_paper' in p.name)]
        K.frame_camera(world_pts(close), Vector(), 12, margin=0.03)
        render(out / f'hand_{pose}_close.png', 96)
        K.frame_camera(world_pts(parts), Vector(), 4, margin=0.06)
        render(out / f'hand_{pose}_full.png')

    # the bench: seated typing, seated reading, and walking in the aisle in front of the desk, at 1x and 2x
    walk.matrix_world = Matrix.Translation((0.7, -1.55, 0)) @ Matrix.Rotation(math.radians(90), 4, 'Z') @ \
        walk.matrix_world
    bpy.context.view_layer.update()
    frame_box = world_pts(kit['chair']) + [Vector(p) for p in ((-0.9, -0.94, 0), (0.9, -0.94, 0), (0.9, -0.14, 0.74))]
    for name, rob, f in (('bench_typing', yparts, ref_frame(typing, info['typing'])),
                         ('bench_reading', rparts, ref_frame(reading, info['reading'])),
                         ('bench_walking', wparts, 12)):
        sc.frame_set(f)
        only(rob + bench)
        for mult in (1, 2):
            K.frame_camera(frame_box + world_pts(rob), Vector(), mult, margin=0.15)
            render(out / f'{name}_{mult}x.png', 96)
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
