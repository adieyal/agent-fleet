"""Render a built scene from its camera with every light on, for side-by-side review
against the concept art. Not shipped.

    blender -b -P art/scripts/preview.py -- <scene>

Writes art/build/<scene>/preview.png (Cycles, OIDN, AgX).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402


def main() -> None:
    A.require_blender()
    p = A.paths(A.scene_arg())
    bpy.ops.wm.open_mainfile(filepath=str(p['blend']))
    s = bpy.context.scene
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'OPTIX'
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type == 'OPTIX'
    s.render.engine = 'CYCLES'
    s.cycles.device = 'GPU'
    s.cycles.samples = 256
    s.cycles.use_denoising = True
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = 1672, 941, 100
    s.render.film_transparent = False
    s.view_settings.view_transform = 'AgX'
    s.render.filepath = str(p['build'] / 'preview.png')
    bpy.ops.render.render(write_still=True)


main()
