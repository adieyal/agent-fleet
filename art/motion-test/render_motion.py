"""Render Mixamo clips on the rebuilt robot with the floor's one camera (orthographic, pitch 28, yaw 33;
docs/design/sprite-world.md on renovate/floor), through bakeoff.py's framing and studio.

    B=~/.local/bin/blender; S=art/motion-test/render_motion.py
    $B -b --factory-startup --python $S -- fit <furniture.json>   # seat and desk heights this robot needs
    $B -b --factory-startup --python $S -- review <out>           # the review set (see cmd_review)
    $B -b --factory-startup --python $S -- calib <out.png>        # standing robot at the sheet's angle
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
FURNITURE = HERE / 'furniture.json'
FLOOR_VIEW = dict(pitch=28.0, yaw=33.0)  # the floor's camera since floor review 1
WRIST_ABOVE_DESK = 0.04  # wrist joint above the desk top for a palm resting on it (the mitt's half-thickness)
SEATED = ['typing', 'sitting-idle', 'sitting-idle-hands-on-thighs', 'sitting-waiting', 'writing-seated',
          'thumbs-up-sitting']


def world_pts(objs) -> list:
    return [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]


def verts(ob) -> np.ndarray:
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get('co', co)
    co = np.c_[co.reshape(-1, 3), np.ones(len(co) // 3)]
    return (np.array(ob.matrix_world) @ co.T)[:3].T


def robot(fbx: Path, tag: str, pin=False, ref='first', hand_pose=None):
    arm, info = R.retarget(fbx, pin=pin, ref=ref)
    parts = R.attach(arm, tag, hand_pose)
    info |= R.ground(arm, parts)
    info['hand_pose'] = arm['hand_pose']
    return arm, parts, info


def piece(parts, tag, key):
    return next(p for p in parts if p.name == f'{tag}_{key}')


def seated_frame(arm) -> int:
    sc = bpy.context.scene
    root = arm.pose.bones[R.ROLES['root']]
    fr = list(R.frames(arm))
    z = []
    for f in fr:
        sc.frame_set(f)
        z.append((arm.matrix_world @ root.head).z)
    return fr[min(range(len(z)), key=z.__getitem__)]


def cmd_fit(dest: Path) -> None:
    """With the feet on the floor, where does each seated clip put the robot's seat (the lower of the pelvis and
    thigh undersides), and for typing, the hands (desk top) and the belly (desk edge)?"""
    res = {}
    for name in SEATED:
        A.reset()
        arm, parts, info = robot(MIX / f'{name}.fbx', 'r', ref='lowest')
        f = seated_frame(arm)
        bpy.context.scene.frame_set(f)
        seat = min(verts(piece(parts, 'r', k))[:, 2].min() for k in ('pelvis', 'thigh_L', 'thigh_R'))
        res[name] = {'seated_frame': f, 'seat_z_m': round(seat, 3)}
        if name == 'typing':
            wrists = [arm.pose.bones[R.ROLES[f'hand_{s}']] for s in 'LR']
            lows, fr = [], list(R.frames(arm))
            for g in fr[:: max(1, len(fr) // 60)]:
                bpy.context.scene.frame_set(g)
                lows.append(min((arm.matrix_world @ w.head).z for w in wrists))
            bpy.context.scene.frame_set(f)
            belly = verts(piece(parts, 'r', 'torso'))[:, 1].min()
            res[name] |= {'wrist_z_median_m': round(float(np.median(lows)), 3), 'belly_front_y_m': round(belly, 3)}
        print('FIT', name, json.dumps(res[name]))
    t = res['typing']
    seat = t['seat_z_m']
    desk_top = t['wrist_z_median_m'] - WRIST_ABOVE_DESK  # palms resting on the top
    edge = -(t['belly_front_y_m'] - 0.03)        # the near edge just clear of the belly, ahead of the seat point
    out = {'clips': res, 'seat_h_m': seat, 'desk_top_m': round(desk_top, 3), 'desk_edge_ahead_m': round(edge, 3),
           'chair_scale': round(seat / W.SEAT_H, 3), 'desk_scale': round(desk_top / W.DESK_H, 3),
           'floor_kit': {'seat_h_m': W.SEAT_H, 'desk_top_m': W.DESK_H}}
    dest.write_text(json.dumps(out, indent=1))
    print('FURNITURE', json.dumps({k: v for k, v in out.items() if k != 'clips'}))


def furniture(desk: bool) -> list:
    """The floor kit's chair and bench desk scaled to the robot (furniture.json): the chair by chair_scale about
    the floor under its seat, the desk by desk_scale, its near edge desk_edge_ahead_m ahead of the seat point."""
    f = json.loads(FURNITURE.read_text())
    m = W.materials()
    before = set(bpy.data.objects)
    W.chair(m, 'chair', 0.0, 0.0, 0.0)
    chair = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
    bpy.context.view_layer.update()  # matrix_world is stale until the new objects are evaluated
    for o in chair:  # seat point 0.2 ahead of the chair centre (bench() anchors it so), scaled with the chair
        o.matrix_world = Matrix.Translation((0, 0.2 * f['chair_scale'], 0)) @ Matrix.Scale(f['chair_scale'], 4) @ \
            o.matrix_world
    out = list(chair)
    if desk:
        before = set(bpy.data.objects)
        W.desk_frame(m, 'desk', -W.DESK_W / 2, -W.DESK_D / 2)
        A.box('keyboard', (0.30, 0.11, 0.018), (0, -0.13, W.DESK_H), m['black'], bevel=0.004)  # near edge: y = 0
        d = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
        bpy.context.view_layer.update()
        k = f['desk_scale']
        for o in d:  # scaled about the middle of its near (+Y) edge, which then sits desk_edge_ahead_m ahead
            o.matrix_world = Matrix.Translation((0, -f['desk_edge_ahead_m'], 0)) @ Matrix.Scale(k, 4) @ o.matrix_world
        out += d
    return out


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


def cmd_review(out: Path) -> None:
    A.reset()
    info = {}
    stand, sparts, info['standing'] = robot(MIX / 'standing-idle.fbx', 'stand')
    apose, aparts, _ = robot(MIX / 'standing-idle.fbx', 'apose', hand_pose='open')
    thumbs, tparts, info['thumbs'] = robot(MIX / 'thumbs-up-standing.fbx', 'thumbs')
    typing, yparts, info['typing'] = robot(MIX / 'typing.fbx', 'type', ref='lowest')
    walk, wparts, info['walk'] = robot(MIX / 'walk-normal.fbx', 'walk', pin=True)
    bench = furniture(desk=True)
    studio()
    sc = bpy.context.scene
    sc.frame_set(1)
    a_pose(apose)
    bpy.context.view_layer.update()

    # the model's A-pose from the front, beside robot-apose-front.png; the standing robot at the sheet's angle
    only(aparts)
    view(0, 0)
    K.frame_camera(world_pts(aparts), Vector(), 4, margin=0.04)
    render(out / 'apose_front.png')
    only(sparts)
    view(14, 24)
    K.frame_camera(world_pts(sparts), Vector(), 4, margin=0.04)
    render(out / 'sheet_angle.png')
    info['standing']['height_m'] = round(max(p.z for p in world_pts(sparts)), 3)

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
    # close-ups: head (floor camera, front, back) and the back
    head = [p for p in sparts if any(k in p.name for k in ('_head', 'faceplate', 'eyes'))]
    for name, (pitch, yaw, deg) in {'head_floor': (28, 33, 0), 'head_front': (8, 20, 0),
                                    'head_back': (28, 33, 180)}.items():
        view(pitch, yaw)
        spin(deg)
        K.frame_camera(world_pts(head), Vector(), 10, margin=0.03)
        render(out / f'close_{name}.png', 96)
    view(**FLOOR_VIEW)
    spin(180)
    K.frame_camera(world_pts(sparts), Vector(), 6, margin=0.04)
    render(out / 'close_back.png', 96)
    spin(0)

    # hands: thumbs up at its peak, the walk's fist, the typing hand; each close from the floor camera and front
    def hand_objs(parts, side):
        return [p for p in parts if p.name.endswith(f'_{side}') and ('_hand_' in p.name or 'forearm' in p.name)]
    thumb_R = next(p for p in tparts if '_hand_' in p.name and p.name.endswith('_R'))
    fr = list(R.frames(thumbs))
    peak = max(fr, key=lambda f: (sc.frame_set(f), verts(thumb_R)[:, 2].max())[1])
    info['thumbs']['peak_frame'] = peak
    sc.frame_set(peak)
    for name, parts, frame, side in (('thumbs_up', tparts, peak, 'R'), ('fist', wparts, 8, 'R'),
                                     ('open', yparts, int(info['typing']['ref_frame']) + 1, 'R')):
        sc.frame_set(frame)
        only(parts)
        for vname, (pitch, yaw) in {'floor': (28, 33), 'front': (10, 15)}.items():
            view(pitch, yaw)
            K.frame_camera(world_pts(hand_objs(parts, side)), Vector(), 12, margin=0.03)
            render(out / f'close_hand_{name}_{vname}.png', 96)
    view(**FLOOR_VIEW)
    sc.frame_set(peak)
    only(tparts)
    K.frame_camera(world_pts(tparts), Vector(), 4, margin=0.05)
    render(out / 'thumbs_up_full.png')

    # bench: typing robot at its chair, walking robot in the aisle in front of the desk
    walk.matrix_world = Matrix.Translation((0.55, -1.0, 0)) @ Matrix.Rotation(math.radians(90), 4, 'Z') @ \
        walk.matrix_world
    bpy.context.view_layer.update()
    seat_f = int(info['typing']['ref_frame']) + int(R.frames(typing).start)
    for name, rob, f in (('bench_typing', yparts, seat_f), ('bench_walking', wparts, 12)):
        sc.frame_set(f)
        only(rob + bench)
        frame_objs = rob + [o for o in bench if o.name.startswith('chair')]
        for mult in (1, 2):
            K.frame_camera(world_pts(frame_objs), Vector(), mult, margin=0.2)
            render(out / f'{name}_{mult}x.png', 96)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'info.json').write_text(json.dumps(info, indent=1))
    print('DONE', out)


def cmd_calib(dest: Path) -> None:
    A.reset()
    stand, parts, _ = robot(MIX / 'standing-idle.fbx', 'stand')
    studio()
    bpy.context.scene.frame_set(1)
    view(14, 24)
    K.frame_camera(world_pts(parts), Vector(), 2, margin=0.05)
    render(dest, 48)


def main() -> None:
    args = sys.argv[sys.argv.index('--') + 1:]
    {'fit': cmd_fit, 'review': cmd_review, 'calib': cmd_calib}[args[0]](Path(args[1]))


main()
