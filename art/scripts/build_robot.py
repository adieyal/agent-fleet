"""The concept robot on RobotExpressive's rig: new meshes in the style of the B2 sprites, skinned to the
deck robot's skeleton so every one of its clips drives them, plus seated arm actions.

    blender -b -P art/scripts/build_robot.py

Source: packages/fleet-web/src/fleet_web/static/assets/models/robot/RobotExpressive.glb (CC0; see art/CREDITS.md). Only its armature and
actions are kept; its meshes are deleted.

- Shape (art/bakeoff/B2/robot-*.webp): a near-spherical glossy helmet with ear discs, a large dark rounded
  face plate, a rounded torso with a chest panel, a dark pelvis, capsule limbs with darker ball joints,
  dark mitten hands and chunky boots. Every part is a quad cage (a spherified cube bent to a
  superellipsoid or capsule) with one subdivision level applied; discs and panels are solidified and
  bevelled.
- Skinning: one mesh, `robot`, with each part weighted to its bone (the torso blends from `Abdomen` to
  `Torso`); the fingers follow their finger bones.
- Materials: `robot_body` (glossy, clear-coated; the runtime tints it with the host colour), `robot_joint`
  (dark grey joints and hands), `robot_panel` (light grey chest panel and ear rims), `robot_visor`.
- Face layers, both hung from the head bone; the runtime shows one per agent: `robot_eyes` (two glowing
  eyes, material `robot_eye`: Codex) and `robot_band` (a band across the visor, material `robot_band`:
  Claude).
- Accessories, hung from bones; the runtime shows the host's one: `acc_backpack` (Torso), `acc_antenna`,
  `acc_halo`, `acc_crest` (Head). Host-coloured parts use `robot_body`, the halo `robot_halo`.
- Scale: the helmet is HEAD_M wide, as big against a desk as the restyled robot's head was.
- Actions: the original clips keep their names (`Sitting`, `Idle`, `Wave`, ...). Added arm-only clips
  `Rest`, `Type`, `Write` and `Hold` are posed on top of the end of `Sitting`: the runtime plays `Sitting`
  without its arm tracks and one of these for the arms. Their base poses are found by searching joint
  angles for hand targets on the desk (or, for `Hold`, beside the head).
- Props, sized to B2 against the helmet: `prop_pencil` and `prop_flask` hang from the right hand bone;
  `prop_laptop` and `prop_paper` sit on the desk under the typing and writing hands. The runtime shows
  the one its loop uses.

The build records `runtime.seat_point`: where the seated pelvis rests, in the robot's own coordinates.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Euler, Matrix, Quaternion, Vector  # noqa: E402

SCENE = 'robot'
SOURCE = A.REPO / 'packages' / 'fleet-web' / 'src' / 'fleet_web' / 'static' / 'assets' / 'models' / 'robot' / 'RobotExpressive.glb'
HEAD_M = 0.404  # helmet width in metres: B2's robots, fitted to l2 by head size (104 px at 257.3 px/m)
FPS = 24
SIT_END = 10  # last frame of Sitting: the seated pose
ARM_BONES = ('UpperArm', 'LowerArm')
# metres, from the workbench: the desk top above the chair seat, and the desk edge ahead of the robot's
# seat anchor (the robot sits at the front of its chair: its arms are short)
DESK_ABOVE_SEAT, DESK_EDGE_AHEAD = 0.27, 0.14
ACCESSORIES = ('backpack', 'antenna', 'halo', 'crest')

# Proportions, in the rig's units: it stands on z 0 facing -Y, arms hanging forward, in the pose its nodes
# rest in. Joints sit on the bones; everything else is shaped to the B2 sprites.
HEAD = {'c': Vector((0, -0.02, 3.55)), 'r': (0.72, 0.685, 0.685), 'e': 2.15}
TORSO = {'c': Vector((0, 0.03, 2.08)), 'r': (0.58, 0.47, 0.84)}
LIMB = {'upper': (0.17, 0.16), 'fore': (0.17, 0.2), 'thigh': (0.25, 0.22), 'shin': (0.22, 0.25), 'finger': 0.074}


def load() -> tuple[bpy.types.Object, bpy.types.Object]:
    if not SOURCE.exists():
        sys.exit(f'art: missing {SOURCE}')
    # rest on the nodes' own transforms, not the skin's T-pose bind: clips key only some bones, and the
    # rest must stay where three.js leaves them, in the nodes' pose
    bpy.ops.import_scene.gltf(filepath=str(SOURCE), guess_original_bind_pose=False)
    for action in list(bpy.data.actions):
        if action.name.endswith('_Head'):  # the old face's shape-key clips
            bpy.data.actions.remove(action)
        else:
            action.name = action.name.removesuffix('_RobotArmature')
            action.use_fake_user = True
    rig = bpy.data.objects['RobotArmature']
    rig['fleet'] = 'character'
    # rest pose: the new meshes are modelled against it
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    bpy.context.view_layer.update()
    for o in [o for o in bpy.data.objects if o.type == 'MESH']:  # the old robot, and a stray helper sphere
        bpy.data.meshes.remove(o.data)
    return bpy.data.objects['RootNode'], rig


# --- look -------------------------------------------------------------------------------

def materials() -> dict:
    body = A.material('robot_body', '#41ced1', rough=0.28)
    bsdf = body.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Coat Weight'].default_value = 0.8  # the glossy, toy-like highlight (KHR_materials_clearcoat)
    bsdf.inputs['Coat Roughness'].default_value = 0.06
    glass = A.material('robot_glass', '#eef8fa', rough=0.03)
    gb = glass.node_tree.nodes['Principled BSDF']
    gb.inputs['Transmission Weight'].default_value = 1.0  # clear, as B2's tube
    gb.inputs['IOR'].default_value = 1.45
    visor = A.material('robot_visor', '#030405', rough=0.05)  # glossy black glass: it catches the key light
    vb = visor.node_tree.nodes['Principled BSDF']
    vb.inputs['Coat Weight'].default_value = 1.0
    vb.inputs['Coat Roughness'].default_value = 0.02
    return {'body': body,
            'joint': A.material('robot_joint', '#3b3e45', rough=0.4, metal=0.2),
            'panel': A.material('robot_panel', '#c4c7cd', rough=0.35),
            'visor': visor,
            'eye': A.material('robot_eye', '#9ffaff', rough=0.3, emission='#62f3ef'),
            'band': A.material('robot_band', '#ff8f6b', rough=0.3, emission='#ff6a3d'),
            'halo': A.material('robot_halo', '#ffffff', rough=0.3, emission='#ffffff'),
            'dark': A.material('robot_dark', '#15171b', rough=0.4),
            'glass': glass,
            'liquid': A.material('robot_liquid', '#23282e', rough=0.15),
            'pencil': A.material('robot_pencil', '#f0b43c', rough=0.5),
            'wood': A.material('robot_wood', '#e3c08e', rough=0.7),
            'laptop': A.material('robot_laptop', '#44474d', rough=0.35, metal=0.6),
            'keys': A.material('robot_keys', '#1d2024', rough=0.6),
            'paper': A.material('robot_paper', '#f6f4ee', rough=0.8)}


# --- modelling ---------------------------------------------------------------------------
# Every solid starts as a spherified, subdivided cube: all quads and no poles, so it subdivides cleanly.

def unit_sphere(cuts: int = 5) -> bmesh.types.BMesh:
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    for v in bm.verts:
        x, y, z = v.co
        v.co = Vector((x * math.sqrt(max(0, 1 - y * y / 2 - z * z / 2 + y * y * z * z / 3)),
                       y * math.sqrt(max(0, 1 - z * z / 2 - x * x / 2 + z * z * x * x / 3)),
                       z * math.sqrt(max(0, 1 - x * x / 2 - y * y / 2 + x * x * y * y / 3))))
    return bm


def frame(axis: Vector, side: Vector = Vector((1, 0, 0))) -> Matrix:
    """A rotation taking +Z to `axis`, keeping +X as near `side` as it can."""
    z = axis.normalized()
    x = (side - z * side.dot(z))
    if x.length < 1e-4:
        x = Vector((0, 1, 0)) - z * z.y
    x.normalize()
    return Matrix((x, z.cross(x), z)).transposed()


def blob(radii, center, e: float = 2.0, ez: float = 2.0, rot: Matrix = Matrix.Identity(3), cuts: int = 5,
         shape=None) -> bmesh.types.BMesh:
    """A superellipsoid: exponent `e` round the waist (2 round, higher boxier), `ez` top to bottom.
    `shape(p)` may reshape the local point before it is rotated and placed."""
    a, b, c = radii
    bm = unit_sphere(cuts)
    for v in bm.verts:
        d = v.co.normalized()
        f = ((abs(d.x / a) ** e + abs(d.y / b) ** e) ** (ez / e) + abs(d.z / c) ** ez) ** (1 / ez)
        p = d / f
        if shape:
            p = shape(p)
        v.co = center + rot @ p
    return bm


def capsule(a: Vector, b: Vector, ra: float, rb: float, cuts: int = 5, side=Vector((1, 0, 0))) -> bmesh.types.BMesh:
    """A capsule from `a` to `b`, radius `ra` at `a` tapering to `rb` at `b`."""
    bm = unit_sphere(cuts)  # odd cuts: there is an equator ring to split at
    m = frame(b - a, side)
    for v in bm.verts:
        v.co = (b + m @ (v.co * rb)) if v.co.z > 1e-6 else (a + m @ (v.co * ra))
    return bm


def disc(center: Vector, axis: Vector, r: float, depth: float, e: float = 2.0) -> bmesh.types.BMesh:
    """A puck with rounded rims, facing `axis`."""
    return blob((r, r, depth / 2), center, e=e, ez=5.0, rot=frame(axis))


def part(name: str, bm: bmesh.types.BMesh, mat, bone=None, levels: int = 1) -> bpy.types.Object:
    """Finish a part: smooth, subdivided once; `bone` is a bone name or a function of position giving
    {bone: weight}."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    me.materials.append(mat)
    if levels:
        apply_mod(ob, 'SUBSURF', levels=levels, render_levels=levels)
    for p in me.polygons:
        p.use_smooth = True
    if bone:
        weigh(ob, bone)
    return ob


