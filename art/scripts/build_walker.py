"""A placeholder walking robot for the floor prototype, until the robot job's sprites land (docs/design/robot-sprites.md).

    blender -b --factory-startup -P art/scripts/build_walker.py -- ROBOT_GLB OUT_DIR

Renders RobotExpressive's Walking clip from the concept robot glb (renovate/robot), 8 frames in each of 8 headings,
with the floor kit's camera, studio and shadow catcher (build_kit.py), at 171.5 and 343 px/m: a grey shell and a
second pass with the body shell alone in white, for the runtime's host tint. Heading k faces 45k degrees
anticlockwise from +x, seen from above. Writes walk-<k>@<ppm>.png, walk-<k>-mask@<ppm>.png and walker.json.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402
import build_kit as K  # noqa: E402

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

HEIGHT = 1.2      # standing, metres: the seated B2 robots' head height over the bench, standing up
FRAMES = 8
SHOWN = ('robot', 'robot_eyes')


def main() -> None:
    argv = sys.argv[sys.argv.index('--') + 1:]
    glb, out = Path(argv[0]), Path(argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    A.require_blender()
    A.reset()
    K.studio()
    K.TIERS = (1, 2)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    rig = bpy.data.objects['RobotArmature']
    root = bpy.data.objects.get('RootNode') or rig
    for o in bpy.data.objects:
        if o.type == 'MESH' and o.name not in SHOWN:
            o.hide_render = True
    walk = bpy.data.actions['Walking_RobotArmature']
    rig.animation_data_create()
    rig.animation_data.action = walk
    f0, f1 = walk.frame_range
    s = bpy.context.scene
    s.frame_set(int(f0))
    body = bpy.data.objects['robot']
    dg = bpy.context.evaluated_depsgraph_get()
    zs = [(body.evaluated_get(dg).matrix_world @ v.co).z for v in body.evaluated_get(dg).data.vertices]
    k = HEIGHT / (max(zs) - min(zs))
    root.scale = [c * k for c in root.scale]
    bpy.context.view_layer.update()
    zs = [(body.evaluated_get(dg).matrix_world @ v.co).z for v in body.evaluated_get(dg).data.vertices]
    root.location.z -= min(zs)   # feet on the floor
    K.floor_catcher()
    base_rot = root.rotation_euler.z
    grey = A.material('walker_grey', '#c9ccd2', rough=0.45)
    white = A.material('walker_white', '#ffffff')
    black = A.material('walker_black', '#000000')
    for m in (white, black):
        b = m.node_tree.nodes['Principled BSDF']
        b.inputs['Emission Color'].default_value = (1, 1, 1, 1) if m is white else (0, 0, 0, 1)
        b.inputs['Emission Strength'].default_value = 1.0 if m is white else 0.0
        b.inputs['Base Color'].default_value = (0, 0, 0, 1)
    slots = [(o, i, sl.material) for o in bpy.data.objects if o.type == 'MESH' for i, sl in enumerate(o.material_slots)]

    def paint(mask: bool) -> None:
        for o, i, m in slots:
            if o.name.startswith('catcher'):
                continue
            if mask:
                o.material_slots[i].material = white if m and m.name.startswith('robot_body') else black
            else:
                o.material_slots[i].material = grey if m and m.name.startswith('robot_body') else m

    times = [f0 + (f1 - f0) * i / FRAMES for i in range(FRAMES)]
    info = {'height_m': HEIGHT, 'frames': FRAMES, 'clip_s': (f1 - f0) / s.render.fps, 'headings': {}}
    for h in range(8):
        # the robot faces -y in Blender at rest; turn it to face 45h degrees from +x
        root.rotation_euler.z = base_rot + math.radians(45 * h + 90)
        frames = [(lambda t=t: s.frame_set(int(t), subframe=t - int(t))) for t in times]
        entry = {}
        for mask in (False, True):
            paint(mask)
            s.cycles.samples = 8 if mask else 96
            for o in bpy.data.objects:
                if o.name.startswith('catcher'):
                    o.hide_render = mask
            tiers = K.render(f'walk-{h}' + ('-mask' if mask else ''), out, Vector((0, 0, 0)), [], 0.35, frames)
            entry['mask' if mask else 'tiers'] = tiers
        info['headings'][str(h)] = entry
        print('WALK', h, [t['size'] for t in entry['tiers']], flush=True)
    (out / 'walker.json').write_text(json.dumps(info, indent=1))


main()
