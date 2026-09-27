"""Render retargeted clips on the parts robot with the floor's B1 camera (art/scripts/bakeoff.py).

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/render_motion.py -- turnaround <out>
    ~/.local/bin/blender -b --factory-startup --python art/motion-test/render_motion.py -- \
        clip <out> <setting> [--pin] [--all] [--sheet N] <label>=<fbx> ...

setting: desk (chair + bench desk + keyboard; seated at the chair), chair (chair only), floor.
Every source in one call shares one camera, so their frames line up. Writes RGBA PNGs to
<out>/<label>/sheet_NN.png (N evenly spaced frames) and, with --all, <out>/<label>/all_NNNN.png, plus
<out>/info.json (retarget info and motion metrics).
"""

import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'scripts')]
import artlib as A  # noqa: E402
import bakeoff as K  # noqa: E402
import build_workbench as W  # noqa: E402
import motion_rig as R  # noqa: E402

HOST = '#27b3b8'  # teal, as robot-sheet.png's first robot
MULT = 2          # 2x the floor's 1x density (343 px/m)
KEYBOARD = dict(size=(0.30, 0.11, 0.018), y=-0.14 - 0.13)


def furniture(setting: str) -> list:
    if setting == 'floor':
        return []
    m = W.materials()
    before = set(bpy.data.objects)
    W.chair(m, 'chair', 0.0, 0.2, 0.0)  # seat point 0.2 ahead of the chair centre, as bench() anchors it
    if setting == 'desk':
        W.desk_frame(m, 'desk', -W.DESK_W / 2, -R_EDGE - W.DESK_D / 2)
        A.box('keyboard', KEYBOARD['size'], (0, KEYBOARD['y'], W.DESK_H), m['black'], bevel=0.004)
    return [o for o in bpy.data.objects if o not in before and o.type == 'MESH']


R_EDGE = 0.14  # desk edge ahead of the seat point (build_robot.DESK_EDGE_AHEAD)


def seat(arm, kind, parts, tag) -> dict:
    """Lift the robot onto the chair seat in seated frames, blending by how far the pelvis has dropped."""
    sc = bpy.context.scene
    root = arm.pose.bones[R.bone(arm, kind, 'root')]
    pelvis = next(p for p in parts if p.name == f'{tag}_pelvis')
    fr = list(R.frames(arm))
    z = []
    for f in fr:
        sc.frame_set(f)
        z.append((arm.matrix_world @ root.head).z)
    i_seat = min(range(len(z)), key=z.__getitem__)
    sc.frame_set(fr[i_seat])
    bottom = min((pelvis.matrix_world @ Vector(c)).z for c in pelvis.bound_box)
    lift = W.SEAT_H - bottom
    z_stand = max(z[0], z[-1], R.ANKLE_H + R.SHIN + R.THIGH)
    base = arm.location.z
    for f, zf in zip(fr, z):
        # full lift once the pelvis is within the lowest 30% of its drop, so seated frames don't bob
        t = min(1.0, max(0.0, (z_stand - zf) / max(1e-6, 0.7 * (z_stand - z[i_seat]))))
        arm.location.z = base + t * lift
        arm.keyframe_insert('location', index=2, frame=f)
    return {'seat_lift_m': round(lift, 3), 'seated_root_z_before_lift': round(z[i_seat], 3)}


def world_pts(objs) -> list:
    return [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]