def apply_mod(ob, kind: str, **settings) -> None:
    mod = ob.modifiers.new(kind.lower(), kind)
    for k, v in settings.items():
        setattr(mod, k, v)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_apply(modifier=mod.name)


def weigh(ob, bone) -> None:
    for v in ob.data.vertices:
        ws = {bone: 1.0} if isinstance(bone, str) else bone(ob.matrix_world @ v.co)
        for name, w in ws.items():
            if w > 0:
                g = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
                g.add([v.index], w, 'REPLACE')


def shell(name: str, target, center: Vector, normal: Vector, up: Vector, w: float, h: float, mat, thick: float,
          lift: float = 0.0, e: float = 4.0, bone=None, bevel: float = 0.0, n: int = 16) -> bpy.types.Object:
    """A plate hugging `target`'s surface: a rounded rectangle (superellipse exponent `e`, half-sizes `w` x `h`)
    shrink-wrapped onto it `lift` above, solidified outwards by `thick`, bevelled and subdivided."""
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=n, y_segments=n, size=1.0)
    for v in bm.verts:
        x, y = v.co.x, v.co.y
        r = max(abs(x), abs(y))
        norm = (abs(x) ** e + abs(y) ** e) ** (1 / e)
        k = r / norm if norm > 1e-9 else 0.0
        v.co = Vector((x * k * w, y * k * h, 0))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    zaxis = normal.normalized()
    xaxis = up.cross(zaxis).normalized()
    ob.matrix_world = Matrix.Translation(center + zaxis * 3.0) @ Matrix((xaxis, zaxis.cross(xaxis), zaxis)).transposed().to_4x4()
    bpy.context.view_layer.update()
    apply_mod(ob, 'SHRINKWRAP', target=target, wrap_method='PROJECT', use_negative_direction=True,
              use_positive_direction=False, use_project_z=True, offset=lift)
    apply_mod(ob, 'SOLIDIFY', thickness=thick, offset=1.0, use_rim=True, use_even_offset=True)
    if bevel:
        apply_mod(ob, 'BEVEL', width=bevel, segments=2, limit_method='ANGLE', angle_limit=math.radians(50))
    apply_mod(ob, 'SUBSURF', levels=1, render_levels=1)
    ob.data.materials.append(mat)
    for p in ob.data.polygons:
        p.use_smooth = True
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if bone:
        weigh(ob, bone)
    return ob


