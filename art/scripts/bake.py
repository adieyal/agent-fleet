"""Bake a built scene's lightmaps with Cycles on the GPU and denoise them with OpenImageDenoise.

    blender -b -P art/scripts/bake.py -- <scene>

Writes art/build/<scene>/lightmaps/<layer>.exr (scene-linear irradiance, multiply by albedo):
`base` holds sky, sun and fill light; one layer per warm group holds only that group's lamps.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402

SAMPLES = {'base': 512, 'warm': 256}
WARM_SCALE = 0.5  # warm layers bake at half the base resolution: their light is soft and local


def gpu() -> str:
    prefs = bpy.context.preferences.addons['cycles'].preferences
    for kind in ('OPTIX', 'CUDA'):
        prefs.compute_device_type = kind
        prefs.get_devices()
        devices = [d for d in prefs.devices if d.type == kind]
        if devices:
            for d in prefs.devices:
                d.use = d.type == kind
            return f"{kind}: {', '.join(d.name for d in devices)}"
    sys.exit('bake: no OptiX or CUDA device; baking needs the GPU')


def setup(scene: bpy.types.Scene) -> None:
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'GPU'
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.use_denoising = False  # bakes are denoised afterwards, see denoise()
    scene.render.bake.margin = 8
    scene.render.bake.margin_type = 'EXTEND'
    scene.render.bake.use_clear = True
    scene.render.bake.target = 'IMAGE_TEXTURES'


def proxy(objs) -> bpy.types.Object:
    """Join copies of the baked objects into one mesh and hide the originals.

    Cycles bakes each selected object as its own pass over the whole image, so ~1000 small
    objects are CPU-bound; one joined mesh bakes in a single GPU pass. The shared `Lightmap`
    UVs carry over, so the result maps back onto the separate exported objects.
    """
    bpy.ops.object.select_all(action='DESELECT')
    copies = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        bpy.context.scene.collection.objects.link(c)
        c.select_set(True)
        copies.append(c)
        o.hide_render = True
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = 'fleet_bake_proxy'
    joined.data.uv_layers.active = joined.data.uv_layers['Lightmap']
    return joined


def attach(image: bpy.types.Image, objs) -> None:
    """Make `image` the active bake target in every material of the baked objects."""
    for mat in {s.material for o in objs for s in o.material_slots if s.material}:
        nt = mat.node_tree
        node = nt.nodes.get('fleet_bake') or nt.nodes.new('ShaderNodeTexImage')
        node.name = 'fleet_bake'
        node.image = image
        node.interpolation = 'Linear'
        nt.nodes.active = node


def emissive_off() -> None:
    """Bulbs, buttons and the lantern are driven at runtime; they must not light the bake,
    and bulbs must not shadow the lamp lights that sit just behind them."""
    for o in bpy.data.objects:
        if o.type == 'MESH' and 'warm' in o:
            o.hide_render = True
    for mat in bpy.data.materials:
        if mat.node_tree and 'Principled BSDF' in mat.node_tree.nodes:
            mat.node_tree.nodes['Principled BSDF'].inputs['Emission Strength'].default_value = 0.0


def lights_for(layer: str) -> None:
    scene = bpy.context.scene
    for o in bpy.data.objects:
        if o.type == 'LIGHT':
            o.hide_render = o.get('warm') != (None if layer == 'base' else layer)
    bg = scene.world.node_tree.nodes['Background']
    bg.inputs['Strength'].default_value = scene.world.get('fleet_strength', 1.0) if layer == 'base' else 0.0


def bake_layer(layer: str, res: int, objs, out: Path) -> Path:
    scene = bpy.context.scene
    scene.cycles.samples = SAMPLES['base' if layer == 'base' else 'warm']
    image = bpy.data.images.new(f'lm_{layer}', res, res, alpha=False, float_buffer=True)
    image.colorspace_settings.name = 'Linear Rec.709'
    attach(image, objs)
    lights_for(layer)
    t = time.time()
    bpy.ops.object.bake(type='DIFFUSE', pass_filter={'DIRECT', 'INDIRECT'})
    raw = out / f'{layer}.raw.exr'
    image.filepath_raw = str(raw)
    image.file_format = 'OPEN_EXR'
    image.save()
    print(f'bake {layer}: {res}px {scene.cycles.samples}spp {time.time() - t:.0f}s')
    return raw


def denoise(raw: Path, dest: Path) -> None:
    """Run OIDN over a baked image through the compositor of a scratch scene."""
    img = bpy.data.images.load(str(raw))
    w, h = img.size
    s = bpy.data.scenes.new('denoise')
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = w, h, 100
    s.render.engine = 'BLENDER_WORKBENCH'
    s.camera = bpy.data.objects.new('denoise_cam', bpy.data.cameras.new('denoise_cam'))
    s.collection.objects.link(s.camera)
    s.use_nodes = True
    nt = s.node_tree
    nt.nodes.clear()
    src = nt.nodes.new('CompositorNodeImage')
    src.image = img
    dn = nt.nodes.new('CompositorNodeDenoise')
    dn.use_hdr = True
    dn.prefilter = 'ACCURATE'
    comp = nt.nodes.new('CompositorNodeComposite')
    nt.links.new(src.outputs['Image'], dn.inputs['Image'])
    nt.links.new(dn.outputs['Image'], comp.inputs['Image'])
    s.render.image_settings.file_format = 'OPEN_EXR'
    s.render.image_settings.color_depth = '32'
    s.render.image_settings.exr_codec = 'ZIP'
    s.render.filepath = str(dest)
    bpy.ops.render.render(write_still=True, scene=s.name)
    bpy.data.scenes.remove(s)
    bpy.data.images.remove(img)
    raw.unlink()


def main() -> None:
    A.require_blender()
    scene_name = A.scene_arg()
    p = A.paths(scene_name)
    bpy.ops.wm.open_mainfile(filepath=str(p['blend']))
    scene = bpy.context.scene
    info = json.loads(scene['fleet_build'])
    print('bake device', gpu())
    setup(scene)
    scene.world['fleet_strength'] = scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value
    emissive_off()
    objs = [proxy(A.baked_objects())]
    p['lightmaps'].mkdir(parents=True, exist_ok=True)
    layers = ['base'] + info['warm_groups']
    for layer in layers:
        res = info['resolution'] if layer == 'base' else int(info['resolution'] * WARM_SCALE)
        raw = bake_layer(layer, res, objs, p['lightmaps'])
        denoise(raw, p['lightmaps'] / f'{layer}.exr')
    (p['lightmaps'] / 'bake.json').write_text(json.dumps(
        {'layers': layers, 'samples': SAMPLES, 'device': gpu(), 'blender': bpy.app.version_string}, indent=2))


main()
