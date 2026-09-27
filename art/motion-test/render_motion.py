"""Render Mixamo clips on the parts robot with the floor's B1 camera (art/scripts/bakeoff.py).

    B=~/.local/bin/blender; S=art/motion-test/render_motion.py
    $B -b --factory-startup --python $S -- review <out>          # assembly review set (see cmd_review)
    $B -b --factory-startup --python $S -- seatcheck <out.json>  # seated clips: pelvis vs seat, feet vs floor
    $B -b --factory-startup --python $S -- clip <out> <setting> [--pin] [--all] [--sheet=N] <label>=<fbx> ...

clip settings: desk (chair + bench desk + keyboard, seated at the chair), chair (chair only), floor.
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
KEYBOARD = dict(size=(0.30, 0.11, 0.018), y=-0.14 - 0.13)
DESK_EDGE = 0.14  # desk edge ahead of the seat point (build_robot.DESK_EDGE_AHEAD)
SEATED = ['typing', 'sitting-idle', 'sitting-idle-hands-on-thighs', 'sitting-waiting', 'writing-seated',
          'thumbs-up-sitting', 'sit-to-stand', 'stand-to-sit']


def furniture(setting: str) -> list:
    if setting == 'floor':
        return []
    m = W.materials()
    before = set(bpy.data.objects)
    W.chair(m, 'chair', 0.0, 0.2, 0.0)  # seat point 0.2 ahead of the chair centre, as bench() anchors it
    if setting == 'desk':
        W.desk_frame(m, 'desk', -W.DESK_W / 2, -DESK_EDGE - W.DESK_D / 2)
        A.box('keyboard', KEYBOARD['size'], (0, KEYBOARD['y'], W.DESK_H), m['black'], bevel=0.004)
    return [o for o in bpy.data.objects if o not in before and o.type == 'MESH']


def world_pts(objs) -> list:
    return [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]


def lowest(ob) -> float:
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get('co', co)
    co = np.c_[co.reshape(-1, 3), np.ones(len(co) // 3)]
    return float((np.array(ob.matrix_world) @ co.T)[2].min())


def robot(fbx: Path, tag: str, pin=False, ref='first'):
    arm, info = R.retarget(fbx, pin=pin, ref=ref)
    parts = R.attach(arm, tag)
    info |= R.ground(arm, parts)
    return arm, parts, info


def seat_report(arm, parts, tag) -> dict:
    """At the most seated frame: pelvis underside vs the chair seat, and each sole vs the floor."""
    sc = bpy.context.scene
    root = arm.pose.bones[R.ROLES['root']]
    fr = list(R.frames(arm))
    z = []
    for f in fr:
        sc.frame_set(f)
        z.append((arm.matrix_world @ root.head).z)
    f = fr[min(range(len(z)), key=z.__getitem__)]
    sc.frame_set(f)
    pelvis = next(p for p in parts if p.name.startswith(f'{tag}_pelvis'))
    feet = {s: next(p for p in parts if p.name == f'{tag}_foot_foot_{s}') for s in 'LR'}
    thighs = [p for p in parts if p.name.startswith(f'{tag}_thigh_')]
    contact = min([lowest(pelvis)] + [lowest(t) for t in thighs])  # whichever rests on the seat
    return {'seated_frame': f, 'pelvis_above_seat_m': round(lowest(pelvis) - W.SEAT_H, 3),
            'seat_contact_above_seat_m': round(contact - W.SEAT_H, 3),
            'sole_above_floor_m': {s: round(lowest(o), 3) for s, o in feet.items()}}


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


def cmd_review(out: Path) -> None:
    """Turnaround (8 B1 angles, 4x) plus a sheet-angle view, close-ups of head, hands and back, and a seated
    typing frame and a walking frame at the bench at 1x and 2x. Writes <out>/info.json with the checks."""
    A.reset()
    info = {}
    stand, sparts, info['standing'] = robot(MIX / 'standing-idle.fbx', 'stand')
    thumbs, tparts, info['thumbs'] = robot(MIX / 'thumbs-up-standing.fbx', 'thumbs')
    typing, yparts, info['typing'] = robot(MIX / 'typing.fbx', 'type', ref='lowest')
    info['typing'] |= seat_report(typing, yparts, 'type')
    walk, wparts, info['walk'] = robot(MIX / 'walk-normal.fbx', 'walk', pin=True)
    bench = furniture('desk')
    K.studio(Vector((0, 0, 0.6)))
    K.shadow_catcher()
    sc = bpy.context.scene
    catcher = bpy.data.objects['catcher']

    # standing robot at the origin, others parked
    sc.frame_set(1)
    turn = bpy.data.objects.new('turn', None)
    sc.collection.objects.link(turn)
    m = stand.matrix_world.copy()
    stand.parent = turn
    stand.matrix_world = m

    def spin(deg):
        turn.rotation_euler.z = math.radians(deg)
        bpy.context.view_layer.update()

    only(sparts)
    view(44.5, 21.25)
    pts = []
    for i in range(8):
        spin(45 * i)
        pts += world_pts(sparts)
    K.frame_camera(pts, Vector(), 4, margin=0.05)
    for i in range(8):
        spin(45 * i)
        render(out / f'turn_{i * 45:03d}.png')
    spin(0)
    view(14, 24)  # roughly robot-sheet.png's camera
    K.frame_camera(world_pts(sparts), Vector(), 4, margin=0.05)
    render(out / 'sheet_angle.png')
    view(0, 0)
    K.frame_camera(world_pts(sparts), Vector(), 4, margin=0.05)
    render(out / 'front.png')
    info['standing']['height_m'] = round(max(p.z for p in world_pts(sparts)), 3)
    info['standing']['part_top_m'] = {p.name: round(max(q.z for q in world_pts([p])), 3) for p in sparts}

    # close-ups
    head = [p for p in sparts if any(k in p.name for k in ('helmet', 'visor', 'ear', 'eye'))]
    for name, (pitch, yaw, deg) in {'head_b1': (44.5, 21.25, 0), 'head_front': (8, 20, 0),
                                    'head_back': (30, 21.25, 180)}.items():
        view(pitch, yaw)
        spin(deg)
        K.frame_camera(world_pts(head), Vector(), 10, margin=0.03)
        render(out / f'close_{name}.png', 96)
    spin(180)
    view(44.5, 21.25)
    K.frame_camera(world_pts(sparts), Vector(), 6, margin=0.04)
    render(out / 'close_back.png', 96)
    spin(0)
    stand.hide_render = True
    # hands: the thumbs-up at its peak (thumb tip highest) and the relaxed standing hand
    r_thumb = next(p for p in tparts if p.name == 'thumbs_thumb_thumb_R')
    l_thumb = next(p for p in tparts if p.name == 'thumbs_thumb_thumb_L')
    fr = list(R.frames(thumbs))
    best = max(fr, key=lambda f: (sc.frame_set(f), max(max(q.z for q in world_pts([t]))
                                                      for t in (r_thumb, l_thumb)))[1])
    sc.frame_set(best)
    info['thumbs']['peak_frame'] = best
    hand_side = 'R' if max(q.z for q in world_pts([r_thumb])) > max(q.z for q in world_pts([l_thumb])) else 'L'
    hand = [p for p in tparts if p.name.endswith(f'_{hand_side}') and
            any(k in p.name for k in ('palm', 'fingers', 'thumb', 'forearm'))]
    only(tparts)
    for name, (pitch, yaw) in {'hand_thumbs_up_b1': (44.5, 21.25), 'hand_thumbs_up_side': (10, 70)}.items():
        view(pitch, yaw)
        K.frame_camera(world_pts(hand), Vector(), 12, margin=0.03)
        render(out / f'close_{name}.png', 96)
    view(44.5, 21.25)
    K.frame_camera(world_pts(tparts), Vector(), 4, margin=0.05)
    render(out / 'thumbs_up_full.png')

    # bench: typing robot at its chair, walking robot in the aisle in front of the desk
    stand.hide_render = thumbs.hide_render = True
    sc.frame_set(int(info['typing']['seated_frame']))
    walk.matrix_world = Matrix.Translation((0.75, -1.35, 0)) @ Matrix.Rotation(math.radians(90), 4, 'Z') @ \
        walk.matrix_world
    bpy.context.view_layer.update()
    view(44.5, 21.25)
    for name, rob, extra in (('bench_typing', yparts, bench), ('bench_walking', wparts, bench)):
        if name == 'bench_walking':
            sc.frame_set(12)
        only(rob + extra)
        frame_objs = rob + [o for o in extra if o.name.startswith('chair')] if name == 'bench_typing' else rob
        for mult in (1, 2):
            K.frame_camera(world_pts(frame_objs), Vector(), mult, margin=0.25)
            render(out / f'{name}_{mult}x.png', 96)
    catcher.hide_render = False
    out.mkdir(parents=True, exist_ok=True)
    (out / 'info.json').write_text(json.dumps(info, indent=1))
    print('DONE', out)


def cmd_calib(dest: Path) -> None:
    """Just the sheet-angle view of the standing robot, for calibrate_colours.py."""
    A.reset()
    stand, parts, _ = robot(MIX / 'standing-idle.fbx', 'stand')
    K.studio(Vector((0, 0, 0.6)))
    K.shadow_catcher()
    bpy.context.scene.frame_set(1)
    view(14, 24)
    K.frame_camera(world_pts(parts), Vector(), 2, margin=0.05)
    render(dest, 48)


def cmd_seatcheck(dest: Path) -> None:
    res = {}
    for name in SEATED:
        A.reset()
        arm, parts, info = robot(MIX / f'{name}.fbx', 'r', ref='lowest')
        res[name] = info | seat_report(arm, parts, 'r')
        print('SEAT', name, json.dumps(res[name]))
    dest.write_text(json.dumps(res, indent=1))


def main() -> None:
    args = sys.argv[sys.argv.index('--') + 1:]
    if args[0] == 'review':
        cmd_review(Path(args[1]))
    elif args[0] == 'calib':
        cmd_calib(Path(args[1]))
    elif args[0] == 'seatcheck':
        cmd_seatcheck(Path(args[1]))
    else:
        sys.exit(f'unknown command {args[0]}')


main()