def ramp(lo: str, hi: str, z0: float, z1: float):
    """Weights blending from bone `lo` below z0 to `hi` above z1."""
    def f(p):
        t = min(1.0, max(0.0, (p.z - z0) / (z1 - z0)))
        t = t * t * (3 - 2 * t)
        return {lo: 1 - t, hi: t}
    return f


class Rig:
    def __init__(self, rig):
        self.rig = rig

    def at(self, bone: str) -> Vector:
        return self.rig.matrix_world @ self.rig.data.bones[bone].head_local


def ring(center: Vector, axis: Vector, r: float, depth: float) -> bmesh.types.BMesh:
    """A dark joint ring round a limb: a flat puck across `axis`."""
    return blob((r, r, depth / 2), center, e=2.0, ez=4.0, rot=frame(axis), cuts=3)


def build_body(m: dict, rig) -> tuple[bpy.types.Object, dict]:
    R = Rig(rig)
    parts = []
    hc, hr = HEAD['c'], HEAD['r']
    # head: a near-sphere, a touch wider than deep, flattened a little at the chin
    head = part('head', blob(hr, hc, e=HEAD['e'], ez=2.1, cuts=7,
                             shape=lambda p: Vector((p.x, p.y, p.z * (0.93 if p.z < 0 else 1.0)))), m['body'], 'Head')
    parts.append(head)
    # the face plate: a big, glossy dark rounded rectangle over most of the front
    plate = shell('visor', head, hc + Vector((0, 0, -0.04 * hr[2])), Vector((0, -1, 0)), Vector((0, 0, 1)),
                  0.84 * hr[0], 0.72 * hr[2], m['visor'], 0.05, lift=0.03, e=3.2, bone='Head', bevel=0.012, n=14)
    parts.append(plate)
    # ear discs: a light rim round a dark hub
    for s in (-1, 1):
        c = hc + Vector((s * (hr[0] - 0.02), 0.04, -0.06 * hr[2]))
        parts.append(part(f'ear{s:+d}', disc(c, Vector((s, 0, 0)), 0.34 * hr[0], 0.2 * hr[0], e=2.0), m['panel'], 'Head'))
        parts.append(part(f'hub{s:+d}', blob((0.21 * hr[0], 0.21 * hr[0], 0.06), c + Vector((s * 0.1, 0, 0)), ez=5.0,
                                             rot=frame(Vector((s, 0, 0))), cuts=3), m['joint'], 'Head'))
    # neck
    parts.append(part('neck', capsule(R.at('Neck') - Vector((0, 0, 0.1)), R.at('Head') + Vector((0, 0, 0.1)), 0.18, 0.2, cuts=3),
                      m['joint'], 'Neck'))
    # torso: tall and upright, rounded, a little narrower at the waist; a chest panel; a collar under the head
    tc, tr = TORSO['c'], TORSO['r']
    torso = part('torso', blob(tr, tc, e=2.8, ez=2.6, cuts=7,
                               shape=lambda p: Vector((p.x * (1 - 0.1 * max(0, -p.z / tr[2]) ** 2),
                                                       p.y * (1 - 0.06 * max(0, -p.z / tr[2]) ** 2), p.z))),
                 m['body'], ramp('Abdomen', 'Torso', 1.7, 2.15))
    parts.append(torso)
    parts.append(shell('chest', torso, tc + Vector((0, 0, 0.18)), Vector((0, -1, 0)), Vector((0, 0, 1)), 0.22, 0.2,
                       m['panel'], 0.035, e=4.0, bone='Torso', bevel=0.01, n=8))
    parts.append(part('collar', blob((0.3, 0.28, 0.07), tc + Vector((0, 0.02, tr[2] - 0.05)), ez=5.0, cuts=3), m['joint'], 'Torso'))
    # pelvis: dark, with ball sockets for the legs
    parts.append(part('pelvis', blob((0.5, 0.38, 0.22), R.at('Hips') + Vector((0, 0.04, 0.1)), e=2.6, ez=2.4), m['joint'], 'Hips'))
    for side in 'LR':
        s = 1 if side == 'L' else -1
        # arm: shoulder cap, slim upper arm, a dark elbow ring, forearm, dark cuff, a small hand with fingers
        sh, el, wr = R.at(f'UpperArm.{side}'), R.at(f'LowerArm.{side}'), R.at(f'Palm2.{side}')
        out = Vector((s, 0, 0))
        up_d, fore_d = (el - sh).normalized(), (wr - el).normalized()
        parts.append(part(f'shoulder{side}', blob((0.23, 0.23, 0.24), sh + out * 0.03 + Vector((0, 0, 0.02))), m['body'],
                          f'Shoulder.{side}'))
        parts.append(part(f'socket{side}', ring(sh - out * 0.12, out, 0.2, 0.11), m['joint'], f'Shoulder.{side}'))
        parts.append(part(f'upperarm{side}', capsule(sh + up_d * 0.18, el - up_d * 0.1, *LIMB['upper']), m['body'], f'UpperArm.{side}'))
        parts.append(part(f'elbow{side}', blob((0.15,) * 3, el, cuts=3), m['joint'], f'LowerArm.{side}'))
        parts.append(part(f'elbowring{side}', ring(el + fore_d * 0.13, fore_d, 0.2, 0.1), m['joint'], f'LowerArm.{side}'))
        cuff = el + (wr - el) * 0.72
        parts.append(part(f'forearm{side}', capsule(el + fore_d * 0.16, cuff, *LIMB['fore']), m['body'], f'LowerArm.{side}'))
        parts.append(part(f'wrist{side}', capsule(cuff, wr - fore_d * 0.02, 0.15, 0.12, cuts=3), m['joint'], f'LowerArm.{side}'))
        mid = R.at(f'Middle1.{side}')
        palm_c = wr + (mid - wr) * 0.5
        axis = (mid - wr).normalized()
        parts.append(part(f'palm{side}', blob((0.15, 0.17, 0.12), palm_c, e=2.6, ez=2.6, rot=frame(axis, out)),
                          m['joint'], f'Palm2.{side}'))
        # fingers: three and a thumb, each on its own bones, drawn in towards the middle finger (the rig fans them)
        base = R.at(f'Middle1.{side}')
        r = LIMB['finger']
        for f1, f2 in (('Index', 'Index2'), ('Middle1', 'Middle2'), ('Ring1', 'Ring2'), ('Thumb', 'Thumb2')):
            a, b = R.at(f'{f1}.{side}'), R.at(f'{f2}.{side}')
            pull = Vector() if f1 in ('Middle1', 'Thumb') else (base - a) * 0.6  # close together, as B2's
            a, b = a + pull, b + pull
            tip = b + (b - a) * 0.75
            parts.append(part(f'{f1}{side}', capsule(a - (b - a) * 0.3, b, r, r, cuts=3), m['joint'], f'{f1}.{side}'))
            parts.append(part(f'{f2}{side}', capsule(b, tip, r, r * 0.9, cuts=3), m['joint'], f'{f2}.{side}'))
        # leg: hip ball, slim thigh, a dark knee ring, shin flaring into a boot
        hip, knee = R.at(f'UpperLeg.{side}'), R.at(f'LowerLeg.{side}')
        foot = R.at(f'Foot.{side}')
        ankle = Vector((foot.x, foot.y - 0.04, 0.4))
        thigh_d, shin_d = (knee - hip).normalized(), (ankle - knee).normalized()
        parts.append(part(f'hip{side}', blob((0.2,) * 3, hip, cuts=3), m['joint'], f'UpperLeg.{side}'))
        parts.append(part(f'thigh{side}', capsule(hip + thigh_d * 0.08, knee - thigh_d * 0.1, *LIMB['thigh']),
                          m['body'], f'UpperLeg.{side}'))
        parts.append(part(f'knee{side}', blob((0.18,) * 3, knee, cuts=3), m['joint'], f'LowerLeg.{side}'))
        parts.append(part(f'kneering{side}', ring(knee + shin_d * 0.13, shin_d, 0.25, 0.11), m['joint'], f'LowerLeg.{side}'))
        parts.append(part(f'shin{side}', capsule(knee + shin_d * 0.17, ankle + Vector((0, 0, 0.08)), *LIMB['shin']),
                          m['body'], f'LowerLeg.{side}'))
        parts.append(part(f'ankle{side}', blob((0.17,) * 3, ankle, cuts=3), m['joint'], f'Foot.{side}'))
        boot_c = Vector((foot.x, foot.y - 0.14, 0.19))
        parts.append(part(f'boot{side}', blob((0.29, 0.44, 0.19), boot_c, e=2.4, ez=2.2,
                                              shape=lambda p: Vector((p.x, p.y, p.z if p.z > 0 else p.z * 0.6))),
                          m['body'], f'Foot.{side}'))
        parts.append(part(f'sole{side}', blob((0.29, 0.43, 0.05), Vector((boot_c.x, boot_c.y, 0.06)), e=2.4, ez=4.0, cuts=3),
                          m['joint'], f'Foot.{side}'))
    body = join(parts, 'robot')
    body['fleet'] = 'character'
    skin(body, rig)
    return body, {'head': head_width(body)}


