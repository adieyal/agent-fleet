"""Import each FBX into Blender and report armature, bones, frames and root travel.

    ~/.local/bin/blender -b --factory-startup --python art/motion-test/blender_check.py -- <dir> [<out.json>]
"""

import json
import sys
from pathlib import Path

import bpy


def check(path: Path) -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.import_scene.fbx(filepath=str(path))
    except RuntimeError as e:
        return {"file": path.name, "ok": False, "error": str(e).strip()}
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        return {"file": path.name, "ok": False, "error": "no armature"}
    arm = arms[0]
    action = arm.animation_data.action if arm.animation_data else None
    if action is None:
        return {"file": path.name, "ok": False, "error": "no action"}
    start, end = (int(f) for f in action.frame_range)
    root = arm.pose.bones[0]
    scene = bpy.context.scene
    heads = []
    for f in (start, end):
        scene.frame_set(f)
        heads.append((arm.matrix_world @ root.head).copy())
    return {
        "file": path.name,
        "ok": True,
        "bones": len(arm.data.bones),
        "root_bone": root.name,
        "frames": [start, end],
        "fps": scene.render.fps,
        "meshes": sum(o.type == "MESH" for o in scene.objects),
        "root_travel_m": round((heads[1] - heads[0]).length, 3),
    }


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1:]
    results = [check(p) for p in sorted(Path(args[0]).rglob("*.fbx"))]
    for r in results:
        print("CHECK", json.dumps(r))
    if len(args) > 1:
        Path(args[1]).write_text(json.dumps(results, indent=2))


main()
