"""Build the rounded, friendly robot: one rigid-skinned mesh on a small armature, seated rest pose,
with looping actions `idle`, `type`, `write` and `hold`, and two props on the right hand.

    blender -b -P art/scripts/build_robot.py

The origin is the centre of the chair seat; the robot faces -Y. The body shell uses material
`robot_body`, which the runtime tints with the host colour; joints are `robot_joint`, the face
plate `robot_visor`, the eyes `robot_eye` (emissive). Props `prop_pencil` and `prop_flask` hang
from bone `hand.R` and are hidden unless a robot's action needs them.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

SCENE = 'robot'
FPS = 24

# joints of the seated rest pose (robot's left is +X)
SHOULDER = Vector((0.205, 0.02, 0.40))
ELBOW = Vector((0.225, -0.1, 0.25))
WRIST = Vector((0.16, -0.40, 0.31))
HAND_TIP = Vector((0.15, -0.48, 0.31))
BONES = {  # name: (head, tail, parent)
    'root': ((0, 0, 0), (0, 0, 0.12), None),
    'spine': ((0, 0, 0.12), (0, 0, 0.44), 'root'),
    'head': ((0, 0, 0.46), (0, 0, 0.8), 'spine'),
}
for side, sx in (('L', 1), ('R', -1)):
    m = Vector((sx, 1, 1))
    BONES[f'upper_arm.{side}'] = (SHOULDER * m, ELBOW * m, 'spine')
    BONES[f'forearm.{side}'] = (ELBOW * m, WRIST * m, f'upper_arm.{side}')
    BONES[f'hand.{side}'] = (WRIST * m, HAND_TIP * m, f'forearm.{side}')


def mats() -> dict:
    body = A.material('robot_body', '#41ced1', rough=0.3)
    # a clear coat over the shell: the glossy, toy-like highlight of l2's robots (KHR_materials_clearcoat)
    bsdf = body.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Coat Weight'].default_value = 0.8
    bsdf.inputs['Coat Roughness'].default_value = 0.08
    return {'body': body,
            'joint': A.material('robot_joint', '#858b95', rough=0.35, metal=0.3),
            'visor': A.material('robot_visor', '#0b0d0f', rough=0.12),
            'eye': A.material('robot_eye', '#7ff7f4', rough=0.3, emission='#62f3ef'),
            'glass': A.material('robot_glass', '#dff2f5', rough=0.05),
            'liquid': A.material('robot_liquid', '#8fe04a', rough=0.2, emission='#5fbf2a'),
            'pencil': A.material('robot_pencil', '#f0b43c', rough=0.5)}


def part(ob, bone: str) -> bpy.types.Object:
    ob['bone'] = bone
    return ob


def rbox(name, size, centre, mat, bone, radius, rot=(0, 0, 0)):
    """Rounded box centred on `centre`."""
    loc = (centre[0], centre[1], centre[2] - size[2] / 2)
    return part(A.box(name, size, loc, mat, kind='dynamic', bevel=radius, segments=5, rot=rot, tile=None), bone)


def limb(name, a: Vector, b: Vector, width, mat, bone, radius=None):
    """A rounded bar from joint a to joint b."""
    d = b - a
    length = d.length
    ob = A.box(name, (width, width, length), (0, 0, 0), mat, kind='dynamic', bevel=radius or width * 0.45,
               segments=5, tile=None)
    ob.rotation_mode = 'QUATERNION'
    ob.rotation_quaternion = d.to_track_quat('Z', 'Y')
    ob.location = a
    return part(ob, bone)


def body(m) -> None:
    rbox('pelvis', (0.28, 0.25, 0.13), (0, 0.03, 0.07), m['body'], 'root', 0.06)
    rbox('torso', (0.34, 0.26, 0.31), (0, 0.02, 0.29), m['body'], 'spine', 0.11)
    rbox('chest_plate', (0.16, 0.02, 0.1), (0, -0.115, 0.3), m['joint'], 'spine', 0.009)
    part(A.cylinder('neck', 0.05, 0.06, (0, 0.01, 0.43), m['joint'], kind='dynamic', segments=20, tile=None), 'head')
    # a big round head, as in the concept: wider than the torso, with a dark face plate
    # nearly a capsule: the bevel takes almost half the height, so it reads as a round helmet
    rbox('head', (0.46, 0.38, 0.36), (0, 0, 0.67), m['body'], 'head', 0.17)
    rbox('visor', (0.38, 0.06, 0.25), (0, -0.172, 0.665), m['visor'], 'head', 0.085)
    for sx in (-1, 1):
        rbox(f'eye_{sx:+d}', (0.056, 0.012, 0.086), (0.075 * sx, -0.205, 0.672), m['eye'], 'head', 0.027)
        ear = A.cylinder(f'ear_{sx:+d}', 0.07, 0.05, (0.22 * sx, 0, 0.66), m['joint'], kind='dynamic', segments=24,
                         bevel=0.012, rot=(0, math.pi / 2 * sx, 0), tile=None)
        part(ear, 'head')
    part(A.cylinder('antenna', 0.024, 0.05, (0, 0.0, 0.835), m['joint'], kind='dynamic', segments=16, bevel=0.008,
                    tile=None), 'head')
    for side, sx in (('L', 1), ('R', -1)):
        mx = Vector((sx, 1, 1))
        part(A.ball(f'shoulder.{side}', 0.075, SHOULDER * mx, m['joint']), f'upper_arm.{side}')
        limb(f'upper_arm_shell.{side}', SHOULDER * mx, ELBOW * mx, 0.1, m['body'], f'upper_arm.{side}')
        part(A.ball(f'elbow.{side}', 0.055, ELBOW * mx, m['joint']), f'forearm.{side}')
        limb(f'forearm_shell.{side}', ELBOW * mx, WRIST * mx, 0.09, m['body'], f'forearm.{side}')
        part(A.ball(f'hand.{side}', 0.055, (WRIST + Vector((0, -0.035, 0))) * mx, m['joint'], scale=(1.0, 1.2, 0.8)),
             f'hand.{side}')
        # legs: thigh forward along the seat, shin down to the floor, a rounded foot
        hip, knee, ankle = Vector((0.095 * sx, 0.0, 0.07)), Vector((0.1 * sx, -0.36, 0.07)), Vector((0.1 * sx, -0.38, -0.40))
        limb(f'thigh.{side}', hip, knee, 0.13, m['body'], 'root')
        part(A.ball(f'knee.{side}', 0.06, knee, m['joint']), 'root')
        limb(f'shin.{side}', knee, ankle, 0.11, m['body'], 'root')
        rbox(f'foot.{side}', (0.13, 0.22, 0.07), (0.1 * sx, -0.42, -0.435), m['joint'], 'root', 0.03)


def armature() -> bpy.types.Object:
    arm = bpy.data.armatures.new('robot_rig')
    ob = bpy.data.objects.new('robot', arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for name, (head, tail, parent) in BONES.items():
        b = arm.edit_bones.new(name)
        b.head, b.tail = head, tail
        b.roll = 0.0
        if parent:
            b.parent = arm.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    ob['fleet'] = 'character'
    return ob


def skin(rig: bpy.types.Object) -> bpy.types.Object:
    """Join the parts into one mesh, each part weighted wholly to its bone."""
    parts = sorted((o for o in bpy.data.objects if 'bone' in o), key=lambda o: o.name)
    for o in parts:
        vg = o.vertex_groups.new(name=o['bone'])
        vg.add(range(len(o.data.vertices)), 1.0, 'REPLACE')
    bpy.ops.object.select_all(action='DESELECT')
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    mesh = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    mesh.name = mesh.data.name = 'robot_mesh'
    del mesh['bone']
    mesh['fleet'] = 'character'
    mesh.parent = rig
    mod = mesh.modifiers.new('rig', 'ARMATURE')
    mod.object = rig
    return mesh


def prop(ob, rig, bone='hand.R') -> None:
    """Hang a prop from a bone, keeping where it was modelled."""
    world = ob.matrix_world.copy()
    ob.parent = rig
    ob.parent_type = 'BONE'
    ob.parent_bone = bone
    bpy.context.view_layer.update()
    ob.matrix_world = world
    ob['fleet'] = 'prop'


def props(m, rig) -> None:
    w = WRIST * Vector((-1, 1, 1)) + Vector((0, -0.04, 0))
    pencil = A.cylinder('prop_pencil', 0.007, 0.17, w + Vector((0, 0, -0.03)), m['pencil'], kind='dynamic',
                        segments=8, rot=(math.radians(-35), 0, 0), tile=None)
    bpy.context.view_layer.update()
    prop(pencil, rig)
    liquid = A.cylinder('prop_flask_liquid', 0.017, 0.06, w + Vector((0, 0, 0.025)), m['liquid'], kind='dynamic',
                        segments=16, tile=None)
    flask = A.cylinder('prop_flask', 0.022, 0.16, w + Vector((0, 0, 0.02)), m['glass'], kind='dynamic', segments=16,
                       bevel=0.006, tile=None)
    bpy.ops.object.select_all(action='DESELECT')
    liquid.select_set(True)
    flask.select_set(True)
    bpy.context.view_layer.objects.active = flask
    bpy.ops.object.join()
    bpy.context.view_layer.update()
    prop(flask, rig)


# --- actions ---------------------------------------------------------------------

def key(rig, action_name: str, seconds: float, poses) -> None:
    """Make a looping action: `poses(t)` returns {bone: (rx, ry, rz) degrees} for t in [0, 1)."""
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    rig.animation_data_create()
    rig.animation_data.action = action
    frames = round(seconds * FPS)
    steps = max(8, frames // 3)
    for pb in rig.pose.bones:
        pb.rotation_mode = 'XYZ'
    for i in range(steps + 1):
        t = i / steps
        frame = 1 + round(t * frames)
        pose = poses(t % 1.0)
        for pb in rig.pose.bones:
            rx, ry, rz = pose.get(pb.name, (0, 0, 0))
            pb.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
            pb.keyframe_insert('rotation_euler', frame=frame)
    for fc in action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'BEZIER'
    track = rig.animation_data.nla_tracks.new()
    track.name = action_name
    track.strips.new(action_name, 1, action)
    rig.animation_data.action = None


def s(t, k=1, phase=0.0):
    return math.sin(2 * math.pi * (k * t + phase))


def actions(rig) -> None:
    key(rig, 'idle', 4.0, lambda t: {
        'spine': (1.2 * s(t), 0, 0),
        'head': (2 * s(t, 1, 0.25), 0, 7 * s(t)),
        'forearm.L': (1.5 * s(t, 1, 0.1), 0, 0), 'forearm.R': (1.5 * s(t, 1, 0.6), 0, 0)})
    key(rig, 'type', 1.0, lambda t: {
        'spine': (1.0, 0, 0),
        'head': (-6 + 1.5 * s(t, 2), 0, 2 * s(t)),
        'forearm.L': (5 * max(0.0, s(t, 4)), 0, 0), 'forearm.R': (5 * max(0.0, s(t, 4, 0.5)), 0, 0),
        'hand.L': (-8 * max(0.0, s(t, 4, 0.1)), 0, 0), 'hand.R': (-8 * max(0.0, s(t, 4, 0.6)), 0, 0)})
    key(rig, 'write', 2.0, lambda t: {
        'spine': (3, 0, 0),
        'head': (-10, 0, 4 * s(t, 0.5)),
        'forearm.R': (3 * s(t, 3), 4 * s(t, 3, 0.25), 0),
        'hand.R': (6 * s(t, 6), 0, 5 * s(t, 3, 0.25))})
    key(rig, 'hold', 3.0, lambda t: {
        'spine': (-2, 0, 3),
        'head': (6, 0, -14 + 3 * s(t)),
        # arm up and out to the side, so the flask shows above the desk clutter as in l2
        'upper_arm.R': (-80 + 3 * s(t), 0, 25), 'forearm.R': (-30, 0, 0), 'hand.R': (25, 0, 4 * s(t, 1, 0.3))})


def main() -> None:
    A.require_blender()
    A.reset()
    bpy.context.scene.render.fps = FPS
    m = mats()
    body(m)
    rig = armature()
    skin(rig)
    props(m, rig)
    actions(rig)
    info = {'kind': 'character', 'actions': ['idle', 'type', 'write', 'hold'], 'props': ['prop_pencil', 'prop_flask'],
            'warm_groups': [], 'seat_height': None}
    A.save_blend(SCENE, info)
    print('BUILD', SCENE, info)


main()