def join(parts, name: str) -> bpy.types.Object:
    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    ob.name = ob.data.name = name
    return ob


def skin(ob, rig) -> None:
    world = ob.matrix_world.copy()
    ob.parent = rig
    ob.matrix_world = world
    mod = ob.modifiers.new('rig', 'ARMATURE')
    mod.object = rig


def head_width(body) -> float:
    """The helmet shell's width, without the ear discs (as B2's helmets were measured)."""
    g = body.vertex_groups['Head'].index
    shell_mat = [i for i, s in enumerate(body.material_slots) if s.material.name == 'robot_body']
    on_shell = {v for p in body.data.polygons if p.material_index in shell_mat for v in p.vertices}
    xs = [(body.matrix_world @ v.co).x for v in body.data.vertices
          if v.index in on_shell and any(e.group == g and e.weight > 0.5 for e in v.groups)]
    return max(xs) - min(xs)


def hang(ob: bpy.types.Object, rig: bpy.types.Object, bone: str) -> None:
    """Parent `ob` to `bone`, keeping where it was modelled."""
    bpy.context.view_layer.update()
    world = ob.matrix_world.copy()
    ob.parent, ob.parent_type, ob.parent_bone = rig, 'BONE', bone
    bpy.context.view_layer.update()
    ob.matrix_world = world


