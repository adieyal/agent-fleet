"""Shared helpers for the Blender build, bake and export scripts.

Run inside Blender (`blender -b -P art/scripts/<step>.py -- <scene>`). Every object
carries a `fleet` custom property that the later steps and the runtime read:

- `baked`: static; lit only by the scene lightmap (UV map `Lightmap`).
- `dynamic`: exported and lit at runtime; still occludes light during the bake.

Lights may carry `warm` = a group name. Warm lights are left out of the base
lightmap and baked into their own additive layer, which the runtime scales by
that workarea's activity.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

ART = Path(__file__).resolve().parent.parent
REPO = ART.parent
SOURCES = ART / 'sources'
BUILD = ART / 'build'
WORLD = REPO / 'fleet' / 'web' / 'assets' / 'world'
MIN_BLENDER = (4, 5, 0)


def require_blender() -> None:
    if bpy.app.version < MIN_BLENDER:
        sys.exit(f'art: Blender {".".join(map(str, MIN_BLENDER))}+ required, got {bpy.app.version_string}; '
                 'run art/build.sh, which uses the pinned build from art/sources/tools')


def scene_arg() -> str:
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(argv) != 1:
        sys.exit('usage: blender -b -P <script> -- <scene>')
    return argv[0]


def paths(scene: str) -> dict[str, Path]:
    b = BUILD / scene
    return {'build': b, 'blend': b / f'{scene}.blend', 'lightmaps': b / 'lightmaps',
            'textures': b / 'textures', 'out': WORLD / scene}


def source(*parts: str) -> Path:
    p = SOURCES.joinpath(*parts)
    if not p.exists():
        sys.exit(f'art: missing source {p.relative_to(ART)}; run art/scripts/fetch_assets.py')
    return p


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    random.seed(0)


# --- colour and materials -----------------------------------------------------

def srgb(hex_: str) -> tuple[float, float, float, float]:
    """#rrggbb (display sRGB) to linear RGBA."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    h = hex_.lstrip('#')
    return (*(lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)), 1.0)


def material(name: str, color: str, rough: float = 0.6, metal: float = 0.0, texture: Path | None = None,
             emission: str | None = None) -> bpy.types.Material:
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = srgb(color)
    bsdf.inputs['Roughness'].default_value = rough
    bsdf.inputs['Metallic'].default_value = metal
    if texture:
        img = bpy.data.images.load(str(texture), check_existing=True)
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        uv = nt.nodes.new('ShaderNodeUVMap')
        uv.uv_map = 'UVMap'
        nt.links.new(uv.outputs['UV'], tex.inputs['Vector'])
        # glTF exports texture x factor as baseColorTexture + baseColorFactor
        mix = nt.nodes.new('ShaderNodeMix')
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.inputs['Factor'].default_value = 1.0
        nt.links.new(tex.outputs['Color'], mix.inputs['A'])
        mix.inputs['B'].default_value = srgb(color)
        nt.links.new(mix.outputs['Result'], bsdf.inputs['Base Color'])
    if emission:
        bsdf.inputs['Emission Color'].default_value = srgb(emission)
        bsdf.inputs['Emission Strength'].default_value = 1.0
    return m


# --- geometry -----------------------------------------------------------------

def _finish(name: str, bm: bmesh.types.BMesh, mat: bpy.types.Material, kind: str,
            loc=(0, 0, 0), rot=(0, 0, 0), tile: float | None = 1.0, parent=None) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = rot
    if parent:
        ob.parent = parent
    me.materials.append(mat)
    for p in me.polygons:
        p.use_smooth = True
    me.set_sharp_from_angle(angle=math.radians(35))
    ob['fleet'] = kind
    box_uv(ob, tile)
    return ob


AXES = {'+x': (1, 0, 0), '-x': (-1, 0, 0), '+y': (0, 1, 0), '-y': (0, -1, 0), '+z': (0, 0, 1), '-z': (0, 0, -1)}


