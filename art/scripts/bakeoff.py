"""Bake-off variants for the l2 bench, a planted planter and the seated robot.

    blender -b -P art/scripts/bakeoff.py -- A     # pre-lit 3D: unlit glTFs with a baked studio look
    blender -b -P art/scripts/bakeoff.py -- B1    # Blender sprites: transparent PNGs at l2's angle

Both variants share one studio: a soft key from the upper left (as in l2), a dim neutral fill from the
white_studio_06 HDRI, and contact shadows. Neither needs real-time lighting to display.

A (art/bakeoff/A): per object a .glb whose colour texture already holds albedo x studio light x AO, drawn
  unlit, plus a separate floor decal holding its contact shadow. The robot keeps its rig and clips; its
  baked texture is shading only (in the seated typing pose), so the page can still tint the body per host.
B1 (art/bakeoff/B1): per object a transparent PNG rendered in Cycles with the l2 camera (orthographic,
  pitch 44.5 deg, yaw 21.25 deg) at 1x, 2x and 4x the prototype's home density (171.5 px/m at 1x), contact
  shadows included via a shadow catcher. Robot poses are 8-frame loops (sheets, one row), rendered seated
  at their desks with the bench as a holdout, so the desk cuts them exactly as in the scene.

Needs art/build/robot/robot.blend (run art/build.sh robot first) and the fetched sources.
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artlib as A  # noqa: E402
import bake as B  # noqa: E402
import build_workbench as W  # noqa: E402

import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

OUT = A.ART / 'bakeoff'
TMP = A.BUILD / 'bakeoff'
VIEW = dict(pitch=44.5, yaw=21.25)  # the prototype's l2 camera
PX_PER_M = 941 / 5.486              # its home density: 941 px over 5.486 m
MULTS = (1, 2, 4)
EXPOSURE = 0.0
# pose: (arm clip, desk, prop, host colour). A sprite's colours are fixed, so each pose is rendered in the
# host colour its robot has in l2; per-host sets would multiply the sprites by the number of hosts.
POSES = {'type': ('Type', 'desk1', None, '#27b3b8'), 'write': ('Write', 'desk2', 'prop_pencil', '#2e62dc'),
         'hold': ('Hold', 'desk3', 'prop_flask', '#7a8a32')}
FRAMES = 8
PLANT_AT = (0.0, 0.0)


# --- shared studio ------------------------------------------------------------------------

def studio(centre: Vector) -> None:
    """Soft key from the upper left of the l2 view, a dim neutral fill, Cycles on the GPU."""
    A.world_hdri(A.source('polyhaven', 'white_studio_06', 'white_studio_06_1k.hdr'), strength=0.55,
                 rotation=math.radians(120))
    right, up, back = axes()
    key = A.light('key', 'AREA', centre - right * 5 + Vector((0, 0, 7)) + Vector((back.x, back.y, 0)) * 3,
                  1400, '#fff6ec', shape='DISK', size=4.0)
    A.aim(key, centre)
    s = bpy.context.scene
    B.gpu()
    B.setup(s)
    s.view_settings.view_transform = 'Standard'
    s.view_settings.look = 'None'
    s.view_settings.exposure = EXPOSURE


def axes():
    p, y = math.radians(VIEW['pitch']), math.radians(VIEW['yaw'])
    back = Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))  # target -> camera
    fwd = -back
    right = fwd.cross(Vector((0, 0, 1))).normalized()
    up = right.cross(fwd).normalized()
    return right, up, back


# --- the three objects ------------------------------------------------------------------------

def build_bench() -> list:
    m = W.materials()
    props = W.props()
    W.bench(m, random.Random(2), props)
    for proto in props.values():
        bpy.data.objects.remove(proto)
    for o in [o for o in bpy.data.objects if o.type == 'LIGHT']:
        bpy.data.objects.remove(o)
    return [o for o in bpy.data.objects if o.type == 'MESH']


def build_plant() -> list:
    m = W.materials()
    tall = A.import_gltf(A.source('polyhaven', 'anthurium_botany_01', 'anthurium_botany_01_1k.gltf'),
                         'anthurium_botany_01_a', 'plant', scale=0.75)
    pot = A.box('planter', (0.42, 0.42, 0.5), (*PLANT_AT, 0), m['pot'], bevel=0.02)
    tall.location = (*PLANT_AT, 0.48)
    return [pot, tall]


def load_robot():
    blend = A.BUILD / 'robot' / 'robot.blend'
    if not blend.exists():
        sys.exit('bakeoff: run art/build.sh robot first')
    with bpy.data.libraries.load(str(blend), link=False) as (src, dst):
        dst.objects = list(src.objects)
        dst.actions = list(src.actions)
    for o in dst.objects:
        bpy.context.scene.collection.objects.link(o)
    manifest = json.loads((A.WORLD / 'robot' / 'manifest.json').read_text())
    return bpy.data.objects['RootNode'], bpy.data.objects['RobotArmature'], Vector(manifest['runtime']['seat_point'])


def pose_robot(rig, arm: str, frame: float) -> None:
    """Body held at the end of Sitting, arms from `arm` at `frame`."""
    ad = rig.animation_data or rig.animation_data_create()
    ad.action = None
    for t in list(ad.nla_tracks):
        ad.nla_tracks.remove(t)
    sit = ad.nla_tracks.new().strips.new('sit', 1, bpy.data.actions['Sitting'])
    sit.action_frame_start = sit.action_frame_end = 10
    sit.frame_end = 1000
    arms = ad.nla_tracks.new().strips.new('arms', 1, bpy.data.actions[arm])
    arms.repeat = 20
    bpy.context.scene.frame_set(int(frame))
    bpy.context.view_layer.update()


def show_props(prop: str | None) -> None:
    for name in ('prop_pencil', 'prop_flask'):
        o = bpy.data.objects[name]
        o.hide_render = o.hide_viewport = name != prop


def seat_anchor(desk: str) -> Vector:
    x0 = W.BENCH_X0 + (int(desk[-1]) - 1) * W.DESK_W
    return Vector((x0 + W.DESK_W / 2 - 0.25, W.BENCH_Y + W.DESK_D / 2 + 0.34 - 0.2, W.SEAT_H))


# --- A: pre-lit 3D --------------------------------------------------------------------------

def floor_plane(name, lo: Vector, hi: Vector) -> bpy.types.Object:
    """A horizontal plane over [lo, hi] at the floor with UVs 0..1, for the contact-shadow decal."""
    bm = bmesh.new()
    vs = [bm.verts.new((x, y, 0.002)) for x, y in ((lo.x, lo.y), (hi.x, lo.y), (hi.x, hi.y), (lo.x, hi.y))]
    face = bm.faces.new(vs)
    uv = bm.loops.layers.uv.new('UVMap')
    for lp, co in zip(face.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        lp[uv].uv = co
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def bake_image(name, res, objs, pass_filter, multi=False) -> np.ndarray:
    """Bake diffuse light (with `pass_filter`) of `objs` into a float image; denoised; returns RGB."""
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=True)
    img.colorspace_settings.name = 'Linear Rec.709'
    B.attach(img, objs)
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.context.scene.cycles.samples = 256
    bpy.ops.object.bake(type='DIFFUSE', pass_filter=pass_filter)
    TMP.mkdir(parents=True, exist_ok=True)
    raw, den = TMP / f'{name}.raw.exr', TMP / f'{name}.exr'
    img.filepath_raw, img.file_format = str(raw), 'OPEN_EXR'
    img.save()
    B.denoise(raw, den)
    out = bpy.data.images.load(str(den))
    px = np.empty(out.size[0] * out.size[1] * 4, dtype=np.float32)
    out.pixels.foreach_get(px)
    return px.reshape(out.size[1], out.size[0], 4)[..., :3]


def srgb(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def save_rgba(path: Path, rgb: np.ndarray, alpha: np.ndarray | None = None) -> Path:
    h, w = rgb.shape[:2]
    px = np.ones((h, w, 4), dtype=np.float32)
    px[..., :3] = rgb
    if alpha is not None:
        px[..., 3] = alpha
    img = bpy.data.images.new(path.stem, w, h, alpha=alpha is not None)
    img.colorspace_settings.name = 'Non-Color'  # values are already display-encoded
    img.pixels.foreach_set(px.ravel())
    path.parent.mkdir(parents=True, exist_ok=True)
    img.filepath_raw, img.file_format = str(path), 'PNG'
    img.save()
    bpy.data.images.remove(img)
    return path


def contact_decal(name, objs, margin=0.35, texel=0.01) -> bpy.types.Object:
    """Bake the objects' shadow on the floor into an alpha decal: 1 - (light with objects / without)."""
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector((min(p.x for p in pts) - margin, min(p.y for p in pts) - margin, 0))
    hi = Vector((max(p.x for p in pts) + margin, max(p.y for p in pts) + margin, 0))
    plane = floor_plane(f'{name}_contact_shadow', lo, hi)
    mat = bpy.data.materials.new(f'{name}_decal_bake')
    mat.use_nodes = True
    mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.8, 0.8, 0.8, 1)
    plane.data.materials.append(mat)
    res = min(1024, 2 ** math.ceil(math.log2(max(hi.x - lo.x, hi.y - lo.y) / texel)))
    lit = bake_image(f'{name}_decal_lit', res, [plane], {'DIRECT', 'INDIRECT'})
    for o in objs:
        o.hide_render = True
    bare = bake_image(f'{name}_decal_bare', res, [plane], {'DIRECT', 'INDIRECT'})
    for o in objs:
        o.hide_render = False
    shade = 1 - np.clip(lit.mean(-1) / np.maximum(bare.mean(-1), 1e-4), 0, 1)
    # drop the faint, far-reaching occlusion so the decal fades to nothing at its edges
    alpha = np.clip((shade - 0.12) / 0.8, 0, 0.9) ** 0.85
    tex = save_rgba(TMP / f'{name}_contact_shadow.png', np.full(lit.shape, (0.10, 0.10, 0.14)), alpha)
    plane.data.materials.clear()
    plane.data.materials.append(decal_material(f'{name}_contact_shadow', tex))
    plane['fleet'] = 'decal'
    return plane


