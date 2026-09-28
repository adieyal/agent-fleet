"""Render 5 evenly spaced frames of each FBX (Workbench, 3/4 view) into <out>/<stem>_f<N>.png.

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/blender_contact.py -- <out> <fbx>...
"""

import sys
from pathlib import Path

import bpy
from mathutils import Vector

FRAMES = 5


def render(fbx: Path, out: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(fbx))
    scene = bpy.context.scene
    arm = next(o for o in scene.objects if o.type == "ARMATURE")
    start, end = (int(f) for f in arm.animation_data.action.frame_range)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = 320, 400
    scene.world = bpy.data.worlds.new("w")
    scene.world.color = (0.9, 0.9, 0.9)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 2.6
    for i in range(FRAMES):
        f = start + round(i * (end - start) / (FRAMES - 1))
        scene.frame_set(f)
        root = arm.matrix_world @ arm.pose.bones[0].head
        target = Vector((root.x, root.y, 0.9))
        cam.location = target + Vector((2.5, -2.5, 0.6))
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(out / f"{fbx.stem}_f{i}.png")
        bpy.ops.render.render(write_still=True)


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1:]
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    for p in args[1:]:
        render(Path(p), out)


main()