def box(name, size, loc, mat, kind='baked', bevel=0.006, rot=(0, 0, 0), tile=1.0, parent=None, drop=()):
    """Axis-aligned box of `size` whose base centre is at `loc` (z = bottom).

    `drop` names sides that can never be seen (e.g. '+y' for the outside of the back wall);
    their faces are removed so they take no lightmap space.
    """
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((0, 0, size[2] / 2)), verts=bm.verts)
    if bevel:
        bmesh.ops.bevel(bm, geom=bm.edges[:] + bm.verts[:], offset=min(bevel, min(size) / 2.5),
                        segments=2, profile=0.5, affect='EDGES', clamp_overlap=True)
    if drop:
        dirs = [Vector(AXES[d]) for d in drop]
        bm.normal_update()
        gone = [f for f in bm.faces if any(f.normal.dot(d) > 0.999 for d in dirs)]
        bmesh.ops.delete(bm, geom=gone, context='FACES')
    return _finish(name, bm, mat, kind, loc, rot, tile, parent)


def cylinder(name, radius, depth, loc, mat, kind='baked', radius2=None, segments=24, bevel=0.0,
             rot=(0, 0, 0), tile=1.0, parent=None):
    """Cylinder (or cone) standing on `loc`."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius,
                          radius2=radius if radius2 is None else radius2, depth=depth)
    bmesh.ops.translate(bm, vec=Vector((0, 0, depth / 2)), verts=bm.verts)
    if bevel:
        rims = [e for e in bm.edges if not e.is_manifold or e.calc_face_angle(0) > 0.6]
        bmesh.ops.bevel(bm, geom=rims, offset=bevel, segments=2, profile=0.5, affect='EDGES', clamp_overlap=True)
    return _finish(name, bm, mat, kind, loc, rot, tile, parent)


def octahedron(name, radius, height, loc, mat, kind='dynamic'):
    bm = bmesh.new()
    ring = [bm.verts.new((radius * math.cos(a), radius * math.sin(a), 0))
            for a in (i * math.pi / 2 for i in range(4))]
    top, bottom = bm.verts.new((0, 0, height / 2)), bm.verts.new((0, 0, -height / 2))
    for i in range(4):
        a, b = ring[i], ring[(i + 1) % 4]
        bm.faces.new((a, b, top))
        bm.faces.new((b, a, bottom))
    ob = _finish(name, bm, mat, kind, loc, (0, 0, math.pi / 4), 1.0)
    for p in ob.data.polygons:
        p.use_smooth = False
    return ob


def box_uv(ob: bpy.types.Object, tile: float | None) -> None:
    """World-scale box mapping into UV map `UVMap`: one texture repeat per `tile` metres."""
    me = ob.data
    uv = me.uv_layers.get('UVMap') or me.uv_layers.new(name='UVMap')
    if tile is None:
        return
    # matrix_world is stale until the depsgraph updates, so compose it here
    mw = Matrix.Translation(ob.location) @ ob.rotation_euler.to_matrix().to_4x4()
    if ob.parent:
        mw = ob.parent.matrix_world @ mw
    rot = mw.to_3x3()
    for poly in me.polygons:
        n = rot @ poly.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        u, v = [(1, 2), (0, 2), (0, 1)][axis]
        for li in poly.loop_indices:
            co = mw @ me.vertices[me.loops[li].vertex_index].co
            uv.data[li].uv = (co[u] / tile, co[v] / tile)


def import_gltf(path: Path, part: str, name: str, rot=(0, 0, 0), scale=1.0, kind='dynamic') -> bpy.types.Object:
    """Import one mesh (`part`) from a glTF as a prototype named `name`.

    Poly Haven files often lay several variants side by side; only `part` is kept. `rot` and
    `scale` are applied to the mesh, and its origin moves to the bottom centre so copies can be
    set straight onto a surface.
    """
    before = {o.name for o in bpy.data.objects}
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o.name for o in bpy.data.objects if o.name not in before]
    if part not in new:
        sys.exit(f'art: {path.name} has no object {part!r}; it has {sorted(new)}')
    ob = bpy.data.objects[part]
    for n in new:
        if n != part:
            bpy.data.objects.remove(bpy.data.objects[n])
    ob.parent = None
    ob.location = (0, 0, 0)
    ob.rotation_euler = rot
    ob.scale = (scale,) * 3
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    co = [v.co for v in ob.data.vertices]
    base = Vector(((min(c.x for c in co) + max(c.x for c in co)) / 2,
                   (min(c.y for c in co) + max(c.y for c in co)) / 2, min(c.z for c in co)))
    ob.data.transform(Matrix.Translation(-base))
    ob.name = ob.data.name = name
    ob.location = (0, 0, -20)
    ob['fleet'] = kind
    return ob


def duplicate(ob: bpy.types.Object, name: str, loc, rot_z=0.0) -> bpy.types.Object:
    d = ob.copy()
    d.name = name
    bpy.context.scene.collection.objects.link(d)
    d.location = loc
    d.rotation_euler = (0, 0, rot_z)
    return d


# --- lights -------------------------------------------------------------------

def light(name, kind, loc, energy, color='#ffffff', rot=(0, 0, 0), warm: str | None = None, **props):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = srgb(color)[:3]
    for k, v in props.items():
        setattr(data, k, v)
    ob = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = rot
    if warm:
        ob['warm'] = warm
    return ob


def aim(ob: bpy.types.Object, target) -> None:
    d = Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


def world_hdri(path: Path, strength: float, rotation: float) -> None:
    w = bpy.data.worlds.new('World')
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    env = nt.nodes.new('ShaderNodeTexEnvironment')
    env.image = bpy.data.images.load(str(path))
    mapping = nt.nodes.new('ShaderNodeMapping')
    mapping.inputs['Rotation'].default_value = (0, 0, rotation)
    coord = nt.nodes.new('ShaderNodeTexCoord')
    nt.links.new(coord.outputs['Generated'], mapping.inputs['Vector'])
    nt.links.new(mapping.outputs['Vector'], env.inputs['Vector'])
    bg = nt.nodes['Background']
    bg.inputs['Strength'].default_value = strength
    nt.links.new(env.outputs['Color'], bg.inputs['Color'])
    w['hdri'] = path.name
    w['hdri_rotation'] = rotation


def ortho_camera(name, target, pitch_deg, yaw_deg, scale, distance=40.0) -> bpy.types.Object:
    """Orthographic camera looking down `pitch_deg`, yawed `yaw_deg` from square-on to +Y."""
    cam = bpy.data.cameras.new(name)
    cam.type = 'ORTHO'
    cam.ortho_scale = scale
    cam.clip_end = distance * 3
    ob = bpy.data.objects.new(name, cam)
    bpy.context.scene.collection.objects.link(ob)
    p, y = math.radians(pitch_deg), math.radians(yaw_deg)
    d = Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))
    ob.location = Vector(target) + d * distance
    aim(ob, target)
    bpy.context.scene.camera = ob
    return ob


# --- lightmap UVs ---------------------------------------------------------------

def baked_objects() -> list[bpy.types.Object]:
    return sorted((o for o in bpy.data.objects if o.get('fleet') == 'baked'), key=lambda o: o.name)


def surface_area(objs) -> float:
    total = 0.0
    for o in objs:
        s = o.matrix_world.to_scale()
        total += sum(p.area for p in o.data.polygons) * abs(s.x * s.y * s.z) ** (2 / 3)
    return total


def lightmap_uvs(texel: float, max_res: int = 4096, margin_px: int = 6) -> dict:
    """Unwrap every baked object into one shared `Lightmap` atlas at `texel` metres per texel.

    Blender's island packer is not deterministic (two runs give different layouts), so islands
    are unwrapped with Blender and packed by `_shelf_pack`.
    """
    objs = baked_objects()
    for o in objs:
        drop_floor_contact(o)
    for o in objs:
        me = o.data
        lm = me.uv_layers.get('Lightmap') or me.uv_layers.new(name='Lightmap')
        me.uv_layers.active = lm
    area = surface_area(objs)
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    # correct_aspect would squash islands by the aspect of some material's image; lightmaps are square
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.0, area_weight=0.0,
                             correct_aspect=False, scale_to_bounds=False)
    bpy.ops.uv.select_all(action='SELECT')
    bpy.ops.uv.average_islands_scale()
    bpy.ops.object.mode_set(mode='OBJECT')
    metres_per_uv = math.sqrt(area / sum(_uv_area(o) for o in objs))
    res = _shelf_pack(objs, metres_per_uv / texel, margin_px, max_res)
    for o in objs:
        o.data.uv_layers.active = o.data.uv_layers['UVMap']
        o.data.uv_layers['UVMap'].active_render = True
    uv_area = sum(_uv_area(o) for o in objs)
    return {'resolution': res, 'surface_m2': round(area, 1), 'uv_fill': round(uv_area, 3),
            'texel_m': round(math.sqrt(area / uv_area) / res, 4)}


def _shelf_pack(objs, px_per_uv: float, margin_px: int, max_res: int) -> int:
    """Pack every object's `Lightmap` islands into the smallest square (a multiple of 256 px) that holds
    them at `px_per_uv`: islands turned landscape, sorted by height, laid out in shelves. Returns the size."""
    from bpy_extras.bmesh_utils import bmesh_linked_uv_islands

    islands = []  # (height, width, order, bm, uv layer, faces, min u, min v, rotated)
    meshes = []
    for oi, o in enumerate(objs):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        uv = bm.loops.layers.uv['Lightmap']
        meshes.append((o, bm))
        for ii, faces in enumerate(bmesh_linked_uv_islands(bm, uv)):
            us = [lp[uv].uv.x for f in faces for lp in f.loops]
            vs = [lp[uv].uv.y for f in faces for lp in f.loops]
            w, h = (max(us) - min(us)) * px_per_uv, (max(vs) - min(vs)) * px_per_uv
            rot = h > w
            if rot:
                w, h = h, w
            islands.append((math.ceil(h), math.ceil(w), (oi, min(f.index for f in faces)), bm, uv, faces,
                            min(us), min(vs), rot))
    islands.sort(key=lambda i: (-i[0], -i[1], i[2]))

    def layout(size: int):
        x = y = margin_px
        shelf, spots = 0, []
        for h, w, *_ in islands:
            if x + w + margin_px > size:
                x, y, shelf = margin_px, y + shelf + margin_px, 0
            spots.append((x, y))
            x, shelf = x + w + margin_px, max(shelf, h)
        return spots if y + shelf + margin_px <= size else None

    area = sum((h + margin_px) * (w + margin_px) for h, w, *_ in islands)
    size = max(256, 256 * math.floor(math.sqrt(area) / 256))
    while (spots := layout(size)) is None:
        size += 256
        if size > max_res:
            sys.exit(f'art: lightmap islands need more than {max_res}px at this texel size')
    for (h, w, _, bm, uv, faces, u0, v0, rot), (x, y) in zip(islands, spots):
        for f in faces:
            for lp in f.loops:
                du, dv = (lp[uv].uv.x - u0) * px_per_uv, (lp[uv].uv.y - v0) * px_per_uv
                if rot:
                    du, dv = dv, w - du
                lp[uv].uv = ((x + du) / size, (y + dv) / size)
    for o, bm in meshes:
        bm.to_mesh(o.data)
        bm.free()
    return size


def drop_floor_contact(o: bpy.types.Object) -> None:
    """Remove downward faces at or below floor level: undersides resting on the floor are never seen."""
    bpy.context.view_layer.update()
    mw = o.matrix_world
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bm.normal_update()
    gone = [f for f in bm.faces
            if (mw.to_3x3() @ f.normal).normalized().z < -0.999 and max((mw @ v.co).z for v in f.verts) < 0.01]
    if gone:
        bmesh.ops.delete(bm, geom=gone, context='FACES')
        bm.to_mesh(o.data)
    bm.free()


def _uv_area(o) -> float:
    uv = o.data.uv_layers['Lightmap'].data
    total = 0.0
    for p in o.data.polygons:
        pts = [uv[li].uv for li in p.loop_indices]
        total += abs(sum(pts[i].x * pts[i - 1].y - pts[i - 1].x * pts[i].y for i in range(len(pts)))) / 2
    return total


def save_blend(scene: str, info: dict) -> None:
    p = paths(scene)
    p['build'].mkdir(parents=True, exist_ok=True)
    bpy.context.scene['fleet_scene'] = scene
    bpy.context.scene['fleet_build'] = json.dumps(info)
    bpy.ops.wm.save_as_mainfile(filepath=str(p['blend']), compress=True)