def decal_material(name, tex: Path) -> bpy.types.Material:
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes['Principled BSDF']
    t = nt.nodes.new('ShaderNodeTexImage')
    t.image = bpy.data.images.load(str(tex))
    nt.links.new(t.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(t.outputs['Alpha'], bsdf.inputs['Alpha'])
    m.surface_render_method = 'BLENDED'
    return m


def baked_material(name, tex: Path) -> bpy.types.Material:
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    t = nt.nodes.new('ShaderNodeTexImage')
    t.image = bpy.data.images.load(str(tex))
    nt.links.new(t.outputs['Color'], nt.nodes['Principled BSDF'].inputs['Base Color'])
    return m


def only_lightmap_uv(o) -> None:
    uvs = o.data.uv_layers
    for layer in [l for l in uvs if l.name != 'Lightmap']:
        uvs.remove(layer)
    uvs['Lightmap'].name = 'UVMap'


def prelit_static(name: str, objs: list, texel: float) -> dict:
    """Bake albedo x studio x AO of `objs` into one texture and write A/<name>.glb with its decal."""
    for o in objs:
        o.data = o.data.copy()  # instanced plants/binders need their own UVs
        o['fleet'] = 'baked'
        for mat in {s.material for s in o.material_slots if s.material}:
            if mat.node_tree and 'Principled BSDF' in mat.node_tree.nodes:
                mat.node_tree.nodes['Principled BSDF'].inputs['Emission Strength'].default_value = 0
    info = A.lightmap_uvs(texel=texel, max_res=4096)
    decal = contact_decal(name, objs)
    proxy = B.proxy(objs)
    rgb = bake_image(f'{name}_albedo_light', info['resolution'], [proxy], {'COLOR', 'DIRECT', 'INDIRECT'})
    bpy.data.objects.remove(proxy)
    tex = save_rgba(TMP / f'{name}_baked.png', srgb(rgb))
    mat = baked_material(f'{name}_baked', tex)
    for o in objs:
        o.hide_render = False
        only_lightmap_uv(o)
        o.data.materials.clear()
        o.data.materials.append(mat)
    return write_glb(name, info | {'decal': decal.name})


def write_glb(name: str, info: dict) -> dict:
    dest = OUT / 'A' / f'{name}.glb'
    dest.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action='DESELECT')
    for o in bpy.data.objects:
        o.select_set(o.type in {'MESH', 'EMPTY', 'ARMATURE'})
    bpy.ops.export_scene.gltf(
        filepath=str(dest), export_format='GLB', use_selection=True, export_image_format='WEBP',
        export_image_quality=88, export_extras=True, export_apply=True, export_lights=False, export_cameras=False,
        export_animations=True, export_animation_mode='ACTIONS', export_force_sampling=True, export_yup=True)
    return {'file': dest.name, 'bytes': dest.stat().st_size, **info}


