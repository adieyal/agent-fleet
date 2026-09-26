"""Restyle the deck's RobotExpressive for the workbench: keep its rig and animations, give it l2's look,
and add seated arm actions.

    blender -b -P art/scripts/build_robot.py

Source: fleet/web/assets/models/robot/RobotExpressive.glb (CC0; see art/CREDITS.md).

- Materials: the yellow shell becomes `robot_body` (glossy, clear-coated; the runtime tints it with the host
  colour); grey parts become darker `robot_joint`; a dark glossy `robot_visor` plate with emissive
  `robot_eye` eyes is added to the head, as in l2.
- Shape: smooth shading, and one subdivision level on every part (the head's expression shape keys and
  its `*_Head` actions are dropped to allow it; the deck never used them).
- Scale: 0.24, so the head is about as big against a desk as l2's robots' heads.
- Actions: the original clips keep their names (`Sitting`, `Idle`, `Wave`, ...). Added arm-only clips
  `Rest`, `Type`, `Write` and `Hold` are posed on top of the end of `Sitting`: the runtime plays `Sitting`
  without its arm tracks and one of these for the arms. Their base poses are found by searching joint
  angles for hand targets on the desk (or, for `Hold`, beside the head).
- Props `prop_pencil` and `prop_flask` hang from the right hand bone; the runtime shows one per robot.

The build records `runtime.seat_point`: where the seated pelvis rests, in the robot's own coordinates.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Euler, Quaternion, Vector  # noqa: E402

SCENE = 'robot'
SOURCE = A.REPO / 'fleet' / 'web' / 'assets' / 'models' / 'robot' / 'RobotExpressive.glb'
SCALE = 0.27
FPS = 24
SIT_END = 10  # last frame of Sitting: the seated pose
ARM_BONES = ('UpperArm', 'LowerArm')
# metres, from the workbench: the desk top above the chair seat, and the desk edge ahead of the robot's
# seat anchor (the robot sits at the front of its chair: its arms are short)
DESK_ABOVE_SEAT, DESK_EDGE_AHEAD = 0.27, 0.14


def load() -> tuple[bpy.types.Object, bpy.types.Object]:
    if not SOURCE.exists():
        sys.exit(f'art: missing {SOURCE}')
    bpy.ops.import_scene.gltf(filepath=str(SOURCE))
    bpy.data.objects.remove(bpy.data.objects['Icosphere'])  # an unused helper shipped in the file
    for action in list(bpy.data.actions):
        if action.name.endswith('_Head'):
            bpy.data.actions.remove(action)
        else:
            action.name = action.name.removesuffix('_RobotArmature')
            action.use_fake_user = True
    head = bpy.data.objects['Head']
    if head.data.shape_keys:
        head.shape_key_clear()
    rig = bpy.data.objects['RobotArmature']
    rig['fleet'] = 'character'
    # rest pose: the visor and props are modelled against it
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    bpy.context.view_layer.update()
    return bpy.data.objects['RootNode'], rig


# --- look -------------------------------------------------------------------------------

def materials() -> dict:
    body = A.material('robot_body', '#41ced1', rough=0.3)
    bsdf = body.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Coat Weight'].default_value = 0.8  # the glossy, toy-like highlight (KHR_materials_clearcoat)
    bsdf.inputs['Coat Roughness'].default_value = 0.08
    return {'body': body,
            'joint': A.material('robot_joint', '#4f535c', rough=0.35, metal=0.3),
            'visor': A.material('robot_visor', '#0b0d10', rough=0.1),
            'eye': A.material('robot_eye', '#7ff7f4', rough=0.3, emission='#62f3ef'),
            'dark': A.material('robot_dark', '#15171b', rough=0.4),
            'glass': A.material('robot_glass', '#dff2f5', rough=0.05),
            'liquid': A.material('robot_liquid', '#8fe04a', rough=0.2, emission='#5fbf2a'),
            'pencil': A.material('robot_pencil', '#f0b43c', rough=0.5)}


def restyle(m: dict) -> None:
    swap = {'Main': m['body'], 'Grey': m['joint'], 'Black': m['dark']}
    for o in [o for o in bpy.data.objects if o.type == 'MESH']:
        for slot in o.material_slots:
            slot.material = swap[slot.material.name]
        # glTF import splits vertices along UV and normal seams; weld them first, or subdivision
        # pulls the pieces apart and opens holes all over the shell
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        bm.to_mesh(o.data)
        bm.free()
        if o.data.has_custom_normals:
            bpy.context.view_layer.objects.active = o
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        for p in o.data.polygons:
            p.use_smooth = True
        o.data.set_sharp_from_angle(angle=math.radians(50))
        # subdivision rounds the low-poly shells; applying keeps vertex weights on the skinned hands
        mod = o.modifiers.new('round', 'SUBSURF')
        mod.levels = mod.render_levels = 1
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.modifier_move_to_index(modifier='round', index=0)
        bpy.ops.object.modifier_apply(modifier='round')
    for mat in ('Main', 'Grey', 'Black'):
        if mat in bpy.data.materials:
            bpy.data.materials.remove(bpy.data.materials[mat])


def hang(ob: bpy.types.Object, rig: bpy.types.Object, bone: str) -> None:
    """Parent `ob` to `bone`, keeping where it was modelled."""
    bpy.context.view_layer.update()
    world = ob.matrix_world.copy()
    ob.parent, ob.parent_type, ob.parent_bone = rig, 'BONE', bone
    bpy.context.view_layer.update()
    ob.matrix_world = world


def visor(m: dict, rig: bpy.types.Object) -> None:
    """l2's face: the original black eyes and brows give way to a dark rounded plate with two glowing
    cyan eyes, placed where the old face was."""
    head = bpy.data.objects['Head']
    dark = [i for i, s in enumerate(head.material_slots) if s.material == m['dark']]
    bm = bmesh.new()
    bm.from_mesh(head.data)
    old_face = [f for f in bm.faces if f.material_index in dark]
    pts = [head.matrix_world @ v.co for f in old_face for v in f.verts]
    bmesh.ops.delete(bm, geom=old_face, context='FACES')
    bm.to_mesh(head.data)
    bm.free()
    cx = sum(p.x for p in pts) / len(pts)
    cz = sum(p.z for p in pts) / len(pts)
    bpy.context.view_layer.update()
    shell = [head.matrix_world @ v.co for v in head.data.vertices]
    front = min(p.y for p in shell if abs(p.z - cz) < 0.4 and abs(p.x - cx) < 0.6)  # face surface ahead
    plate = A.box('robot_face', (2.1, 0.2, 1.35), (cx, front + 0.04, cz - 0.62), m['visor'], kind='dynamic',
                  bevel=0.3, segments=5, tile=None)
    hang(plate, rig, 'Head')
    for sx in (-1, 1):
        eye = A.box(f'robot_eye_{sx:+d}', (0.3, 0.05, 0.46), (cx + 0.45 * sx, front - 0.075, cz - 0.18), m['eye'],
                    kind='dynamic', bevel=0.11, segments=4, tile=None)
        hang(eye, rig, 'Head')


def pose(rig, base: dict, offsets: dict) -> Vector:
    """Set the seated pose plus arm `offsets`; return the right palm's position."""
    for pb in rig.pose.bones:
        pb.rotation_quaternion = base[pb.name] @ offsets.get(pb.name, Euler()).to_quaternion()
    bpy.context.view_layer.update()
    return rig.matrix_world @ rig.pose.bones['Palm2.R'].head