def face(m: dict, rig, body) -> None:
    """The two agent faces, as separate layers over the visor: glowing eyes, and a band."""
    visor = bpy.data.objects.new('visor_ref', body.data.copy())  # the plate alone, to wrap the faces onto
    bpy.context.scene.collection.objects.link(visor)
    bm = bmesh.new()
    bm.from_mesh(visor.data)
    vi = [i for i, s in enumerate(body.material_slots) if s.material == m['visor']][0]
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index != vi], context='FACES')
    bm.to_mesh(visor.data)
    bm.free()
    hc = HEAD['c']
    hr = HEAD['r']
    c = hc + Vector((0, 0, -0.04 * hr[2]))
    eyes = [shell(f'eye{s:+d}', visor, c + Vector((s * 0.27 * hr[0], 0, 0.02 * hr[2])), Vector((0, -1, 0)), Vector((0, 0, 1)),
                  0.12 * hr[0], 0.2 * hr[2], m['eye'], 0.02, lift=0.001, e=2.0, n=6) for s in (-1, 1)]  # tall ovals, as B2's
    eyes_ob = join(eyes, 'robot_eyes')
    band = shell('robot_band', visor, c, Vector((0, -1, 0)), Vector((0, 0, 1)), 0.62 * hr[0], 0.11 * hr[2], m['band'], 0.02,
                 lift=0.001, e=5.0, n=8)
    bpy.data.meshes.remove(visor.data)
    for ob in (eyes_ob, band):
        ob['fleet'] = 'dynamic'
        hang(ob, rig, 'Head')