def prelit_robot() -> dict:
    """The robot keeps its rig: bake studio shading only (seated, typing) under its per-material colours."""
    root, rig, seat_point = load_robot()
    root.location = Vector((0, 0, W.SEAT_H)) - seat_point  # seated at chair height, over the floor at 0
    show_props(None)
    for name in ('prop_pencil', 'prop_flask'):
        bpy.data.objects[name].hide_render = False
    pose_robot(rig, 'Type', 1)
    studio(Vector((0, 0, 0.8)))
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    for o in meshes:
        o['fleet'] = 'baked'
    info = A.lightmap_uvs(texel=0.004, max_res=2048)
    for o in meshes:  # Cycles bakes into the active UV map
        o.data.uv_layers.active = o.data.uv_layers['Lightmap']
    shade = bake_image('robot_shade', info['resolution'], meshes, {'DIRECT', 'INDIRECT'})
    shade = shade / np.percentile(shade.max(-1), 99.5)  # 1 = fully lit: the page multiplies by the colours
    tex = save_rgba(TMP / 'robot_shade.png', srgb(shade * 0.95))
    # the robot's floor shadow while seated; the page puts it under the seat
    chairless = contact_decal('robot', [o for o in meshes if not o.name.startswith('prop_')], margin=0.25)
    decal = {'file': 'robot_shadow.glb', 'offset_from_seat': [0, 0]}
    for o in meshes:
        only_lightmap_uv(o)
        for slot in o.material_slots:
            add_shade(slot.material, tex)
    # export the decal on its own (it belongs to the floor, not the rig), then the robot
    for o in bpy.data.objects:
        o.select_set(o == chairless)
    OUT.joinpath('A').mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(OUT / 'A' / 'robot_shadow.glb'), export_format='GLB',
                              use_selection=True, export_image_format='WEBP', export_yup=True)
    bpy.data.objects.remove(chairless)
    for t in list(rig.animation_data.nla_tracks):
        rig.animation_data.nla_tracks.remove(t)
    root.location = (0, 0, 0)
    show_props(None)
    for name in ('prop_pencil', 'prop_flask'):
        bpy.data.objects[name].hide_render = bpy.data.objects[name].hide_viewport = False
    return write_glb('robot', info | {'seat_point': list(seat_point), 'decal': decal})