def props(m: dict, rig: bpy.types.Object, base: dict, found: dict) -> None:
    """A pencil and a test tube in the right hand. Each is modelled in the pose that uses it (the pencil
    tilted onto the paper in Write, the tube upright in Hold), then hung from the hand bone."""
    rig.animation_data.action = None
    palm = pose(rig, base, found['Write'])
    pencil = A.cylinder('prop_pencil', 0.035, 0.8, palm + Vector((0, -0.15, -0.35)), m['pencil'], kind='dynamic',
                        segments=8, rot=(math.radians(-30), 0, 0), tile=None)
    hang(pencil, rig, 'Palm2.R')
    palm = pose(rig, base, found['Hold'])
    liquid = A.cylinder('prop_flask_liquid', 0.1, 0.3, palm + Vector((0, -0.12, 0.05)), m['liquid'], kind='dynamic',
                        segments=16, tile=None)
    flask = A.cylinder('prop_flask', 0.13, 0.9, palm + Vector((0, -0.12, 0.0)), m['glass'], kind='dynamic',
                       segments=16, bevel=0.03, tile=None)
    bpy.ops.object.select_all(action='DESELECT')
    liquid.select_set(True)
    flask.select_set(True)
    bpy.context.view_layer.objects.active = flask
    bpy.ops.object.join()
    hang(flask, rig, 'Palm2.R')
    for ob in (pencil, flask):
        ob['fleet'] = 'prop'
    pose(rig, base, {})


# --- seated arm actions -------------------------------------------------------------------

def pose_at_sit_end(rig) -> dict[str, Quaternion]:
    rig.animation_data.action = bpy.data.actions['Sitting']
    bpy.context.scene.frame_set(SIT_END)
    return {pb.name: pb.rotation_quaternion.copy() for pb in rig.pose.bones}