def metrics(arm, kind, parts, tag, setting, drift) -> dict:
    """Motion numbers on the robot: jitter (hand/head/foot noise around a 5-frame average, mm), foot slide
    while planted, and for the desk setting where the hands sit relative to the desk top and keyboard."""
    sc = bpy.context.scene
    names = {r: R.bone(arm, kind, r) for r in ('hand_L', 'hand_R', 'head', 'foot_L', 'foot_R')}
    hands = {s: next(p for p in parts if p.name == f'{tag}_hand_{s}') for s in 'LR'}
    feet = {s: next(p for p in parts if p.name == f'{tag}_foot_{s}') for s in 'LR'}
    track = {k: [] for k in names}
    tips, soles = {s: [] for s in 'LR'}, {s: [] for s in 'LR'}
    for f in R.frames(arm):
        sc.frame_set(f)
        for k, n in names.items():
            track[k].append(arm.matrix_world @ arm.pose.bones[n].head)
        for s in 'LR':
            hp = world_pts([hands[s]])
            tips[s].append(min(hp, key=lambda p: p.z))
            soles[s].append(min(p.z for p in world_pts([feet[s]])))
    fps = 30

    def jitter(ps):  # frame-to-frame noise: RMS distance from a centred 5-frame moving average, mm
        if len(ps) < 5:
            return 0.0
        res = [(ps[i] - sum(ps[i - 2:i + 3], Vector()) / 5).length for i in range(2, len(ps) - 2)]
        return 1000 * math.sqrt(sum(r * r for r in res) / len(res))
    out = {'jitter_mm': {k: round(jitter(v), 2) for k, v in track.items()}}
    slide = []  # planted = sole within 2 cm of its lowest; pinned walks get their removed drift added back
    dx, dy = drift
    for s in 'LR':
        f = track[f'foot_{s}']
        lo = min(soles[s]) + 0.02
        for i in range(1, len(f)):
            if soles[s][i] < lo and soles[s][i - 1] < lo:
                v = (f[i] - f[i - 1]).to_2d() * fps
                slide.append((v.x + dx, v.y + dy))
    if slide:  # ground = the planted feet's typical velocity (a walk's treadmill); slip = spread around it
        mx = sorted(v[0] for v in slide)[len(slide) // 2]
        my = sorted(v[1] for v in slide)[len(slide) // 2]
        slip = sorted(math.hypot(v[0] - mx, v[1] - my) for v in slide)
        out['planted_foot_ground_speed_ms'] = round(math.hypot(mx, my), 3)
        out['planted_foot_slip_ms'] = round(slip[len(slip) // 2], 3)
    if setting == 'desk':
        kx, ky, _ = KEYBOARD['size']
        on = 0
        gap = []
        for s in 'LR':
            for p in tips[s]:
                gap.append(p.z - W.DESK_H)
                on += abs(p.x) < kx / 2 + 0.02 and abs(p.y - KEYBOARD['y']) < ky / 2 + 0.02 and abs(p.z - W.DESK_H - 0.02) < 0.03
        gap.sort()
        out['hand_low_point_above_desk_m'] = {'median': round(gap[len(gap) // 2], 3), 'min': round(gap[0], 3),
                                             'max': round(gap[-1], 3)}
        out['hand_frames_on_keyboard_pct'] = round(100 * on / len(gap), 1)
        wr = [track['hand_L'][i] for i in range(len(track['hand_L']))] + track['hand_R']
        out['wrist_y_median_m'] = round(sorted(p.y for p in wr)[len(wr) // 2], 3)
    return out


def camera(robots, extra) -> dict:
    pts = []
    for arm, parts in robots:
        fr = list(R.frames(arm))
        for f in fr[:: max(1, len(fr) // 16)] + [fr[-1]]:
            bpy.context.scene.frame_set(f)
            pts += world_pts(parts)
    pts += world_pts(extra)
    return K.frame_camera(pts, Vector((0, 0, 0)), MULT, margin=0.08)


def render(path: Path, samples: int) -> None:
    s = bpy.context.scene
    s.render.film_transparent = True
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'
    s.cycles.samples = samples
    s.cycles.use_denoising = True
    s.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def setup_scene(centre: Vector) -> None:
    K.studio(centre)
    K.shadow_catcher()
    bpy.context.scene.render.fps = 30


def cmd_clip(out: Path, setting: str, pin: bool, all_frames: bool, n_sheet: int, sources: list[str]) -> None:
    A.reset()
    robots, info = [], {}
    for spec in sources:
        label, path = spec.split('=', 1)
        ref = 'lowest' if setting in ('desk', 'chair') else 'first'
        arm, kind, rinfo = R.retarget(Path(path), pin=pin, ref=ref)
        parts = R.attach(arm, kind, label)
        if setting in ('desk', 'chair'):
            rinfo |= seat(arm, kind, parts, label)
        rinfo |= metrics(arm, kind, parts, label, setting, rinfo['pinned_drift_ms'])
        robots.append((arm, parts))
        info[label] = rinfo | {'fbx': Path(path).name}
        print('RETARGET', label, json.dumps(rinfo))
    R.tint(HOST)
    extra = furniture(setting)
    chair_desk = [o for o in extra if o.name.startswith('chair')]
    setup_scene(Vector((0, 0, 0.5)))
    cam = camera(robots, chair_desk)
    info['_camera'] = cam | {'mult': MULT, 'pitch': K.VIEW['pitch'], 'yaw': K.VIEW['yaw']}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'info.json').write_text(json.dumps(info, indent=2))
    sc = bpy.context.scene
    for i, (arm, parts) in enumerate(robots):
        label = sources[i].split('=', 1)[0]
        for j, (_, others) in enumerate(robots):
            for p in others:
                p.hide_render = j != i
        fr = list(R.frames(arm))
        if n_sheet < 2:  # metrics only
            continue
        picks = [fr[round(k * (len(fr) - 1) / (n_sheet - 1))] for k in range(n_sheet)]
        for k, f in enumerate(picks):
            sc.frame_set(f)
            render(out / label / f'sheet_{k:02d}.png', 48)
        if all_frames:  # webm frames at 1x, the floor's sprite density
            sc.render.resolution_percentage = 50
            for f in fr:
                sc.frame_set(f)
                render(out / label / f'all_{f - fr[0]:04d}.png', 16)
            sc.render.resolution_percentage = 100
    print('DONE', out)


def cmd_turnaround(out: Path, fbx: str, frame: int) -> None:
    A.reset()
    arm, kind, _ = R.retarget(Path(fbx), pin=False, ref='first')
    parts = R.attach(arm, kind, 'robot')
    R.tint(HOST)
    bpy.context.scene.frame_set(frame)
    setup_scene(Vector((0, 0, 0.5)))
    turn = bpy.data.objects.new('turn', None)
    bpy.context.scene.collection.objects.link(turn)
    loc = arm.matrix_world.copy()
    arm.parent = turn
    arm.matrix_world = loc
    views = []
    for i in range(8):
        turn.rotation_euler.z = math.radians(45 * i)
        bpy.context.view_layer.update()
        views.append(world_pts(parts))
    K.frame_camera([p for v in views for p in v], Vector((0, 0, 0)), 4, margin=0.05)
    for i in range(8):
        turn.rotation_euler.z = math.radians(45 * i)
        render(out / f'turn_{i}.png', 64)
    # straight front view, same density, for proportions against robot-sheet.png
    K.VIEW.update(pitch=0.0, yaw=0.0)
    turn.rotation_euler.z = 0
    bpy.context.view_layer.update()
    K.frame_camera(world_pts(parts), Vector((0, 0, 0)), 4, margin=0.05)
    render(out / 'front.png', 64)
    heights = {p.name: round(max(q.z for q in world_pts([p])), 3) for p in parts}
    (out / 'info.json').write_text(json.dumps({'top_m': max(heights.values()), 'part_tops_m': heights}, indent=2))
    print('DONE', out)


def main() -> None:
    args = sys.argv[sys.argv.index('--') + 1:]
    if args[0] == 'turnaround':
        cmd_turnaround(Path(args[1]), args[2], int(args[3]) if len(args) > 3 else 1)
        return
    out, setting = Path(args[1]), args[2]
    flags = [a for a in args[3:] if a.startswith('--')]
    n = next((int(a.split('=')[1]) for a in flags if a.startswith('--sheet=')), 10)
    cmd_clip(out, setting, '--pin' in flags, '--all' in flags, n, [a for a in args[3:] if not a.startswith('--')])


main()