def add_shade(mat: bpy.types.Material, tex: Path) -> None:
    """Base colour = baked shading x the material's own colour (exported as texture x baseColorFactor)."""
    if mat.name.startswith('robot_eye') or mat.get('shaded'):
        return
    nt = mat.node_tree
    bsdf = nt.nodes['Principled BSDF']
    t = nt.nodes.new('ShaderNodeTexImage')
    t.image = bpy.data.images.load(str(tex), check_existing=True)
    mix = nt.nodes.new('ShaderNodeMix')
    mix.data_type, mix.blend_type = 'RGBA', 'MULTIPLY'
    mix.inputs['Factor'].default_value = 1.0
    mix.inputs['B'].default_value = bsdf.inputs['Base Color'].default_value
    nt.links.new(t.outputs['Color'], mix.inputs['A'])
    nt.links.new(mix.outputs['Result'], bsdf.inputs['Base Color'])
    mat['shaded'] = True


def variant_a() -> dict:
    out = {}
    A.reset()
    objs = build_bench()
    studio(Vector((W.BENCH_X0 + 1.5 * W.DESK_W, W.BENCH_Y, 0.5)))
    out['bench'] = prelit_static('bench', objs, texel=0.006)
    A.reset()
    objs = build_plant()
    studio(Vector((*PLANT_AT, 0.6)))
    out['plant'] = prelit_static('plant', objs, texel=0.003)
    A.reset()
    out['robot'] = prelit_robot()
    return out