def accessories(m: dict, rig) -> None:
    """One per host; each hangs from a bone and the runtime shows the host's."""
    hc, hr = HEAD['c'], HEAD['r']
    tc, tr = TORSO['c'], TORSO['r']
    top = hc.z + hr[2]
    k = hr[0] / 0.93  # the kit was sized on a helmet of radius 0.93
    made = {
        'backpack': ([part('pack', blob((0.44, 0.2, 0.42), tc + Vector((0, tr[1] + 0.12, 0.12)), e=3.0, ez=3.0), m['body']),
                      part('pack_vent', blob((0.28, 0.05, 0.06), tc + Vector((0, tr[1] + 0.3, 0.3)), e=3.0, ez=3.0), m['joint']),
                      part('pack_vent2', blob((0.28, 0.05, 0.06), tc + Vector((0, tr[1] + 0.3, 0.12)), e=3.0, ez=3.0), m['joint'])],
                     'Torso'),
        'antenna': ([part('rod', capsule(hc + Vector((0.42, 0, 0.72)) * k, hc + Vector((0.52, 0, 1.3)) * k, 0.04, 0.035), m['joint']),
                     part('bulb', blob((0.11 * k,) * 3, hc + Vector((0.53, 0, 1.36)) * k), m['body'])], 'Head'),
        'halo': ([part('ring', torus(hc + Vector((0, 0, hr[2] + 0.28 * k)), 0.58 * k, 0.045), m['halo'], levels=0)], 'Head'),
        'crest': ([part('fin', blob((0.08, 0.72 * k, 0.2 * k), Vector((0, hc.y + 0.12 * k, top - 0.08 * k)), e=2.0, ez=2.4), m['body'])],
                  'Head'),
    }
    for kind, (pieces, bone) in made.items():
        ob = join(pieces, f'acc_{kind}')
        ob['fleet'] = 'dynamic'
        ob['accessory'] = kind
        hang(ob, rig, bone)


def torus(center: Vector, r: float, t: float) -> bmesh.types.BMesh:
    bm = bmesh.new()
    n, k = 48, 10
    rings = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rings.append([bm.verts.new(center + Vector((math.cos(a) * (r + t * math.cos(b)), math.sin(a) * (r + t * math.cos(b)),
                                                    t * math.sin(b))))
                      for b in (2 * math.pi * j / k for j in range(k))])
    for i in range(n):
        for j in range(k):
            a, b = rings[i], rings[(i + 1) % n]
            bm.faces.new((a[j], b[j], b[(j + 1) % k], a[(j + 1) % k]))
    return bm


# --- seated arm actions -------------------------------------------------------------------

def pose(rig, base: dict, offsets: dict) -> Vector:
    """Set the seated pose plus arm `offsets`; return the right palm's position."""
    for pb in rig.pose.bones:
        pb.rotation_quaternion = base[pb.name] @ offsets.get(pb.name, Euler()).to_quaternion()
    bpy.context.view_layer.update()
    return rig.matrix_world @ rig.pose.bones['Palm2.R'].head


