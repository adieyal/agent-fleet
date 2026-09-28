"""Render the concept robot for review: a turnaround, its faces and accessories, and a side-by-side
with the B2 sprites it is modelled on, seated in the same three poses at the same angle and head size.

    blender -b -P art/scripts/shoot_robot.py -- [OUT_DIR]

Needs art/build/robot/robot.blend (art/build.sh robot). Writes turnaround.png, looks.png and
the renders and each comparison's scale to OUT_DIR (default art/build/robot/review); robot_sheets.py lays
them out as turnaround.png, looks.png, clips.png and compare.png. Cycles on the GPU.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402

B2 = A.ART / 'bakeoff' / 'B2'
# the three B2 poses: sprite, arm clip, prop, and the B2 helmet width in sprite pixels (measured by hand)
POSES = [('robot-typing', 'Type', ('prop_laptop',), 104), ('robot-pencil', 'Write', ('prop_pencil', 'prop_paper'), 98),
         ('robot-tube', 'Hold', ('prop_flask',), 100)]
GREY, TEAL = '#c9c6c1', '#1fb5b0'
B2_PITCH, L2_YAW = 30.0, 21.25  # B2 was generated looking about 30° down (its STYLE prompt); l2's yaw
SIT_END = 10
CLIPS = ['Idle', 'Walking', 'Running', 'Wave', 'ThumbsUp', 'Yes', 'No', 'Death', 'Dance', 'Jump', 'WalkJump', 'Punch',
         'Sitting', 'Standing']
FACING = 35.0  # degrees the seated robot is turned from facing the camera towards screen left, as in B2


def args() -> Path:
    rest = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    return Path(rest[0]) if rest else A.BUILD / 'robot' / 'review'


def setup() -> None:
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'OPTIX'
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type == 'OPTIX'
    sc.cycles.device = 'GPU'
    sc.cycles.samples = 96
    sc.cycles.use_denoising = True
    sc.render.film_transparent = True
    sc.view_settings.view_transform = 'Standard'
    world = bpy.data.worlds.new('studio')
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs['Color'].default_value = A.srgb('#e3ebf5')
    world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.7
    sc.world = world
    # a soft key from the upper left of the view, a dim fill from the right, a rim from behind
    for name, loc, energy, size in (('key', (-5, -6, 8), 900, 5), ('fill', (7, -4, 3), 180, 6), ('rim', (2, 7, 6), 300, 4)):
        light = A.light(name, 'AREA', loc, energy)
        light.data.size = size
        A.aim(light, (0, 0, 0.4))


def show(only: dict) -> None:
    """Visibility of the optional layers: faces, accessories, props."""
    for o in bpy.data.objects:
        if o.name in ('robot_eyes', 'robot_band') or o.name.startswith(('acc_', 'prop_')):
            o.hide_render = not only.get(o.name, False)


def tint(color: str) -> None:
    bpy.data.materials['robot_body'].node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = A.srgb(color)


def pose(rig, clip: str, frame: float, arms: str | None = None) -> None:
    for pb in rig.pose.bones:  # from rest: a clip leaves the bones it doesn't key as they are
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    rig.animation_data.action = None
    rig.animation_data.action = bpy.data.actions[clip]
    bpy.context.scene.frame_set(int(frame))
    if arms:  # the seated pose, then the arm clip over it (it keys only the arms)
        held = {pb.name: (pb.location.copy(), pb.rotation_quaternion.copy()) for pb in rig.pose.bones}
        rig.animation_data.action = None
        for pb in rig.pose.bones:
            pb.location, pb.rotation_quaternion = held[pb.name]
        rig.animation_data.action = bpy.data.actions[arms]
        bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()


def bounds(root) -> tuple[Vector, Vector]:
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in bpy.data.objects:
        if o.type == 'MESH' and not o.hide_render:
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            pts += [ev.matrix_world @ v.co for v in me.vertices]
            ev.to_mesh_clear()
    return Vector([min(p[i] for p in pts) for i in range(3)]), Vector([max(p[i] for p in pts) for i in range(3)])


def head_px(cam, res: int) -> float:
    """The helmet's projected width in pixels."""
    body = bpy.data.objects['robot']
    g = body.vertex_groups['Head'].index
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(dg)
    me = ev.to_mesh()
    idx = [v.index for v in body.data.vertices if any(e.group == g and e.weight > 0.5 for e in v.groups)]
    mats = {i for i, s in enumerate(body.material_slots) if s.material.name == 'robot_body'}
    on_body = set()
    for p in body.data.polygons:
        if p.material_index in mats:
            on_body.update(p.vertices)
    xs = [world_to_camera_view(bpy.context.scene, cam, ev.matrix_world @ me.vertices[i].co).x for i in idx if i in on_body]
    ev.to_mesh_clear()
    return (max(xs) - min(xs)) * res