# --- B1: sprites ------------------------------------------------------------------------------

def shadow_catcher() -> None:
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=40)
    me = bpy.data.meshes.new('catcher')
    bm.to_mesh(me)
    ob = bpy.data.objects.new('catcher', me)
    bpy.context.scene.collection.objects.link(ob)
    ob.is_shadow_catcher = True


def frame_camera(points: list, ref: Vector, mult: int, margin=0.3) -> dict:
    """An orthographic camera at the l2 angle framing `points` at mult x the home density; returns where
    `ref` (a world point) lands in the image, for placing the sprite."""
    right, up, back = axes()
    us, vs = [p.dot(right) for p in points], [p.dot(up) for p in points]
    u0, u1, v0, v1 = min(us) - margin, max(us) + margin, min(vs) - margin, max(vs) + margin
    ppm = PX_PER_M * mult
    w, h = math.ceil((u1 - u0) * ppm), math.ceil((v1 - v0) * ppm)
    u1, v0 = u0 + w / ppm, v1 - h / ppm  # whole pixels
    cam = bpy.data.cameras.get('sprite_cam') or bpy.data.cameras.new('sprite_cam')
    cam.type, cam.clip_end = 'ORTHO', 200
    cam.ortho_scale = max(u1 - u0, v1 - v0)
    ob = bpy.data.objects.get('sprite_cam') or bpy.data.objects.new('sprite_cam', cam)
    if ob.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(ob)
    centre = right * (u0 + u1) / 2 + up * (v0 + v1) / 2
    ob.location = centre + back * 60
    A.aim(ob, centre)
    s = bpy.context.scene
    s.camera = ob
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = w, h, 100
    return {'size': [w, h], 'px_per_m': round(ppm, 3), 'ref_world': [round(v, 4) for v in ref],
            'ref_px': [round((ref.dot(right) - u0) * ppm, 2), round((v1 - ref.dot(up)) * ppm, 2)]}


def render_png(path: Path) -> Path:
    s = bpy.context.scene
    s.render.film_transparent = True
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'
    s.render.image_settings.compression = 100
    s.cycles.samples = 128
    s.cycles.use_denoising = True
    s.render.filepath = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return soften_catcher(path)


def soften_catcher(path: Path) -> Path:
    """The shadow catcher also records the faint, wide occlusion of the whole object, so the sprite's edge
    would cut a grey field. Drop that floor from shadow-only pixels (black, not opaque), as the A decal does."""
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    shadow = (px[:, 3] < 0.98) & (px[:, :3].max(1) < 0.02)
    px[shadow, 3] = np.clip((px[shadow, 3] - 0.1) / 0.9, 0, 1)
    img.pixels.foreach_set(px.ravel())
    img.save()
    bpy.data.images.remove(img)
    return path


def world_points(objs) -> list:
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        e = o.evaluated_get(dg)
        pts += [e.matrix_world @ v.co for v in e.data.vertices]
    return pts


def sprites_static(name: str, objs: list, ref: Vector) -> dict:
    shadow_catcher()
    out = {}
    pts = world_points(objs) + [p - Vector((0, 0, p.z)) for p in world_points(objs)]  # and their floor print
    for mult in MULTS:
        meta = frame_camera(pts, ref, mult, margin=0.6)  # room for the soft shadow to fade out
        png = render_png(OUT / 'B1' / f'{name}@{mult}x.png')
        out[f'{mult}x'] = {'file': png.name, 'bytes': png.stat().st_size, **meta}
    return out


