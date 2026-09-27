"""Build the robot parts library from the user's AI-generated GLBs (outside the repo).

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/robot_parts.py

Reads ~/.local/state/fleet/renovation/robot-parts/*.glb. Each part is decimated, rotated into the robot's
frame (facing -Y, Z up; limb parts as mounted on the robot's RIGHT side, -X), scaled to metres, and given
its origin at its joint pivot. Left-side parts are mirrored at assembly time. Writes
~/.cache/fleet-motion-test/robot_parts.blend with one object per part (named after the spec key).
"""

import math
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

SRC = Path.home() / '.local/state/fleet/renovation/robot-parts'
OUT = Path.home() / '.cache/fleet-motion-test/robot_parts.blend'

# key: (glb, euler deg XYZ, (size axis, metres), pivot as bbox fractions, faces, material kind)
# Kinds: shell = texture x host tint; white = texture as is; dark = joints; glove = texture darkened.
PARTS = {
    'helmet':      ('helmet large', (0, 0, 0), ('x', 0.42), (0.5, 0.5, 0.0), 9000, 'shell'),
    'visor':       ('visor', (0, 0, 0), ('x', 0.30), (0.5, 0.5, 0.5), 3000, 'dark'),
    'ear':         ('ear cap', (0, 0, -90), ('z', 0.12), (1.0, 0.5, 0.5), 2500, 'white'),
    'neck':        ('neck joint', (0, 0, 0), ('x', 0.075), (0.5, 0.5, 0.5), 1500, 'dark'),
    'chest':       ('chest', (0, 0, 0), ('x', 0.27), (0.5, 0.5, 0.0), 6000, 'white'),
    'chest_light': ('chest light', (0, 0, 0), ('x', 0.055), (0.5, 1.0, 0.5), 1500, 'glow'),
    'waist':       ('waist ring', (0, 0, 0), ('x', 0.17), (0.5, 0.5, 0.5), 2500, 'dark'),
    'pelvis':      ('pelvis', (0, 0, 0), ('x', 0.20), (0.5, 0.5, 0.6), 5000, 'white'),
    'shoulder':    ('shoulder joint', (0, 0, 90), ('z', 0.085), (0.5, 0.5, 0.5), 2000, 'dark'),
    'upper_arm':   ('upper arm', (0, 0, 0), ('x', 0.15), (0.85, 0.5, 0.5), 4000, 'shell'),
    'elbow':       ('elbow joint', (0, 0, 180), ('z', 0.065), (0.6, 0.5, 0.5), 2000, 'dark'),
    'forearm':     ('forearm', (0, 0, -90), ('x', 0.12), (1.0, 0.5, 0.5), 4000, 'shell'),
    'hand':        ('hand back side', (0, 0, -90), ('x', 0.095), (1.0, 0.5, 0.5), 4000, 'glove'),
    'hip':         ('socket joint 1', (0, -90, 0), ('x', 0.07), (0.5, 0.5, 0.5), 2000, 'dark'),
    'thigh':       ('thigh', (0, 0, 0), ('z', 0.16), (0.5, 0.5, 0.92), 4000, 'shell'),
    'knee':        ('ball joint', (0, 0, 0), ('z', 0.06), (0.5, 0.5, 0.5), 1500, 'dark'),
    'shin':        ('shin', (0, 0, 0), ('z', 0.14), (0.5, 0.5, 0.95), 4000, 'shell'),
    'ankle':       ('socket joint 2', (0, 0, 0), ('z', 0.05), (0.5, 0.5, 0.7), 1500, 'dark'),
    'foot':        ('foot', (0, 0, 0), ('y', 0.17), (0.5, 0.62, None), 4000, 'shell'),
}
ANKLE_H = 0.075  # sole to ankle pivot; shared with motion_rig.py
# the sheet's limb shells are chunkier than the generated parts: widen them across their length axis
GIRTH = {'upper_arm': 1.35, 'forearm': 1.55, 'thigh': 1.2, 'shin': 1.35, 'hand': 1.2}
UNUSED = ['helmet (helmet large matches the sheet\'s taller dome)',
          'hand palm side (a second complete hand, not a half; hand back side is used for both hands)']