def render(cam, path: Path, res: int) -> None:
    sc = bpy.context.scene
    sc.camera = cam
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def camera(name: str, target: Vector, pitch: float, yaw: float, scale: float):
    return A.ortho_camera(name, target, pitch, yaw, scale, distance=30.0)


def main() -> None:
    A.require_blender()
    out = args()
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(A.BUILD / 'robot' / 'robot.blend'))
    setup()
    rig, root = bpy.data.objects['RobotArmature'], bpy.data.objects['RootNode']
    res = 512
    # turnaround: Idle, eight directions, from a little above
    tint(GREY)
    show({'robot_eyes': True})
    pose(rig, 'Idle', 1)
    lo, hi = bounds(root)
    c = (lo + hi) / 2
    size = (hi - lo).z * 1.12
    for k in range(8):
        cam = camera(f'turn{k}', c, 12, k * 45, size)
        render(cam, out / f'turn{k}.png', res)
    # looks: Claude band vs Codex eyes, and each accessory, three-quarter view
    for i, (only, color) in enumerate([({'robot_eyes': True}, TEAL), ({'robot_band': True}, TEAL),
                                       ({'robot_eyes': True, 'acc_backpack': True}, '#ff9340'),
                                       ({'robot_eyes': True, 'acc_antenna': True}, '#2dd4bf'),
                                       ({'robot_eyes': True, 'acc_halo': True}, '#a78bfa'),
                                       ({'robot_band': True, 'acc_crest': True}, '#facc15')]):
        show(only)
        tint(color)
        yaw = 150 if 'acc_backpack' in only else 30
        cam = camera(f'look{i}', c + Vector((0, 0, 0.1)), 20, yaw, size * 1.08)
        render(cam, out / f'look{i}.png', res)
    # clips: every original clip drives the new meshes; one frame from each, three-quarter view
    show({'robot_eyes': True})
    tint(TEAL)
    for clip in CLIPS:
        act = bpy.data.actions[clip]
        pose(rig, clip, (act.frame_range[0] + act.frame_range[1]) * 0.45)
        cam = camera(f'clip_{clip}', c + Vector((0, 0, 0.1)), 20, 30, size * 1.25)
        render(cam, out / f'clip_{clip}.png', 384)
    # compare: seated as in B2, the l2 camera, turned the way B2's robots face
    rig.rotation_mode = 'XYZ'
    root.rotation_euler.z = math.radians(L2_YAW - FACING)
    for name, arms, prop, b2_head in POSES:
        for color, tag in ((GREY, 'grey'), (TEAL, 'teal')):
            show({'robot_eyes': True, **{p: True for p in prop}})
            tint(color)
            pose(rig, 'Sitting', SIT_END, arms)
            lo, hi = bounds(root)
            c = (lo + hi) / 2
            cam = camera(f'cmp_{name}', c, B2_PITCH, L2_YAW, (hi - lo).length * 0.8)
            render(cam, out / f'cmp_{name}_{tag}.png', res)
            (out / f'cmp_{name}_{tag}.txt').write_text(f'{head_px(cam, res) / b2_head}\n')
    print('SHOT', out)


if __name__ == '__main__':
    main()
