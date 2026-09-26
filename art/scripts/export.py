"""Export a built and baked scene for three.js.

    blender -b -P art/scripts/export.py -- <scene>

Writes fleet/web/assets/world/<scene>/:
- <scene>.glb: geometry and materials, textures as WebP (EXT_texture_webp). Baked meshes carry
  their lightmap UVs in TEXCOORD_1; every node's `fleet` / `warm` custom properties are in extras.
- lightmap-<layer>.webp: 8-bit sRGB-encoded irradiance, value = texel (decoded to linear) x scale.
- manifest.json: how to apply the lightmaps, the warm groups, and which nodes are dynamic.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

TEXTURE_QUALITY = 85
LIGHTMAP_QUALITY = 92
CLIP_PERCENTILE = 99.9  # the rare brighter texels (lamp hot spots) clip to white


def encode_lightmap(exr: Path, dest: Path) -> dict:
    img = bpy.data.images.load(str(exr))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    rgb = np.clip(px.reshape(-1, 4)[:, :3], 0.0, None)
    peak = rgb.max(axis=1)
    lit = peak[peak > 0]
    scale = float(np.percentile(lit, CLIP_PERCENTILE)) if lit.size else 0.0
    if scale <= 0:
        sys.exit(f'export: {exr.name} is black; did its lights bake?')
    v = np.clip(rgb / scale, 0.0, 1.0)
    v = np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)
    out = np.ones((w * h, 4), dtype=np.float32)
    out[:, :3] = v
    enc = bpy.data.images.new(dest.stem, w, h, alpha=False)
    enc.colorspace_settings.name = 'Non-Color'  # values are already sRGB-encoded; write them as they are
    enc.pixels.foreach_set(out.ravel())
    enc.filepath_raw = str(dest)
    enc.file_format = 'WEBP'
    bpy.context.scene.render.image_settings.quality = LIGHTMAP_QUALITY
    enc.save(quality=LIGHTMAP_QUALITY)
    bpy.data.images.remove(enc)
    bpy.data.images.remove(img)
    return {'file': dest.name, 'size': w, 'scale': round(scale, 5)}


def strip_bake_nodes() -> None:
    for mat in bpy.data.materials:
        if mat.node_tree and 'fleet_bake' in mat.node_tree.nodes:
            mat.node_tree.nodes.remove(mat.node_tree.nodes['fleet_bake'])


def export_glb(dest: Path) -> None:
    bpy.ops.object.select_all(action='DESELECT')
    for o in bpy.data.objects:
        o.select_set(o.type in {'MESH', 'CAMERA', 'EMPTY', 'ARMATURE'})
    # export_apply leaves armature modifiers alone, so skinned characters stay skinned
    bpy.ops.export_scene.gltf(
        filepath=str(dest), export_format='GLB', use_selection=True,
        export_image_format='WEBP', export_image_quality=TEXTURE_QUALITY, export_image_add_webp=False,
        export_extras=True, export_texcoords=True, export_normals=True, export_apply=True,
        export_cameras=True, export_lights=False, export_yup=True, export_materials='EXPORT',
        export_animations=True, export_animation_mode='ACTIONS', export_force_sampling=True)


def export_environment(dest: Path) -> dict:
    """A small copy of the world HDRI for the runtime environment map (PMREM needs little resolution)."""
    world = bpy.context.scene.world
    src = next(n.image for n in world.node_tree.nodes if n.type == 'TEX_ENVIRONMENT')
    img = src.copy()
    img.scale(512, 256)
    img.filepath_raw = str(dest)
    img.file_format = 'HDR'
    img.save()
    return {'file': dest.name, 'hdri': world['hdri'], 'rotation_deg': round(math.degrees(world['hdri_rotation']), 1),
            'strength': world.node_tree.nodes['Background'].inputs['Strength'].default_value}


def check_glb(path: Path) -> dict:
    """Read back the glb's JSON chunk: baked meshes must carry TEXCOORD_1, images must be WebP."""
    data = path.read_bytes()
    n = int.from_bytes(data[12:16], 'little')
    gltf = json.loads(data[20:20 + n])
    bad_uv = [m['name'] for m in gltf['meshes'] for prim in m['primitives']
              if any((gltf['nodes'][i].get('extras') or {}).get('fleet') == 'baked'
                     for i, node in enumerate(gltf['nodes']) if node.get('mesh') == gltf['meshes'].index(m))
              and 'TEXCOORD_1' not in prim['attributes']]
    if bad_uv:
        sys.exit(f'export: baked meshes without lightmap UVs: {bad_uv[:5]}')
    mimes = sorted({img.get('mimeType') for img in gltf.get('images', [])})
    if mimes and mimes != ['image/webp']:
        sys.exit(f'export: non-WebP images in glb: {mimes}')
    return {'nodes': len(gltf['nodes']), 'meshes': len(gltf['meshes']), 'images': len(gltf.get('images', [])),
            'extensions': gltf.get('extensionsUsed', [])}


def main() -> None:
    A.require_blender()
    scene_name = A.scene_arg()
    p = A.paths(scene_name)
    bpy.ops.wm.open_mainfile(filepath=str(p['blend']))
    info = json.loads(bpy.context.scene['fleet_build'])
    bake = json.loads((p['lightmaps'] / 'bake.json').read_text())
    out = p['out']
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*'):
        old.unlink()
    layers = {}
    for layer in bake['layers']:
        layers[layer] = encode_lightmap(p['lightmaps'] / f'{layer}.exr', out / f'lightmap-{layer}.webp')
    strip_bake_nodes()
    glb = out / f'{scene_name}.glb'
    export_glb(glb)
    summary = check_glb(glb)
    objs = sorted(bpy.data.objects, key=lambda o: o.name)
    cameras = [o.name for o in objs if o.type == 'CAMERA']
    manifest = {
        'scene': scene_name,
        'glb': glb.name,
        'lightmap': {
            'uv_channel': 1,
            'encoding': 'srgb8',
            'apply': 'irradiance = decode_srgb(texel) * scale; colour = albedo * irradiance. '
                     'With MeshBasicMaterial set lightMap.channel = 1, lightMap.colorSpace = SRGBColorSpace '
                     'and lightMapIntensity = scale * PI (the shader divides by PI).',
            'texel_m': info['texel_m'],
            'layers': layers,
        } if layers else None,
        'warm_groups': {g: sorted(o.name for o in objs if o.type == 'MESH' and o.get('warm') == g)
                        for g in info['warm_groups']},
        'dynamic': sorted(o.name for o in objs if o.get('fleet') == 'dynamic'),
        'anchors': sorted(o.name for o in objs if o.get('fleet') == 'anchor'),
        'actions': sorted(a.name for a in bpy.data.actions),
        'seat_height': info['seat_height'],
        'camera': cameras[0] if cameras else None,
        'environment': export_environment(out / 'environment.hdr') if bpy.context.scene.world else None,
        'built_with': {'blender': bpy.app.version_string, 'bake_device': bake.get('device'),
                       'samples': bake.get('samples')},
        'files': {f.name: {'bytes': f.stat().st_size, 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
                  for f in sorted(out.glob('*'))},
        'glb_summary': summary,
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    total = sum(f.stat().st_size for f in out.glob('*'))
    print(f'EXPORT {scene_name}: {total / 1e6:.1f} MB in {out}')


main()