def seat_point(rig) -> Vector:
    """Where the seated robot rests on the chair: under its hips, at the underside of its thighs
    (the torso mesh hangs lower than the thighs, so it would sink the robot into the seat). Model units."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    lowest = min((o.matrix_world @ v.co).z for n in ('Leg.L', 'Leg.R')
                 for o in [bpy.data.objects[n].evaluated_get(dg)] for v in o.data.vertices)
    hips = rig.matrix_world @ rig.pose.bones['Hips'].head
    return Vector((hips.x, hips.y, lowest))


def reach(rig, side: str, base: dict, target: Vector) -> dict[str, Euler]:
    """Offsets (on top of the seated pose) for the upper and lower arm that bring the palm near `target`.
    Coordinate descent over four angles; the arm is short, so this settles in a few hundred updates."""
    bones = [f'UpperArm.{side}', f'LowerArm.{side}']
    angles = [0.0, 0.0, 0.0, 0.0]  # upper x, upper y, upper z, lower x (degrees)

    def apply(a):
        for name, e in ((bones[0], Euler([math.radians(v) for v in a[:3]])),
                        (bones[1], Euler((math.radians(a[3]), 0, 0)))):
            rig.pose.bones[name].rotation_quaternion = base[name] @ e.to_quaternion()
        bpy.context.view_layer.update()
        return (rig.matrix_world @ rig.pose.bones[f'Palm2.{side}'].head - target).length

    best = apply(angles)
    step = 40.0
    while step > 1.0:
        improved = False
        for i in range(4):
            for d in (step, -step):
                trial = angles.copy()
                trial[i] = max(-150.0, min(150.0, trial[i] + d))
                err = apply(trial)
                if err < best:
                    best, angles, improved = err, trial, True
        if not improved:
            step /= 2
    apply([0, 0, 0, 0])
    return {bones[0]: Euler([math.radians(v) for v in angles[:3]]), bones[1]: Euler((math.radians(angles[3]), 0, 0)),
            'error': best}


def arm_action(rig, name: str, base: dict, offsets: dict, seconds: float, motion) -> None:
    """A looping arm-only action: base seated pose x found offsets x `motion(bone, t)` (degrees, xyz)."""
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    rig.animation_data.action = action
    frames = round(seconds * FPS)
    steps = max(8, frames // 2)
    bones = [f'{b}.{s}' for s in 'LR' for b in ARM_BONES]
    for i in range(steps + 1):
        t = (i / steps) % 1.0
        for bone in bones:
            q = base[bone] @ offsets.get(bone, Euler()).to_quaternion()
            wobble = Euler([math.radians(v) for v in motion(bone, t)])
            rig.pose.bones[bone].rotation_quaternion = q @ wobble.to_quaternion()
            rig.pose.bones[bone].keyframe_insert('rotation_quaternion', frame=1 + round(i / steps * frames))
    rig.animation_data.action = None


def s(t, k=1, phase=0.0):
    return math.sin(2 * math.pi * (k * t + phase))


def actions(rig) -> dict:
    base = pose_at_sit_end(rig)
    seat = seat_point(rig)
    u = 1 / SCALE  # metres to model units
    desk_z = seat.z + DESK_ABOVE_SEAT * u + 0.15
    ahead = seat.y - (DESK_EDGE_AHEAD + 0.12) * u  # the robot faces -Y
    targets = {
        'Rest': {'L': Vector((0.75, ahead + 0.2, desk_z)), 'R': Vector((-0.75, ahead + 0.2, desk_z))},
        'Type': {'L': Vector((0.45, ahead - 0.3, desk_z)), 'R': Vector((-0.45, ahead - 0.3, desk_z))},
        'Write': {'L': Vector((0.75, ahead, desk_z)), 'R': Vector((-0.25, ahead - 0.45, desk_z))},
        'Hold': {'L': Vector((0.7, ahead, desk_z)), 'R': Vector((-1.35, ahead + 0.1, seat.z + 2.2))},
    }
    found, errors = {}, {}
    for name, sides in targets.items():
        found[name] = {}
        for side, target in sides.items():
            r = reach(rig, side, base, target)
            errors[f'{name}.{side}'] = round(r.pop('error'), 2)
            found[name].update(r)
    motions = {
        'Rest': (4.0, lambda b, t: (1.5 * s(t, 1, 0.3 if b.endswith('R') else 0), 0, 0)),
        'Type': (1.0, lambda b, t: ((6 if b.startswith('Lower') else 2) * max(0.0, s(t, 4, 0.5 if b.endswith('R') else 0)),
                                    0, 0)),
        'Write': (2.0, lambda b, t: ((3 * s(t, 3), 4 * s(t, 3, 0.25), 0) if b.endswith('R') else (0, 0, 0))),
        'Hold': (3.0, lambda b, t: ((3 * s(t), 0, 2 * s(t, 1, 0.25)) if b.endswith('R') else (1 * s(t), 0, 0))),
    }
    for name, (seconds, motion) in motions.items():
        arm_action(rig, name, base, found[name], seconds, motion)
    for pb in rig.pose.bones:
        pb.rotation_quaternion = base[pb.name]
    runtime = {'seat_point': [round(v * SCALE, 4) for v in seat], 'reach_error_units': errors}
    return runtime, base, found


def main() -> None:
    A.require_blender()
    A.reset()
    bpy.context.scene.render.fps = FPS
    root, rig = load()
    m = materials()
    restyle(m)
    visor(m, rig)
    runtime, base, found = actions(rig)
    props(m, rig, base, found)
    rig.animation_data.action = None
    root.scale = (SCALE,) * 3
    info = {'kind': 'character', 'source': 'RobotExpressive.glb (CC0)', 'scale': SCALE,
            'actions': sorted(a.name for a in bpy.data.actions), 'props': ['prop_pencil', 'prop_flask'],
            'arm_bones': list(ARM_BONES), 'warm_groups': [], 'seat_height': None, 'runtime': runtime}
    A.save_blend(SCENE, info)
    print('BUILD', SCENE, info)


main()