def sprite_sheet(frames: list[Path], dest: Path) -> Path:
    imgs = [bpy.data.images.load(str(f)) for f in frames]
    w, h = imgs[0].size
    sheet = np.zeros((h, w * len(imgs), 4), dtype=np.float32)
    for i, im in enumerate(imgs):
        px = np.empty(w * h * 4, dtype=np.float32)
        im.pixels.foreach_get(px)
        sheet[:, i * w:(i + 1) * w] = px.reshape(h, w, 4)
    out = bpy.data.images.new(dest.stem, w * len(imgs), h, alpha=True)
    out.colorspace_settings.name = imgs[0].colorspace_settings.name
    out.pixels.foreach_set(sheet.ravel())
    out.filepath_raw, out.file_format = str(dest), 'PNG'
    out.save()
    for im in imgs:
        bpy.data.images.remove(im)
    for f in frames:
        f.unlink()
    return dest


def sprites_robot(bench_objs: list) -> dict:
    root, rig, seat_point = load_robot()
    shadow_catcher()
    for o in bench_objs:
        o.is_holdout = True  # the desk cuts the robot exactly as it will overlap it in the scene
        o.visible_shadow = False  # its own shadows are already in the bench sprite
    robot = [o for o in bpy.data.objects if o.type == 'MESH' and o.parent and o.name not in ('catcher',)
             and o not in bench_objs]
    out = {}
    body = bpy.data.materials['robot_body'].node_tree.nodes['Principled BSDF'].inputs['Base Color']
    for pose, (arm, desk, prop, host) in POSES.items():
        anchor = seat_anchor(desk)
        root.location = anchor - seat_point
        show_props(prop)
        body.default_value = A.srgb(host)
        length = bpy.data.actions[arm].frame_range[1] - 1
        frames_at = [1 + length * i / FRAMES for i in range(FRAMES)]
        pts = []
        for f in frames_at:
            pose_robot(rig, arm, f)
            pts += world_points([o for o in robot if not o.hide_render])
        out[pose] = {}
        for mult in MULTS:
            meta = frame_camera(pts, anchor, mult, margin=0.12)
            files = []
            for i, f in enumerate(frames_at):
                pose_robot(rig, arm, f)
                files.append(render_png(TMP / f'robot_{pose}_{mult}x_{i}.png'))
            sheet = sprite_sheet(files, OUT / 'B1' / f'robot_{pose}@{mult}x.png')
            out[pose][f'{mult}x'] = {'file': sheet.name, 'bytes': sheet.stat().st_size, 'frames': FRAMES,
                                     'fps': round(FRAMES / (length / 24), 2), 'desk': desk, **meta}
    return out


def variant_b1() -> dict:
    out = {}
    A.reset()
    bench = build_bench()
    centre = Vector((W.BENCH_X0 + 1.5 * W.DESK_W, W.BENCH_Y, 0.5))
    studio(centre)
    out['bench'] = sprites_static('bench', bench, Vector((0, 0, 0)))
    out['robot'] = sprites_robot(bench)
    A.reset()
    objs = build_plant()
    studio(Vector((*PLANT_AT, 0.6)))
    out['plant'] = sprites_static('plant', objs, Vector((*PLANT_AT, 0)))
    return out


def main() -> None:
    A.require_blender()
    which = A.scene_arg()
    result = {'A': variant_a, 'B1': variant_b1}[which]()
    meta = OUT / which / 'manifest.json'
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({'variant': which, 'camera': VIEW | {'px_per_m_1x': round(PX_PER_M, 3)},
                                'blender': bpy.app.version_string, 'objects': result}, indent=2) + '\n')
    print('BAKEOFF', which, json.dumps({k: (v.get('bytes') if isinstance(v, dict) and 'bytes' in v else '...')
                                        for k, v in result.items()}))


if __name__ == '__main__':
    main()