def props(m: dict, rig: bpy.types.Object, base: dict, found: dict, desk_z: float) -> list[str]:
    """What the seated loops work with, sized to B2 against the helmet: a pencil and a test tube in the right
    hand (each modelled in the pose that uses it, then hung from the hand bone), and a laptop and a sheet of
    paper on the desk under the typing and writing hands (fixed in the robot's frame)."""
    rig.animation_data.action = None
    hw = 2 * HEAD['r'][0]  # helmet width, in model units
    M = rig.matrix_world

    def hand(side='R'):
        palm = M @ rig.pose.bones[f'Palm2.{side}'].head
        return palm, (M @ rig.pose.bones[f'Middle1.{side}'].head - palm).normalized()

    def joined(objs, name):
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.object.join()
        ob = bpy.context.view_layer.objects.active
        ob.name = name
        return ob

    # pencil: 0.7 helmets long, gripped in the fingers, tipped onto the paper
    pose(rig, base, found['Write'])
    palm, along = hand()
    grip = palm + along * 0.2
    length, r = 0.7 * hw, 0.035 * hw
    tilt = (math.radians(-25), 0, 0)
    shaft = A.cylinder('prop_pencil', r, length, grip + Vector((0, 0, -0.45 * length)), m['pencil'], kind='dynamic',
                       segments=6, rot=tilt, tile=None)
    tip = A.cylinder('prop_pencil_tip', r, 0.12 * length, grip + Vector((0, 0.04, -0.55 * length)), m['wood'], kind='dynamic',
                     radius2=0.004, segments=6, rot=(tilt[0] + math.pi, 0, 0), tile=None)
    pencil = joined([shaft, tip], 'prop_pencil')
    hang(pencil, rig, 'Palm2.R')
    # test tube: 1.1 helmets tall, two-thirds full of a dark liquid, held upright beside the head
    pose(rig, base, found['Hold'])
    palm, along = hand()
    grip = palm + along * 0.18
    length, r = 0.95 * hw, 0.12 * hw
    bottom = grip - Vector((0, 0, 0.3 * length))
    liquid = A.cylinder('prop_flask_liquid', r * 0.74, 0.55 * length, bottom + Vector((0, 0, 0.03)), m['liquid'], kind='dynamic',
                        segments=16, tile=None)
    glass = A.cylinder('prop_flask', r, length, bottom, m['glass'], kind='dynamic', segments=16, bevel=0.03, tile=None)
    flask = joined([glass, liquid], 'prop_flask')
    hang(flask, rig, 'Palm2.R')
    # laptop and paper: on the desk, under the hands that use them
    pose(rig, base, found['Type'])
    c = (hand('L')[0] + hand('R')[0]) / 2
    w, d = 1.25 * hw * 0.62, 0.9 * hw * 0.62
    base_c = Vector((c.x, c.y - 0.12, desk_z))
    parts = [A.box('prop_laptop', (w, d, 0.05), base_c, m['laptop'], kind='dynamic', bevel=0.02, tile=None),
             A.box('prop_laptop_keys', (w * 0.86, d * 0.5, 0.012), base_c + Vector((0, 0.08, 0.05)), m['keys'], kind='dynamic', tile=None)]
    hinge = base_c + Vector((0, -d / 2, 0.03))
    lid = A.box('prop_laptop_lid', (w, 0.04, 0.8 * d), hinge, m['laptop'], kind='dynamic', bevel=0.02, rot=(math.radians(22), 0, 0), tile=None)  # open past upright, away from the hands
    parts.append(lid)
    laptop = joined(parts, 'prop_laptop')
    pose(rig, base, found['Write'])
    c = (hand('L')[0] + hand('R')[0]) / 2
    paper = A.box('prop_paper', (0.62 * hw, 0.85 * hw, 0.01), Vector((c.x, c.y - 0.05, desk_z)), m['paper'], kind='dynamic',
                  rot=(0, 0, math.radians(12)), tile=None)
    bpy.context.view_layer.update()  # new objects have no world matrix until the scene updates
    for ob in (laptop, paper):  # fixed in the robot's frame, not a bone's
        world = ob.matrix_world.copy()
        ob.parent = rig
        ob.matrix_world = world
    for ob in (pencil, flask, laptop, paper):
        ob['fleet'] = 'prop'
    pose(rig, base, {})
    return [ob.name for ob in (pencil, flask, laptop, paper)]


def pose_at_sit_end(rig) -> dict[str, Quaternion]:
    rig.animation_data.action = bpy.data.actions['Sitting']
    bpy.context.scene.frame_set(SIT_END)
    return {pb.name: pb.rotation_quaternion.copy() for pb in rig.pose.bones}


def seat_point(rig, body) -> Vector:
    """Where the seated robot rests on the chair: under its hips, at the underside of its thighs. Model units."""
    bpy.context.view_layer.update()
    groups = {body.vertex_groups[f'UpperLeg.{s}'].index for s in 'LR'}
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(dg)
    me = ev.to_mesh()
    thigh = [i for i, v in enumerate(body.data.vertices) if any(g.group in groups and g.weight > 0.5 for g in v.groups)]
    lowest = min((ev.matrix_world @ me.vertices[i].co).z for i in thigh)
    ev.to_mesh_clear()
    hips = rig.matrix_world @ rig.pose.bones['Hips'].head
    return Vector((hips.x, hips.y, lowest))