def import_part(glb: str) -> bpy.types.Object:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(SRC / f'{glb}.glb'))
    new = [o for o in bpy.data.objects if o not in before]
    mesh = next(o for o in new if o.type == 'MESH')
    mw = mesh.matrix_world.copy()
    mesh.parent = None
    mesh.matrix_world = mw
    for o in new:
        if o is not mesh:
            bpy.data.objects.remove(o)
    return mesh


def material(ob, kind: str) -> None:
    src = ob.data.materials[0]
    tex = next(n.image for n in src.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    m = bpy.data.materials.new(f'{ob.name}_{kind}')
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes['Principled BSDF']
    img = nt.nodes.new('ShaderNodeTexImage')
    img.image = tex
    col = img.outputs['Color']
    if kind in ('shell', 'glove'):
        mul = nt.nodes.new('ShaderNodeMix')
        mul.data_type, mul.blend_type = 'RGBA', 'MULTIPLY'
        mul.inputs['Factor'].default_value = 1.0
        mul.name = 'host_tint' if kind == 'shell' else 'glove'
        # the RGBA sockets: inputs 6/7 are colour A/B, output 2 is the colour result
        mul.inputs[7].default_value = (1, 1, 1, 1) if kind == 'shell' else (0.06, 0.06, 0.065, 1)
        nt.links.new(col, mul.inputs[6])
        col = mul.outputs[2]
    nt.links.new(col, bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = 0.35 if kind != 'dark' else 0.3
    if kind == 'glow':
        nt.links.new(img.outputs['Color'], bsdf.inputs['Emission Color'])
        bsdf.inputs['Emission Strength'].default_value = 2.0
    ob.data.materials.clear()
    ob.data.materials.append(m)


def build(key: str, spec) -> bpy.types.Object:
    glb, rot, (axis, size), pivot, faces, kind = spec
    ob = import_part(glb)
    ob.name = ob.data.name = key
    dec = ob.modifiers.new('dec', 'DECIMATE')
    dec.ratio = min(1.0, faces / len(ob.data.polygons))
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_apply(modifier='dec')
    # bake import transform + canonical rotation into the mesh
    ob.data.transform(Euler([math.radians(a) for a in rot]).to_matrix().to_4x4() @ ob.matrix_world)
    ob.matrix_world = Matrix()
    co = [v.co for v in ob.data.vertices]
    lo = Vector([min(c[i] for c in co) for i in range(3)])
    hi = Vector([max(c[i] for c in co) for i in range(3)])
    dims = hi - lo
    s = size / dims['xyz'.index(axis)] if axis != 'max' else size / max(dims)
    fz = pivot[2] if pivot[2] is not None else ANKLE_H / (dims.z * s)  # foot: sole sits ANKLE_H below pivot
    piv = Vector([lo[i] + dims[i] * f for i, f in enumerate((pivot[0], pivot[1], fz))])
    ob.data.transform(Matrix.Scale(s, 4) @ Matrix.Translation(-piv))
    g = GIRTH.get(key, 1.0)
    if axis != 'max':
        ob.data.transform(Matrix.Diagonal([1.0 if a == axis else g for a in 'xyz']).to_4x4())
    ob.data.update()
    for p in ob.data.polygons:
        p.use_smooth = True
    material(ob, kind)
    ob['kind'] = kind
    ob['dims_m'] = [d * s * (1.0 if a == axis else g) for d, a in zip(dims, 'xyz')]
    return ob


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for key, spec in PARTS.items():
        ob = build(key, spec)
        print('PART', key, len(ob.data.polygons), 'faces', [round(d, 3) for d in ob['dims_m']])
    for img in bpy.data.images:
        img.pack()
    for m in [m for m in bpy.data.materials if m.users == 0]:
        bpy.data.materials.remove(m)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT), compress=True)
    print('SAVED', OUT)


main()
