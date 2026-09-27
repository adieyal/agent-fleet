"""Paper-doll sprite atlases of the concept robot for the 2D runtime.

    uv run --group dev python art/scripts/build_robot_sprites.py           # render (Blender), then pack
    uv run --group dev python art/scripts/build_robot_sprites.py --pack    # pack the last render only

Environment: SPRITES_ONLY=Clip or Clip:S renders just that; SPRITES_DRY=1 frames the cameras without rendering;
SPRITES_ANCHORS=1 recomputes the anchors of finished clips without rendering; SPRITES_RES=12 packs only those sets.
A run resumes: each finished clip and facing is kept in art/build/robot_sprites/frames/ (delete it to start over),
and when the GPU is short of memory (it may be shared) a frame waits for it, then falls back to the CPU.

Needs art/build/robot/robot.blend (art/build.sh robot) and the fetched sources (the studio HDRI). The
script runs itself inside Blender (BLENDER, default ~/.local/bin/blender) to render, then packs with
Pillow. Output: fleet/web/assets/world/robot/sprites/ (atlases per resolution and sprites.json); the
format is documented in docs/design/robot-sprites.md.

Render (Cycles on the GPU, the bake-off B1 camera and studio: l2's orthographic view, pitch 44.5°, yaw
21.25°, a soft disk key from the upper left and a dim white_studio_06 fill):
- Every frame is rendered once per layer, each layer a view layer of the same scene: `body` (the robot,
  shell in neutral light grey, with its tint mask and, seated, a world-height split at the desk top),
  `shadow` (the contact shadow alone, from a shadow catcher), the faces (`face_eyes`, `face_band`), the
  host kits (`acc_*`, with tint masks) and the held items (`item_*`). Overlay layers see the robot as a
  holdout, so each comes out already cut where the body passes in front of it.
- The tint mask is the body shell's coverage times its share of diffuse and emitted light, so specular
  highlights stay untinted.
- Four facings, all rendered: the l2 camera is yawed 21.25°, so a mirrored sprite would face 42.5° off
  the room's axes, and the robot is not symmetric anyway (the antenna, the waving arm, the flask hand).
- Frames are rendered at 4x and scaled down for 2x and 1x.
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ART = HERE.parent
REPO = ART.parent
BUILD = ART / 'build' / 'robot_sprites'
FRAMES_DIR = BUILD / 'frames'
OUT = REPO / 'fleet' / 'web' / 'assets' / 'world' / 'robot' / 'sprites'

# Directions: the deck's facing (0 towards the door, which is towards the camera side of the room; π towards
# the back wall), as a turn of the robot about +Z.
DIRS = [('S', 0), ('E', 90), ('N', 180), ('W', 270)]
SEATED_DIRS = ['S', 'N']  # the bench is worked facing out of the room, the terminals facing the back wall
# clip: (source action, frames, loop, arms over the end of Sitting, items shown)
CLIPS = {
    'Walking': ('Walking', 12, True, None, ['box', 'book']),
    'Idle': ('Idle', 8, True, None, ['box', 'book', 'sheet']),
    'Wave': ('Wave', 12, True, None, []),
    'Yes': ('Yes', 8, False, None, []),
    'No': ('No', 8, False, None, []),
    'Death': ('Death', 16, False, None, []),
    'Sitting': ('Sitting', 8, False, None, ['book', 'sheet']),
    'Typing': ('Sitting', 8, True, 'Type', ['laptop']),
    'Writing': ('Sitting', 8, True, 'Write', ['pencil']),
    'Holding': ('Sitting', 8, True, 'Hold', ['flask']),
}
SEATED = {'Sitting', 'Typing', 'Writing', 'Holding'}
HOLD_LAST = {'Sitting', 'Death'}  # played once and held on the last frame
ACCESSORIES = ['backpack', 'antenna', 'halo', 'crest']
ITEMS = ['box', 'book', 'sheet', 'laptop', 'pencil', 'flask']
FACES = {'eyes': 'codex', 'band': 'claude'}
GREY = '#cccccc'  # the shell's neutral grey; the runtime multiplies masked pixels by host / GREY
RES = (1, 2, 4)
SAMPLES = {'body': 48, 'acc': 24, 'other': 16}  # per view layer; OIDN cleans up
SIT_END = 10  # last frame of Sitting
PAGE = 2048  # atlas page size limit
QUALITY = {1: (64, 50), 2: (50, 40), 4: (78, 70)}  # WebP colour and alpha quality per resolution
SHADOW_SCALE = 4  # shadows are soft blurs: stored at a quarter of their size
MASK_LEVELS = 8
MASK_SCALE = {1: 2, 2: 4, 4: 2}  # a tint mask is stored this many times smaller than its colour layer
SHADOW_OPACITY = 0.55  # B2's contact shadows are soft grey, not black
DEDUPE_MEAN, DEDUPE_P99 = 1.5, 24  # of 255: a layer this close (mean, and 99th percentile) to one already
# stored in the same clip and direction is reused; render noise alone differs by less


# ======================================================================== render (inside Blender)

def render_all() -> None:
    import bpy
    sys.path.insert(0, str(HERE))
    import artlib as A
    import bakeoff as K  # the B1 camera and studio
    import build_workbench as W
    from mathutils import Matrix, Vector

    A.require_blender()
    blend = A.BUILD / 'robot' / 'robot.blend'
    if not blend.exists():
        sys.exit('sprites: run art/build.sh robot first')
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene = bpy.context.scene
    rig, root = bpy.data.objects['RobotArmature'], bpy.data.objects['RootNode']
    root.rotation_mode = 'XYZ'
    robot = bpy.data.objects['robot']
    info = json.loads(scene['fleet_build'])
    seat = Vector(info['runtime']['seat_point'])
    desk_top = seat.z + W.DESK_H - W.SEAT_H  # in the robot's frame, seated with seat_point on the chair
    seat_floor = seat.z - W.SEAT_H

    # --- materials: neutral grey shell; AOVs for the tint mask and the desk split
    body_mat = bpy.data.materials['robot_body']
    body_mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = A.srgb(GREY)
    halo = bpy.data.materials['robot_halo']
    halo.node_tree.nodes['Principled BSDF'].inputs['Emission Strength'].default_value = 2.0

    def aov(mat, name, value_socket=None, value=None):
        nt = mat.node_tree
        node = nt.nodes.new('ShaderNodeOutputAOV')
        node.aov_name = name
        if value_socket is not None:
            nt.links.new(value_socket, node.inputs['Value'])
        else:
            node.inputs['Value'].default_value = value

    items = make_items(bpy, A, rig, Vector, Matrix)

    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        nt = mat.node_tree
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        nt.links.new(geo.outputs['Position'], sep.inputs['Vector'])
        aov(mat, 'height', sep.outputs['Z'])
        if mat.name in ('robot_body', 'robot_halo'):
            aov(mat, 'tint', value=1.0)

    # --- collections, one per layer
    def collection(name, objs):
        c = bpy.data.collections.new(name)
        scene.collection.children.link(c)
        for o in objs:
            for old in list(o.users_collection):
                old.objects.unlink(o)
            c.objects.link(o)
        return c

    layers = {'body': collection('body', [robot])}
    for k in FACES:
        layers[f'face_{k}'] = collection(f'face_{k}', [bpy.data.objects[f'robot_{k}']])
    for k in ACCESSORIES:
        layers[f'acc_{k}'] = collection(f'acc_{k}', [bpy.data.objects[f'acc_{k}']])
    for k in ITEMS:
        layers[f'item_{k}'] = collection(f'item_{k}', [items[k]])
    K.shadow_catcher()
    catcher = bpy.data.objects['catcher']
    layers['shadow'] = collection('catcher', [catcher])

    K.studio(Vector((0, 0, 0.7)))
    # the key light shades the robot; the contact shadow comes from a broad soft light straight overhead
    # instead, so it pools under the feet rather than falling long across the floor
    key = collection('key_light', [bpy.data.objects['key']])
    A.light('contact', 'AREA', (0, 0, 10.0), 3000, size=5.0)
    contact = collection('contact_light', [bpy.data.objects['contact']])
    c = scene.cycles
    c.samples = SAMPLES['body']
    c.use_denoising, c.denoiser = True, 'OPTIX'  # on the GPU, and lighter on its memory than OIDN's GPU path
    c.adaptive_threshold, c.adaptive_min_samples = 0.02, 4
    # a studio render: short paths are enough (glass needs a few transmission bounces)
    c.max_bounces, c.diffuse_bounces, c.glossy_bounces, c.transmission_bounces, c.transparent_max_bounces = 6, 2, 2, 4, 4
    scene.render.film_transparent = True
    scene.render.fps = 24

    # --- view layers
    base = scene.view_layers[0]
    base.name = 'body'
    views = {'body': base}
    for name in layers:
        if name not in ('body', 'shadow'):
            views[name] = scene.view_layers.new(name)
    views['shadow'] = scene.view_layers.new('shadow')
    for name, vl in views.items():
        for coll, shadow_only in ((key, False), (contact, True)):
            vl.layer_collection.children[coll.name].exclude = (name == 'shadow') != shadow_only
        vl.use_sky = name != 'shadow'  # the studio fill would darken the whole floor around the robot
        for cname, coll in layers.items():
            lc = vl.layer_collection.children[coll.name]
            lc.exclude, lc.holdout, lc.indirect_only = True, False, False
            if cname == name:
                lc.exclude = False
            elif cname == 'body' and name != 'body':
                lc.exclude = False
                if name == 'shadow':
                    lc.indirect_only = True  # casts the shadow, unseen
                else:
                    lc.holdout = True  # cuts the overlay where the body is in front
        tinted = name == 'body' or name.startswith('acc_')
        vl.samples = SAMPLES['body'] if name == 'body' else SAMPLES['acc'] if tinted else SAMPLES['other']
        for pname in ('tint', 'height'):
            a = vl.aovs.add()
            a.name, a.type = pname, 'VALUE'
        if tinted:
            for p in ('diffuse_direct', 'diffuse_indirect', 'diffuse_color', 'glossy_direct', 'glossy_indirect',
                      'glossy_color', 'emit'):
                setattr(vl, f'use_pass_{p}', True)
    tmp = BUILD / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    compositor(bpy, scene, views, tmp)
    # a view layer that is never rendered but evaluates everything, for framing and anchors (a layer that
    # excludes a collection never updates its objects)
    everything = scene.view_layers.new('everything')
    everything.use = False
    everything.layer_collection.children['catcher'].exclude = True
    global EVAL
    EVAL = everything

    # --- per clip and direction: frame the camera on every frame, then render
    manifest = {'seat_point': list(seat), 'desk_top': desk_top, 'seat_floor': seat_floor,
                'px_per_m_4x': K.PX_PER_M * 4, 'view': K.VIEW, 'clips': {}}
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    right, up, _ = K.axes()
    only = os.environ.get('SPRITES_ONLY')  # e.g. "Idle:S" while iterating
    for clip, (action, count, loop, arms, clip_items) in CLIPS.items():
        dirs = SEATED_DIRS if clip in SEATED else [d for d, _ in DIRS]
        times = sample_times(bpy.data.actions[arms or action], count, loop, clip)
        manifest['clips'][clip] = {'loop': loop, 'hold_last': clip in HOLD_LAST, 'count': count,
                                   'fps': round(clip_fps(bpy.data.actions[arms or action], count, clip), 3),
                                   'items': clip_items, 'dirs': {}}
        catcher.location.z = seat_floor if clip in SEATED else 0.0
        for d in dirs:
            if only and only not in (clip, f'{clip}:{d}'):
                continue
            done = FRAMES_DIR / f'{clip}_{d}.json'  # a finished clip and facing: kept, so a failed run resumes
            redo_anchors = os.environ.get('SPRITES_ANCHORS') and done.exists()  # recompute anchors, no render
            if done.exists() and not redo_anchors:
                manifest['clips'][clip]['dirs'][d] = json.loads(done.read_text())
                print('cached', clip, d, flush=True)
                continue
            root.rotation_euler.z = math.radians(dict(DIRS)[d])
            pts = []
            for t in times:
                pose(bpy, rig, clip, action, arms, t)
                pts += points(bpy, [robot] + [bpy.data.objects[f'acc_{k}'] for k in ACCESSORIES]
                              + [items[k] for k in clip_items])
            floor = [Vector((p.x, p.y, catcher.location.z)) for p in pts]
            ref = Vector((0, 0, 0))
            cam = K.frame_camera(pts + floor, ref, 4, margin=0.4)
            w, h = cam['size']
            w4, h4 = -(-w // 8) * 8, -(-h // 8) * 8  # the crop grid (see collect)
            scene.render.resolution_x, scene.render.resolution_y = w4, h4
            bpy.data.objects['sprite_cam'].data.ortho_scale = max(w4, h4) / cam['px_per_m']
            # keep ref's pixel: frame_camera centred on the unpadded frame
            cam_ob = bpy.data.objects['sprite_cam']
            shift = right * ((w4 - w) / 2 / cam['px_per_m']) - up * ((h4 - h) / 2 / cam['px_per_m'])
            cam_ob.location += shift
            ppm = cam['px_per_m']
            ref_px = cam['ref_px']
            print('canvas', clip, d, w4, h4, flush=True)
            if os.environ.get('SPRITES_DRY'):
                continue

            def to_px(p):
                return [round(ref_px[0] + (p - ref).dot(right) * ppm, 2), round(ref_px[1] - (p - ref).dot(up) * ppm, 2)]

            if redo_anchors:
                kept = json.loads(done.read_text())
                assert kept['canvas'] == [w4, h4], (clip, d, kept['canvas'], [w4, h4])
                for fr, t in zip(kept['frames'], times):
                    pose(bpy, rig, clip, action, arms, t)
                    fr['anchors'] = anchors(bpy, rig, robot, to_px, Vector, clip)
                done.write_text(json.dumps(kept))
                manifest['clips'][clip]['dirs'][d] = kept
                print('anchors', clip, d, flush=True)
                continue
            frames = []
            for i, t in enumerate(times):
                pose(bpy, rig, clip, action, arms, t)
                active = {'body', 'shadow', 'face_eyes', 'face_band', *[f'acc_{k}' for k in ACCESSORIES],
                          *[f'item_{k}' for k in clip_items]}
                for name, vl in views.items():
                    vl.use = name in active
                # render only around what this frame holds (and its shadow): sampling and denoising cost area
                here = points(bpy, [robot] + [bpy.data.objects[f'acc_{k}'] for k in ACCESSORIES]
                              + [items[k] for k in clip_items])
                px = [to_px(q) for q in here + [Vector((q.x, q.y, catcher.location.z)) for q in here]]
                pad = 0.4 * ppm
                x0, x1 = min(q[0] for q in px) - pad, max(q[0] for q in px) + pad
                y0, y1 = min(q[1] for q in px) - pad, max(q[1] for q in px) + pad
                r = scene.render
                r.use_border, r.use_crop_to_border = True, False
                r.border_min_x, r.border_max_x = max(0.0, x0 / w4), min(1.0, x1 / w4)
                r.border_min_y, r.border_max_y = max(0.0, 1 - y1 / h4), min(1.0, 1 - y0 / h4)
                render_frame(bpy, scene, tmp)
                key = f'{clip}_{d}_{i}'
                saved = collect(bpy, tmp, views, active, desk_top if clip in SEATED else None, FRAMES_DIR / f'{key}.npz')
                frames.append({'key': key, 'layers': saved, 'anchors': anchors(bpy, rig, robot, to_px, Vector, clip)})
                print('frame', key, len(saved), flush=True)
            manifest['clips'][clip]['dirs'][d] = {'canvas': [w4, h4], 'foot': to_px(ref), 'frames': frames,
                                                  **({'seat': to_px(seat)} if clip in SEATED else {})}
            done.write_text(json.dumps(manifest['clips'][clip]['dirs'][d]))
    (BUILD / 'frames.json').write_text(json.dumps(manifest))
    print('RENDERED', BUILD / 'frames.json')


def render_frame(bpy, scene, tmp: Path) -> None:
    """Render every active view layer. The GPU may be shared with other jobs: when it runs out of memory, wait
    for it (up to 15 minutes), then render the frame on the CPU."""
    import time
    for attempt in range(31):
        for f in tmp.glob('*'):
            f.unlink()
        scene.render.filepath = str(tmp / 'combined')
        try:
            bpy.ops.render.render(write_still=False)
            return
        except RuntimeError as err:
            if 'GPU memory' not in str(err):
                raise
            if attempt < 30:
                print('gpu busy, waiting', flush=True)
                time.sleep(30)
                continue
    print('gpu busy: rendering this frame on the CPU', flush=True)
    scene.cycles.device = 'CPU'
    try:
        for f in tmp.glob('*'):
            f.unlink()
        bpy.ops.render.render(write_still=False)
    finally:
        scene.cycles.device = 'GPU'


def make_items(bpy, A, rig, Vector, Matrix) -> dict:
    """What the robot carries. The box, book and sheet ride on the torso, in front of the chest (the deck holds
    them in the android's frame, not in its hands); the laptop sits on the desk under the typing hands; the
    pencil and flask are the build's hand props."""
    m = {'box': A.material('item_box', '#b98b52', rough=0.85), 'tape': A.material('item_tape', '#d9c08a', rough=0.5),
         'book': A.material('item_book', '#b4463c', rough=0.6), 'pages': A.material('item_pages', '#f3ecdc', rough=0.8),
         'sheet': A.material('item_sheet', '#f4f6fa', rough=0.7), 'ink': A.material('item_ink', '#7c8594', rough=0.7),
         'laptop': A.material('item_laptop', '#3f4147', rough=0.35, metal=0.6),
         'screen': A.material('item_screen', '#0d1117', rough=0.1, emission='#1d2a3a')}
    M = rig.matrix_world
    torso = M @ rig.data.bones['Torso'].head_local
    s = json.loads(bpy.context.scene['fleet_build'])['scale']  # metres per model unit
    front = torso.y - 0.62 * s  # the chest's front
    made = {}

    def join(objs, name):
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.object.join()
        ob = bpy.context.view_layer.objects.active
        ob.name = name
        return ob

    def hang(ob, bone):
        bpy.context.view_layer.update()
        world = ob.matrix_world.copy()
        ob.parent, ob.parent_type, ob.parent_bone = rig, 'BONE', bone
        bpy.context.view_layer.update()
        ob.matrix_world = world

    for p in rig.pose.bones:
        p.location, p.rotation_quaternion, p.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    rig.animation_data.action = None
    bpy.context.view_layer.update()
    # a parcel against the belly
    c = Vector((0, front - 0.13, torso.z - 0.02))
    made['box'] = join([A.box('box', (0.34, 0.26, 0.24), c - Vector((0, 0, 0.12)), m['box'], kind='dynamic', bevel=0.01, tile=None),
                        A.box('box_tape', (0.06, 0.262, 0.242), c - Vector((0, 0, 0.12)), m['tape'], kind='dynamic', tile=None)], 'item_box')
    # a book held up at the chest, tipped towards the face
    c = Vector((0, front - 0.12, torso.z + 0.08))
    rot = (math.radians(55), 0, 0)
    made['book'] = join([A.box('book', (0.24, 0.05, 0.31), c, m['book'], kind='dynamic', bevel=0.006, rot=rot, tile=None),
                         A.box('book_pages', (0.225, 0.052, 0.29), c + Vector((0.012, 0.0, 0.0)), m['pages'], kind='dynamic', rot=rot, tile=None)],
                        'item_book')
    # a printout, tipped the same way
    c = Vector((0, front - 0.1, torso.z + 0.06))
    made['sheet'] = join([A.box('sheet', (0.21, 0.004, 0.29), c, m['sheet'], kind='dynamic', rot=rot, tile=None),
                          A.box('sheet_ink', (0.15, 0.006, 0.012), c + Vector((0, -0.004, 0.08)), m['ink'], kind='dynamic', rot=rot, tile=None),
                          A.box('sheet_ink2', (0.15, 0.006, 0.012), c + Vector((0, -0.01, 0.02)), m['ink'], kind='dynamic', rot=rot, tile=None)],
                         'item_sheet')
    for k in ('box', 'book', 'sheet'):
        hang(made[k], 'Torso')
    # the laptop on the desk under the typing hands (the desk's height as seated at the workbench)
    import build_workbench as W
    info = json.loads(bpy.context.scene['fleet_build'])
    seat = Vector(info['runtime']['seat_point'])
    top = seat.z + W.DESK_H - W.SEAT_H
    ahead = seat.y - 0.36  # the robot faces -Y; the keyboard under its reach
    base = A.box('laptop', (0.34, 0.24, 0.016), (0, ahead - 0.02, top), m['laptop'], kind='dynamic', bevel=0.004, tile=None)
    lid = A.box('laptop_lid', (0.34, 0.012, 0.23), (0, ahead - 0.14 - 0.03, top + 0.11), m['laptop'], kind='dynamic', bevel=0.004,
                rot=(math.radians(-15), 0, 0), tile=None)
    screen = A.box('laptop_screen', (0.31, 0.004, 0.2), (0, ahead - 0.14 - 0.02, top + 0.115), m['screen'], kind='dynamic',
                   rot=(math.radians(-15), 0, 0), tile=None)
    made['laptop'] = join([base, lid, screen], 'item_laptop')
    made['laptop'].parent = bpy.data.objects['RootNode']
    made['laptop'].matrix_parent_inverse = bpy.data.objects['RootNode'].matrix_world.inverted()
    made['pencil'] = bpy.data.objects['prop_pencil']
    made['flask'] = bpy.data.objects['prop_flask']
    for ob in made.values():
        ob.hide_render = False
    return made


def sample_times(action, count: int, loop: bool, clip: str) -> list[float]:
    f0, f1 = action.frame_range
    if clip in HOLD_LAST:  # from the first frame to the last, inclusive
        return [f0 + (f1 - f0) * i / (count - 1) for i in range(count)]
    return [f0 + (f1 - f0) * i / count for i in range(count)]


def clip_fps(action, count: int, clip: str) -> float:
    f0, f1 = action.frame_range
    return (count - 1 if clip in HOLD_LAST else count) / (max(1, f1 - f0) / 24)


def pose(bpy, rig, clip, action, arms, t) -> None:
    for pb in rig.pose.bones:  # from rest: a clip leaves the bones it doesn't key as they are
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    ad = rig.animation_data
    ad.action = bpy.data.actions[action]
    scene = bpy.context.scene
    if arms:  # the end of Sitting, with the arm clip over it (it keys only the arms)
        scene.frame_set(SIT_END)
        held = {pb.name: (pb.location.copy(), pb.rotation_quaternion.copy()) for pb in rig.pose.bones}
        ad.action = None
        for pb in rig.pose.bones:
            pb.location, pb.rotation_quaternion = held[pb.name]
        ad.action = bpy.data.actions[arms]
    scene.frame_set(int(t), subframe=t - int(t))
    bpy.context.view_layer.update()


EVAL = None  # the view layer to evaluate positions in; see render_all


def evaluated():
    EVAL.update()
    return EVAL.depsgraph


def points(bpy, objs) -> list:
    dg = evaluated()
    out = []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        vs = me.vertices
        out += [mw @ vs[i].co for i in range(0, len(vs), 5)]
        ev.to_mesh_clear()
    return out


def compositor(bpy, scene, views, tmp: Path) -> None:
    """Per view layer, write the colour (PNG, as displayed) and, where needed, the tint mask and the height
    (EXR). tint = shell coverage x (diffuse + emission) / (diffuse + glossy + emission)."""
    scene.use_nodes = True
    nt = scene.node_tree
    nt.nodes.clear()
    comp = nt.nodes.new('CompositorNodeComposite')

    def math_node(op, a, b):
        n = nt.nodes.new('CompositorNodeMath')
        n.operation = op
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                nt.links.new(v, n.inputs[i])
        return n.outputs[0]

    def mix(op, a, b):
        n = nt.nodes.new('CompositorNodeMixRGB')
        n.blend_type = op
        nt.links.new(a, n.inputs[1])
        nt.links.new(b, n.inputs[2])
        return n.outputs[0]

    def lum(sock):
        n = nt.nodes.new('CompositorNodeRGBToBW')
        nt.links.new(sock, n.inputs[0])
        return n.outputs[0]

    for name, vl in views.items():
        rl = nt.nodes.new('CompositorNodeRLayers')
        rl.layer = vl.name
        out = nt.nodes.new('CompositorNodeOutputFile')
        out.base_path = str(tmp)
        out.file_slots.clear()
        out.file_slots.new(f'{name}.rgba_')
        slot = out.file_slots[0]
        slot.use_node_format = False
        slot.format.file_format, slot.format.color_mode, slot.format.color_depth = 'PNG', 'RGBA', '8'
        nt.links.new(rl.outputs['Image'], out.inputs[0])
        if name == 'body':
            nt.links.new(rl.outputs['Image'], comp.inputs[0])
        extra = {'height': rl.outputs['height']}
        if name == 'body' or name.startswith('acc_'):
            d = lum(mix('MULTIPLY', mix('ADD', rl.outputs['DiffDir'], rl.outputs['DiffInd']), rl.outputs['DiffCol']))
            g = lum(mix('MULTIPLY', mix('ADD', rl.outputs['GlossDir'], rl.outputs['GlossInd']), rl.outputs['GlossCol']))
            e = lum(rl.outputs['Emit'])
            de = math_node('ADD', d, e)
            ratio = math_node('DIVIDE', de, math_node('ADD', math_node('ADD', de, g), 1e-4))
            extra['tint'] = math_node('MULTIPLY', rl.outputs['tint'], ratio)
        for key, sock in extra.items():
            out.file_slots.new(f'{name}.{key}_')
            slot = out.file_slots[-1]
            slot.use_node_format = False
            slot.format.file_format, slot.format.color_mode, slot.format.color_depth = 'OPEN_EXR', 'RGB', '32'
            nt.links.new(sock, out.inputs[-1])


def collect(bpy, tmp: Path, views, active, desk_top, dest: Path) -> dict:
    """Read one frame's files; keep each layer cropped to its pixels (on an 8-pixel grid, so the 2x and 1x
    crops and the half-size masks stay whole); returns {layer: [x, y, w, h]} at 4x."""
    import numpy as np

    def load(path):
        img = bpy.data.images.load(str(path))
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        bpy.data.images.remove(img)
        return px.reshape(h, w, 4)[::-1]

    def one(prefix):
        hits = sorted(tmp.glob(prefix + '*'))
        return load(hits[0]) if hits else None

    arrays, rects = {}, {}

    def keep(name, rgba, mask=None):
        a = rgba[..., 3]
        ys, xs = np.nonzero(a > 2 / 255)
        if not len(xs):
            return
        x0, y0 = xs.min() // 8 * 8, ys.min() // 8 * 8  # an 8-pixel grid: whole pixels at 1x, even halves
        x1, y1 = -(-(xs.max() + 1) // 8) * 8, -(-(ys.max() + 1) // 8) * 8
        crop = rgba[y0:y1, x0:x1]
        arrays[name] = (np.clip(crop, 0, 1) * 255 + 0.5).astype(np.uint8)
        if mask is not None:
            arrays[name + '.mask'] = (np.clip(mask[y0:y1, x0:x1], 0, 1) * 255 + 0.5).astype(np.uint8)
        rects[name] = [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]

    for name in views:
        if name not in active:
            continue
        rgba = one(f'{name}.rgba_')
        if rgba is None:
            continue
        rgba[..., 3] = np.where(rgba[..., 3] > 2 / 255, rgba[..., 3], 0)  # denoiser dust
        alpha = rgba[..., 3]
        mask = None
        tint = one(f'{name}.tint_')
        if tint is not None:
            mask = np.where(alpha > 1e-3, tint[..., 0] / np.maximum(alpha, 1e-3), 0)
        if name == 'shadow':
            # the catcher also records the faint, wide occlusion of the whole robot: drop that floor (as B1)
            s = np.clip((alpha - 0.1) / 0.9, 0, 1) * SHADOW_OPACITY
            rgba = np.zeros_like(rgba)
            rgba[..., 3] = s
        if name == 'body' and desk_top is not None:
            height = one(f'{name}.height_')[..., 0] / np.maximum(alpha, 1e-3)
            below = height < desk_top
            for part, sel in (('body_low', below), ('body_high', ~below)):
                sub = rgba.copy()
                sub[..., 3] = np.where(sel, alpha, 0)
                keep(part, sub, mask)
            if 'body_low' in rects or 'body_high' in rects:
                keep('body', rgba, mask)  # the whole, for the hit box; not packed
            continue
        keep(name, rgba, mask)
    np.savez_compressed(dest, **arrays)
    return rects


def anchors(bpy, rig, robot, to_px, Vector, clip) -> dict:
    """World points of interest, in 4x canvas pixels: the helmet's top and each kit's top as seen (the highest
    point on screen, not in the world: from above, that is towards the back of the helmet), both palms."""
    dg = evaluated()
    ev = robot.evaluated_get(dg)
    me = ev.to_mesh()
    head = robot.vertex_groups['Head'].index
    idx = anchors.head_idx if hasattr(anchors, 'head_idx') else None
    if idx is None:
        idx = [v.index for v in robot.data.vertices if any(g.group == head and g.weight > 0.5 for g in v.groups)]
        anchors.head_idx = idx
    top = min((to_px(ev.matrix_world @ me.vertices[i].co) for i in idx), key=lambda q: q[1])
    ev.to_mesh_clear()
    out = {'head_top': top, 'kit_top': {}}
    for k in ('backpack', 'antenna', 'halo', 'crest'):
        o = bpy.data.objects[f'acc_{k}'].evaluated_get(dg)
        m = o.to_mesh()
        q = min((to_px(o.matrix_world @ v.co) for v in m.vertices), key=lambda q: q[1])
        o.to_mesh_clear()
        out['kit_top'][k] = q if q[1] < top[1] else top
    M = rig.matrix_world
    for side, key in (('L', 'hand_l'), ('R', 'hand_r')):
        a, b = M @ rig.pose.bones[f'Palm2.{side}'].head, M @ rig.pose.bones[f'Middle1.{side}'].head
        out[key] = to_px((a + b) / 2)
    return out


# ======================================================================== pack (plain Python)

def pack() -> None:
    import numpy as np
    from PIL import Image, ImageFilter

    meta = json.loads((BUILD / 'frames.json').read_text())
    ppm1 = meta['px_per_m_4x'] / 4
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.rglob('*'):
        if old.is_file():
            old.unlink()
    # every layer image of every frame: (clip, dir, frame, layer, npz, rect at 4x)
    entries = []
    for clip, c in meta['clips'].items():
        for d, dd in c['dirs'].items():
            for i, fr in enumerate(dd['frames']):
                for layer, rect in fr['layers'].items():
                    if layer == 'body' and 'body_low' in fr['layers']:
                        continue  # seated: drawn as the desk split
                    entries.append((clip, d, i, layer, FRAMES_DIR / f"{fr['key']}.npz", rect))
    uniq, ref = dedupe(entries)
    manifest = {
        'version': 1,
        'camera': {'name': 'l2', 'projection': 'orthographic', 'pitch_deg': meta['view']['pitch'],
                   'yaw_deg': meta['view']['yaw'], 'px_per_m_1x': round(ppm1, 3)},
        'resolutions': {},
        'directions': {d: {'facing_deg': a} for d, a in DIRS},
        'grey': GREY,
        'tint': 'rgb = rgb * (1 - mask + mask * host / grey), per sRGB channel',
        'masked': ['body', 'body_low', 'body_high'] + [f'acc_{k}' for k in ACCESSORIES if k != 'halo'],
        'tinted_whole': ['acc_halo'],
        'shadow_scale': SHADOW_SCALE,
        'draw_order': ['shadow', 'body_low', '(desk)', 'body', 'body_high', 'face_*', 'acc_*', 'item_*'],
        'faces': {f'face_{k}': agent for k, agent in FACES.items()},
        'accessories': [f'acc_{k}' for k in ACCESSORIES],
        'items': [f'item_{k}' for k in ITEMS],
        'seat_point_m': [round(v, 4) for v in meta['seat_point']],
        'desk_top_m': round(meta['desk_top'], 4),
        'clips': {},
    }
    footprint_m = 0.3
    pitch = math.radians(meta['view']['pitch'])

    def s4(p):  # 4x pixels to 1x, a tenth of a pixel is plenty
        return [round(p[0] / 4, 1), round(p[1] / 4, 1)]

    for clip, c in meta['clips'].items():
        mc = {'fps': c['fps'], 'loop': c['loop'], 'hold_last': c['hold_last'], 'frames': c['count'],
              'items': [f'item_{k}' for k in c['items']], 'seated': clip in SEATED, 'dirs': {}}
        for d, dd in c['dirs'].items():
            frames = []
            for fr in dd['frames']:
                a = fr['anchors']
                hb = fr['layers']['body']
                frames.append({
                    # a kit's top only where it rises above the helmet (the backpack never does)
                    'anchors': {'head_top': s4(a['head_top']),
                                'kit_top': {k: s4(v) for k, v in a['kit_top'].items() if s4(v)[1] < s4(a['head_top'])[1]},
                                'hand_l': s4(a['hand_l']), 'hand_r': s4(a['hand_r'])},
                    'hit': [hb[0] / 4, hb[1] / 4, hb[2] / 4, hb[3] / 4],
                    'layers': {}})
            mc['dirs'][d] = {'canvas': [dd['canvas'][0] // 4, dd['canvas'][1] // 4], 'foot': s4(dd['foot']),
                             'footprint': [round(footprint_m * ppm1, 2), round(footprint_m * ppm1 * math.sin(pitch), 2)],
                             **({'seat': s4(dd['seat'])} if 'seat' in dd else {}), 'frames': frames}
        manifest['clips'][clip] = mc
    sizes = {}
    for res in [r for r in RES if str(r) in os.environ.get('SPRITES_RES', '124')]:
        k = 4 // res

        def size(n):  # a unique image's size at this resolution: even, so masks can be stored at half
            rect, layer = uniq[n][2], uniq[n][1]
            if layer == 'shadow':  # rounded up: drawn SHADOW_SCALE times larger, it covers its whole rect
                return -(-rect[2] // (k * SHADOW_SCALE)), -(-rect[3] // (k * SHADOW_SCALE))
            return rect[2] // k, rect[3] // k

        # colour layers and their half-size masks share one set of pages; shadows (smooth, all alpha) get
        # their own, with lossless alpha so their soft falloff doesn't band
        group = {n: 'shadow' if u[1] == 'shadow' else 'color' for n, u in enumerate(uniq)}
        spots = {}
        ms = MASK_SCALE[res]
        packers = {'color': Packer(PAGE, align=ms), 'shadow': Packer(PAGE)}
        for n in sorted(range(len(uniq)), key=lambda n: -size(n)[1]):
            spots[n] = packers[group[n]].add(*size(n))
        for e, n in zip(entries, ref):
            clip, d, i, layer, _, rect = e
            page, x, y = spots[n]
            # one entry per resolution, in the order of manifest['resolutions']
            manifest['clips'][clip]['dirs'][d]['frames'][i]['layers'].setdefault(layer, [None] * len(RES))[RES.index(res)] = \
                [page, x, y, *size(n), rect[0] // k, rect[1] // k]
        cdir = OUT / f'{res}x'
        cdir.mkdir(exist_ok=True)
        q, aq = QUALITY[res]
        pages = {'color': [], 'shadow': []}
        for g, packer in packers.items():
            for p, (pw, ph) in enumerate(packer.sizes()):
                color = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
                mask = np.zeros((ph // ms, pw // ms), np.uint8)
                by_file = {}
                for n, u in enumerate(uniq):
                    if group[n] == g and spots[n][0] == p:
                        by_file.setdefault(u[0], []).append(n)
                for npz, ns in by_file.items():
                    z = np.load(npz)
                    for n in ns:
                        layer = uniq[n][1]
                        _, x, y = spots[n]
                        w, h = size(n)
                        if g == 'shadow':
                            a = z[layer][..., 3]
                            full = np.zeros((h * k * SHADOW_SCALE, w * k * SHADOW_SCALE), np.uint8)
                            full[:a.shape[0], :a.shape[1]] = a
                            sh = Image.fromarray(full, 'L').filter(ImageFilter.GaussianBlur(6))
                            im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
                            im.putalpha(sh.resize((w, h), Image.LANCZOS))
                        else:
                            im = Image.fromarray(z[layer], 'RGBA')
                            if im.size != (w, h):
                                im = im.convert('RGBa').resize((w, h), Image.LANCZOS).convert('RGBA')
                        color.paste(im, (x, y))
                        if layer + '.mask' in z and layer in manifest['masked']:
                            mk = Image.fromarray(z[layer + '.mask'], 'L').filter(ImageFilter.GaussianBlur(2))
                            mk = np.asarray(mk.resize((w // ms, h // ms), Image.LANCZOS))
                            mask[y // ms:y // ms + h // ms, x // ms:x // ms + w // ms] = np.round(mk / 255 * (MASK_LEVELS - 1))
                if g == 'shadow':
                    sf = cdir / f'shadow-{p}.webp'
                    color.save(sf, 'WEBP', quality=10, alpha_quality=100, method=6)
                    pages['shadow'].append({'image': f'{res}x/{sf.name}', 'size': [pw, ph]})
                    continue
                cf, mf = cdir / f'color-{p}.webp', cdir / f'mask-{p}.png'
                color.save(cf, 'WEBP', quality=q, alpha_quality=aq, method=6)
                pm = Image.fromarray(mask, 'P')
                pm.putpalette([round(v * 255 / (MASK_LEVELS - 1)) for v in range(MASK_LEVELS) for _ in range(3)])
                pm.save(mf, 'PNG', optimize=True, bits=4)
                pages['color'].append({'color': f'{res}x/{cf.name}', 'mask': f'{res}x/{mf.name}', 'size': [pw, ph]})
        manifest['resolutions'][f'{res}x'] = {'scale': res, 'load': 'on demand' if res == 4 else 'eager', 'mask_scale': ms,
                                               'pages': pages['color'], 'shadow_pages': pages['shadow']}
        sizes[f'{res}x'] = sum(f.stat().st_size for f in cdir.iterdir())
    for res in RES:  # the sets not packed this time (SPRITES_RES) keep their earlier size
        if f'{res}x' not in sizes and (OUT / f'{res}x').exists():
            sizes[f'{res}x'] = sum(f.stat().st_size for f in (OUT / f'{res}x').iterdir())
    manifest['stats'] = {'layer_images': len(entries), 'unique': len(uniq)}
    mf = OUT / 'sprites.json'
    mf.write_text(json.dumps(manifest, separators=(',', ':')) + '\n')
    sizes['sprites.json'] = mf.stat().st_size
    eager = sizes['1x'] + sizes['2x'] + sizes['sprites.json']
    print('PACKED', {k: f'{v / 1e6:.2f} MB' for k, v in sizes.items()}, f'1x+2x+manifest {eager / 1e6:.2f} MB')


def preview(res: str = '2x', host: str = '#2dd4bf', face: str = 'face_eyes', kit: str = 'acc_antenna') -> Path:
    """Recompose frames from the atlases and sprites.json alone, as the runtime would, into a contact sheet:
    per clip and direction, frames 0 and middle, tinted `host`, with one face and kit and the clip's first item."""
    import numpy as np
    from PIL import Image, ImageDraw

    man = json.loads((OUT / 'sprites.json').read_text())
    pages = man['resolutions'][res]['pages']
    color = [Image.open(OUT / pg['color']).convert('RGBA') for pg in pages]
    shadow = [Image.open(OUT / pg['image']).convert('RGBA') for pg in man['resolutions'][res]['shadow_pages']]
    mask = [Image.open(OUT / pg['mask']).convert('L') for pg in pages]
    scale = man['resolutions'][res]['scale']
    grey = np.array([int(man['grey'][i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    tint = np.array([int(host[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / grey
    tiles = []
    for clip, c in man['clips'].items():
        for d, dd in c['dirs'].items():
            for i in (0, c['frames'] // 2):
                fr = dd['frames'][i]
                W, H = dd['canvas'][0] * scale, dd['canvas'][1] * scale
                canvas = Image.new('RGBA', (W, H), (236, 240, 245, 255))
                order = ['shadow', 'body_low', 'body', 'body_high', face, kit] + c['items'][:1]
                for layer in order:
                    if layer not in fr['layers']:
                        continue
                    pg, x, y, w, h, ox, oy = fr['layers'][layer][list(man['resolutions']).index(res)]
                    im = (shadow if layer == 'shadow' else color)[pg].crop((x, y, x + w, y + h))
                    if layer == 'shadow':
                        im = im.resize((w * man['shadow_scale'], h * man['shadow_scale']), Image.BILINEAR)
                    if layer in man['masked'] or layer in man['tinted_whole']:
                        a = np.asarray(im).astype(np.float32)
                        if layer in man['masked']:
                            ms = man['resolutions'][res]['mask_scale']
                            mk = mask[pg].crop((x // ms, y // ms, (x + w) // ms, (y + h) // ms)).resize((w, h), Image.BILINEAR)
                            m = np.asarray(mk).astype(np.float32)[..., None] / 255
                        else:
                            m = 1.0
                        a[..., :3] = np.clip(a[..., :3] * (1 - m + m * tint), 0, 255)
                        im = Image.fromarray(a.astype(np.uint8), 'RGBA')
                    canvas.alpha_composite(im, (ox, oy))
                dr = ImageDraw.Draw(canvas)
                fx, fy = dd['foot'][0] * scale, dd['foot'][1] * scale
                dr.ellipse((fx - 3, fy - 3, fx + 3, fy + 3), outline=(255, 0, 0))
                hx, hy = fr['anchors']['head_top'][0] * scale, fr['anchors']['head_top'][1] * scale
                dr.line((hx - 6, hy, hx + 6, hy), fill=(0, 120, 255))
                for hand in ('hand_l', 'hand_r'):
                    px, py = fr['anchors'][hand][0] * scale, fr['anchors'][hand][1] * scale
                    dr.ellipse((px - 2, py - 2, px + 2, py + 2), outline=(255, 160, 0))
                hb = [v * scale for v in fr['hit']]
                dr.rectangle((hb[0], hb[1], hb[0] + hb[2], hb[1] + hb[3]), outline=(0, 200, 0))
                dr.text((4, 4), f'{clip} {d} {i}', fill=(40, 40, 40))
                tiles.append(canvas)
    th = max(t.height for t in tiles)
    cols = 8
    rows = -(-len(tiles) // cols)
    tw = max(t.width for t in tiles)
    sheet = Image.new('RGB', (cols * tw, rows * th), (236, 240, 245))
    for n, t in enumerate(tiles):
        sheet.paste(t, ((n % cols) * tw, (n // cols) * th + th - t.height))
    dest = BUILD / f'preview-{res}.png'
    sheet.save(dest)
    return dest


def dedupe(entries) -> tuple[list, list]:
    """Within a clip and direction, a layer that barely changes between frames (a seated robot's head and legs
    while its arms type, most contact shadows) is stored once. Returns the unique images (npz, layer, rect) and,
    per entry, the index of its unique image."""
    import numpy as np
    uniq, ref, seen, cache = [], [], {}, {}

    def load(npz, layer):
        if npz not in cache:
            cache.clear()
            cache[npz] = np.load(npz)
        z = cache[npz]
        a = z[layer][::2, ::2].astype(np.int16)
        m = z[layer + '.mask'][::2, ::2].astype(np.int16) if layer + '.mask' in z else None
        return a, m

    def close(x, y):
        diff = np.abs(x - y)
        return diff.mean() <= DEDUPE_MEAN and np.percentile(diff, 99) <= DEDUPE_P99

    for clip, d, i, layer, npz, rect in entries:
        a, m = load(npz, layer)
        match = None
        for n, a2, m2 in seen.get((clip, d, layer), []):
            if uniq[n][2] == rect and close(a, a2) and (m is None or close(m, m2)):
                match = n
                break
        if match is None:
            match = len(uniq)
            uniq.append((npz, layer, rect))
            seen.setdefault((clip, d, layer), []).append((match, a, m))
        ref.append(match)
    return uniq, ref


class Packer:
    """Shelf packing into pages of at most `size` square; add the tallest images first."""

    def __init__(self, size: int, pad: int = 4, align: int = 2):
        self.size, self.pad, self.align = size, pad, align
        self.pages = []  # per page: shelves [y, h, x], and the used width and height

    def add(self, w: int, h: int) -> tuple[int, int, int]:
        a = self.align  # sizes and places on this grid, so masks stored `align` times smaller line up
        w2, h2 = -(-w // a) * a + self.pad, -(-h // a) * a + self.pad
        for i, pg in enumerate(self.pages):
            for sh in pg['shelves']:
                if h2 <= sh[1] and sh[2] + w2 <= self.size:
                    x = sh[2]
                    sh[2] += w2
                    pg['w'] = max(pg['w'], sh[2])
                    return i, x, sh[0]
            y = pg['shelves'][-1][0] + pg['shelves'][-1][1]
            if y + h2 <= self.size:
                pg['shelves'].append([y, h2, w2])
                pg['w'], pg['h'] = max(pg['w'], w2), y + h2
                return i, 0, y
        self.pages.append({'shelves': [[0, h2, w2]], 'w': w2, 'h': h2})
        return len(self.pages) - 1, 0, 0

    def sizes(self) -> list[tuple[int, int]]:
        return [(-(-pg['w'] // 8) * 8, -(-pg['h'] // 8) * 8) for pg in self.pages]


# ======================================================================== entry

def main() -> None:
    try:
        import bpy  # noqa: F401
    except ImportError:
        if '--pack' not in sys.argv:
            blender = os.environ.get('BLENDER', str(Path.home() / '.local' / 'bin' / 'blender'))
            BUILD.mkdir(parents=True, exist_ok=True)
            with open(BUILD / 'render.log', 'w') as log:
                r = subprocess.run([blender, '-b', '--factory-startup', '--python-exit-code', '1', '-P', __file__],
                                   stdout=log, stderr=subprocess.STDOUT)
            if r.returncode:
                sys.exit(f'sprites: render failed, see {BUILD / "render.log"}')
        pack()
        print('PREVIEW', preview())
        return
    render_all()


if __name__ == '__main__':
    main()