def reach(rig, side: str, base: dict, target: Vector, fingers: Vector | None, relaxed: bool = True) -> dict[str, Euler]:
    """Offsets (on top of the seated pose) for the upper and lower arm that bring the palm near `target`,
    with the fingers pointing along `fingers` (if given) and, when `relaxed`, the elbow hanging low and close to
    the body rather than flung out. Coordinate descent over six angles."""
    bones = [f'UpperArm.{side}', f'LowerArm.{side}']
    angles = [0.0] * 6  # upper x, y, z, lower x, y, z (degrees)

    def apply(a):
        for name, e in ((bones[0], Euler([math.radians(v) for v in a[:3]])),
                        (bones[1], Euler([math.radians(v) for v in a[3:]]))):
            rig.pose.bones[name].rotation_quaternion = base[name] @ e.to_quaternion()
        bpy.context.view_layer.update()
        palm = rig.matrix_world @ rig.pose.bones[f'Palm2.{side}'].head
        err = (palm - target).length
        if fingers:
            along = (rig.matrix_world @ rig.pose.bones[f'Middle1.{side}'].head - palm).normalized()
            err += 0.25 * (1 - along.dot(fingers.normalized()))
        sh = rig.matrix_world @ rig.pose.bones[f'UpperArm.{side}'].head
        el = rig.matrix_world @ rig.pose.bones[f'LowerArm.{side}'].head
        if relaxed:
            err += 0.5 * max(0.0, el.z - (sh.z - 0.45)) + 0.5 * max(0.0, abs(el.x) - abs(sh.x) - 0.3)
        else:  # a raised arm: the elbow out to the side and no higher than the shoulder, as B2 holds the tube
            err += 0.5 * max(0.0, el.z - sh.z) + 0.5 * max(0.0, abs(sh.x) + 0.3 - abs(el.x))
        return err

    best = apply(angles)
    step = 40.0
    while step > 1.0:
        improved = False
        for i in range(len(angles)):
            for d in (step, -step):
                trial = angles.copy()
                trial[i] = max(-150.0, min(150.0, trial[i] + d))
                err = apply(trial)
                if err < best:
                    best, angles, improved = err, trial, True
        if not improved:
            step /= 2
    err = (rig.matrix_world @ rig.pose.bones[f'Palm2.{side}'].head - target).length
    apply([0.0] * 6)
    return {bones[0]: Euler([math.radians(v) for v in angles[:3]]), bones[1]: Euler([math.radians(v) for v in angles[3:]]),
            'error': err}


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


def actions(rig, body, scale: float) -> tuple:
    base = pose_at_sit_end(rig)
    seat = seat_point(rig, body)
    u = 1 / scale  # metres to model units
    desk_z = seat.z + DESK_ABOVE_SEAT * u  # the desk top, seated at the workbench
    for pb in rig.pose.bones:
        pb.rotation_quaternion = base[pb.name]
    bpy.context.view_layer.update()
    M = rig.matrix_world
    torso, head = M @ rig.pose.bones['Torso'].head, M @ rig.pose.bones['Head'].head
    knee = M @ rig.pose.bones['LowerLeg.L'].head
    front = torso.y - TORSO['r'][1]  # the chest (the robot faces -Y)
    hand_z = desk_z + 0.14  # palms just over the keys or the paper
    down = Vector((0, -1, -0.35))  # fingers forward, tipped down onto the desk
    targets = {  # palm target and finger direction, per side, placed as in B2's sprites
        'Rest': {'L': (Vector((0.5, front - 0.35, hand_z)), down), 'R': (Vector((-0.5, front - 0.35, hand_z)), down)},
        'Type': {'L': (Vector((0.34, front - 0.6, hand_z)), down), 'R': (Vector((-0.34, front - 0.6, hand_z)), down)},
        'Write': {'L': (Vector((0.5, front - 0.5, hand_z)), down),
                  'R': (Vector((-0.12, front - 0.62, hand_z + 0.08)), Vector((0.4, -1, -0.6)))},
        'Hold': {'L': (knee + Vector((0, -0.05, 0.25)), down),  # the free hand on its knee; the tube up beside the head
                 'R': (Vector((head.x - 1.5, head.y - 0.45, head.z - 0.05)), Vector((1, -0.25, 0)))},
    }
    found, errors = {}, {}
    for name, sides in targets.items():
        found[name] = {}
        for side, (target, fingers) in sides.items():
            r = reach(rig, side, base, target, fingers, relaxed=not (name == 'Hold' and side == 'R'))
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
    runtime = {'seat_point': [round(v * scale, 4) for v in seat], 'reach_error_units': errors}
    return runtime, base, found, desk_z


def main() -> None:
    A.require_blender()
    A.reset()
    bpy.context.scene.render.fps = FPS
    root, rig = load()
    m = materials()
    body, dims = build_body(m, rig)
    face(m, rig, body)
    accessories(m, rig)
    scale = HEAD_M / dims['head']
    runtime, base, found, desk_z = actions(rig, body, scale)
    prop_names = props(m, rig, base, found, desk_z)
    rig.animation_data.action = None
    # back to rest: bones a clip doesn't key keep whatever pose they hold, and the exporter samples it into
    # every clip
    for pb in rig.pose.bones:
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    root.scale = (scale,) * 3
    info = {'kind': 'character', 'source': 'RobotExpressive.glb (CC0): armature and actions', 'scale': round(scale, 4),
            'actions': sorted(a.name for a in bpy.data.actions), 'props': prop_names,
            'faces': {'codex': 'robot_eyes', 'claude': 'robot_band'},
            'accessories': {k: f'acc_{k}' for k in ACCESSORIES},
            'arm_bones': list(ARM_BONES), 'warm_groups': [], 'seat_height': None, 'runtime': runtime,
            'triangles': sum(len(p.vertices) - 2 for o in bpy.data.objects if o.type == 'MESH' for p in o.data.polygons)}
    A.save_blend(SCENE, info)
    print('BUILD', SCENE, info)


if __name__ == '__main__':  # importable by art/scripts/bakeoff.py
    main()
