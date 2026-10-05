"""Paper-doll sprite atlases of the robot for the 2D runtime (v2: the robot rebuilt from the whole-body model).

    uv run --group dev python art/scripts/build_robot_sprites.py           # render (Blender), then pack
    uv run --group dev python art/scripts/build_robot_sprites.py --pack    # pack the last render only

Environment: SPRITES_ONLY=Clip or Clip:S renders just that; SPRITES_DRY=1 frames the cameras without rendering;
SPRITES_RES=12 packs only those sets. A run resumes: each finished clip and facing is kept in
art/build/robot_sprites/frames/ (delete it to start over), and when the GPU is short of memory (it may be shared) a
frame waits for it, then falls back to the CPU.

The robot: art/motion-test/ (robot_body.py, robot_hands.py, motion_rig.py, render_motion.py), built from the user's
whole-body Hunyuan3D model and posed hands (outside the repo; their libraries in ~/.cache/fleet-motion-test/), with
Mixamo clips (~/.local/state/fleet/renovation/mixamo/) retargeted onto it, the per-clip hand poses and held items
of rebuilds 2 and 3, the seated robot on the floor kit's chair raised to robot_scale.json's seat height. The script
runs itself inside Blender (BLENDER, default ~/.local/bin/blender) to render, then packs with Pillow. Output:
packages/fleet-web/src/fleet_web/static/assets/world/robot/sprites/ (atlases per resolution and sprites.json); the format is documented in
docs/design/robot-sprites.md.

Render (Cycles on the GPU, the canonical camera every Fleet render shares: artlib.canonical_camera, oblique, yaw 30°,
rays falling at atan(1/2), 171.5 px/m at 1x, with bakeoff.py's studio: a soft disk key from the upper left and a dim white_studio_06 fill):
- Every frame is rendered once per layer, each layer a view layer of the same scene: `body` (the robot, its teal
  shell rendered in neutral grey, with its tint mask and, seated, a world-height split at the desk top), `shadow`
  (the contact shadow alone), the faces (`face_eyes`, `face_band`: white, emissive), the host kits (`acc_*`, with
  tint masks) and the items (`item_*`). Overlay layers see the robot as a holdout, so each comes out already cut
  where the body passes in front of it.
- The tint mask is the teal shell's coverage times its share of diffuse and emitted light, so specular highlights
  stay untinted; joints, face, hands, eyes and ear rings are never tinted.
- Four facings, all rendered: the robot is not symmetric in motion (the waving arm, the flask hand).
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
MOTION = ART / 'motion-test'
BUILD = ART / 'build' / 'robot_sprites'
FRAMES_DIR = BUILD / 'frames'
OUT = REPO / 'packages' / 'fleet-web' / 'src' / 'fleet_web' / 'static' / 'assets' / 'world' / 'robot' / 'sprites'

# Directions: the deck's facing (0 towards the door, which is towards the camera side of the room; π towards
# the back wall), as a turn of the robot about +Z.
DIRS = [('S', 0), ('E', 90), ('N', 180), ('W', 270)]
SEATED_DIRS = ['S', 'N']  # the bench is worked facing out of the room, the terminals facing the back wall
ALL = [d for d, _ in DIRS]
# clip: Mixamo (or motion_rig.LAYERED) source, frames, loop, and options:
#   hold: played once and held on its last frame; seated: on the chair (seat anchor, desk split); dirs;
#   pin: walk in place; hand: hand pose; items: item layers; work: work items; slump: (spine, head) degrees;
#   window: seconds of a long clip to loop (keeps a work loop's pace)
CLIPS = {
    'Walking': dict(src='walk-normal', frames=12, loop=True, pin=True),
    'Idle': dict(src='standing-idle', frames=8, loop=True),
    'Wave': dict(src='waving', frames=8, loop=True),
    'Yes': dict(src='nod-yes', frames=8),
    'No': dict(src='shake-no', frames=8),
    'Death': dict(src='dying-back', frames=12, hold=True),
    'ThumbsUp': dict(src='thumbs-up-standing', frames=8, dirs=SEATED_DIRS),
    'Slump': dict(src='sad-idle', frames=6, loop=True),
    'BoxIdle': dict(src='box-idle', frames=6, loop=True, items=['box'], dirs=SEATED_DIRS),
    'BoxWalk': dict(src='box-walk-arc', frames=8, loop=True, pin=True, items=['box']),
    'BookWalk': dict(src='walk-normal', frames=8, loop=True, pin=True, hand='book', items=['book']),
    'SheetWalk': dict(src='walking-reading-phone', frames=8, loop=True, pin=True, items=['sheet'], window=1.2),
    'StandUp': dict(src='sit-to-stand', frames=8, seated=True, dirs=SEATED_DIRS),
    'Sitting': dict(src='stand-to-sit', frames=8, hold=True, seated=True, dirs=SEATED_DIRS),
    'SitIdle': dict(src='sitting-idle', frames=8, loop=True, seated=True, dirs=SEATED_DIRS),
    'Typing': dict(src='typing', frames=8, loop=True, seated=True, dirs=SEATED_DIRS, work='laptop', items=['laptop'],
                   window=1.2),
    'Writing': dict(src='writing-seated', frames=8, loop=True, seated=True, dirs=SEATED_DIRS, work='paper',
                    items=['paper', 'pencil'], window=2.0),
    'SitRead': dict(src='reading-seated', frames=8, loop=True, seated=True, dirs=SEATED_DIRS, items=['book']),
    'Holding': dict(src='sitting-idle', frames=8, loop=True, seated=True, dirs=SEATED_DIRS, work='flask',
                    items=['flask']),
    'SitThumbsUp': dict(src='thumbs-up-sitting', frames=8, seated=True, dirs=SEATED_DIRS),
    'SitSlump': dict(src='sitting-idle', frames=6, loop=True, seated=True, dirs=SEATED_DIRS, slump=(9, 30)),
    'SitNod': dict(src='sit-nod', frames=6, seated=True, dirs=SEATED_DIRS),
    'SitShake': dict(src='sit-shake', frames=6, seated=True, dirs=SEATED_DIRS),
}
# (the nod and shake on the typing and reading bodies, motion_rig.LAYERED type-/read-nod and -shake, are not in the
# set: the 8 MB up-front budget; the runtime plays SitNod / SitShake over any seated base)
SEATED = {c for c, o in CLIPS.items() if o.get('seated')}
HOLD_LAST = {c for c, o in CLIPS.items() if o.get('hold')}
ACCESSORIES = ['backpack', 'antenna', 'halo', 'crest']
ITEMS = ['box', 'book', 'sheet', 'laptop', 'paper', 'pencil', 'flask']
FACES = {'eyes': 'codex', 'band': 'claude'}
GREY = '#cccccc'  # the shell's neutral grey; the runtime multiplies masked pixels by host / GREY
RES = (1, 2, 4)
SAMPLES = {'body': 32, 'acc': 16, 'other': 12}  # per view layer; OIDN cleans up
PAGE = 2048  # atlas page size limit
QUALITY = {1: (42, 28), 2: (31, 22), 4: (80, 70)}  # WebP colour and alpha quality per resolution
SHADOW_SCALE = 4  # shadows are soft blurs: stored at a quarter of their size
MASK_LEVELS = 8
MASK_SCALE = {1: 4, 2: 8, 4: 4}  # a tint mask is stored this many times smaller than its colour layer (it is blurred:
# the shell's colour changes slowly, and the budget needs the bytes)
SHADOW_OPACITY = 0.55  # B2's contact shadows are soft grey, not black
# artlib.canonical_record() (the packer runs outside Blender, so without artlib): the world checks the axes
CAMERA = {'name': 'canonical', 'projection': 'oblique', 'yaw_deg': 30.0, 'depression_deg': 26.5651,
          'axes_px_per_m': [[0.86603, 0.25], [0.5, -0.43301], [0.0, -1.0]]}
DEDUPE_MEAN, DEDUPE_P99 = 1.5, 24  # of 255: a layer this close (mean, and 99th percentile) to one already
# stored in the same clip and direction is reused; render noise alone differs by less


# ======================================================================== render (inside Blender)

def clip_scene(bpy, clip: str, opt: dict):
    """Build one clip's robot in a fresh scene: the retargeted armature with its pieces, hands, held and work items
    and the four kits, all under a turntable empty (the facing). Returns (arm, turntable, groups) where groups maps
    each layer to its objects."""
    sys.path[:0] = [str(MOTION), str(HERE)]
    import render_motion as RM
    import motion_rig as R
    tag = 'r'
    arm, parts, info = RM.robot(opt['src'], tag, pin=opt.get('pin', False), seated=opt.get('seated', False),
                                hand_pose=opt.get('hand'), loop=opt.get('loop', False), slump=opt.get('slump'),
                                work=opt.get('work'), window=opt.get('window'))
    kit = R.kits(arm, parts, tag)
    name = lambda o: o.name.split('.')[0]  # noqa: E731
    dot = next(p for p in parts if name(p) == f'{tag}_back_dot')
    dot2 = dot.copy()  # each face layer its own dot: an object lives in one layer's collection only
    bpy.context.scene.collection.objects.link(dot2)
    dot2.matrix_parent_inverse = dot.matrix_parent_inverse.copy()
    faces = {'eyes': [p for p in parts if name(p) == f'{tag}_eyes'] + [dot2],
             'band': [p for p in parts if name(p) == f'{tag}_band'] + [dot]}
    for p in faces['band'] + faces['eyes']:
        p.hide_render = False
    items = {}
    for k in ITEMS:
        suffix = {'sheet': 'sheet_paper'}.get(k, k)
        items[k] = [p for p in parts if name(p) == f'{tag}_{suffix}']
    carried = {p for v in items.values() for p in v} | {p for v in faces.values() for p in v}
    body = [p for p in parts if p not in carried]
    turn = bpy.data.objects.new('turntable', None)
    bpy.context.scene.collection.objects.link(turn)
    for o in [arm] + [o for o in bpy.data.objects if o.parent is None and o.type == 'MESH' and o is not arm]:
        mw = o.matrix_world.copy()
        o.parent = turn
        o.matrix_world = mw
    groups = {'body': body, **{f'face_{k}': v for k, v in faces.items()},
              **{f'acc_{k}': [o] for k, o in kit.items()}, **{f'item_{k}': v for k, v in items.items() if v}}
    return arm, turn, groups, info


def sprite_materials(bpy, A) -> None:
    """Materials as the sprites need them: every 'host_tint' colour in neutral grey with a 'tint' AOV of its shell
    coverage (the body's teal threshold from its class attribute; flat teal parts whole); hands' teal cuffs black and
    untinted; faces white; a 'height' AOV everywhere for the desk split."""
    grey = A.srgb(GREY)
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        nt = mat.node_tree
        host = nt.nodes.get('host_tint')

        def aov(name, sock=None, value=None):
            n = nt.nodes.new('ShaderNodeOutputAOV')
            n.aov_name = name
            if sock is not None:
                nt.links.new(sock, n.inputs['Value'])
            else:
                n.inputs['Value'].default_value = value
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        nt.links.new(geo.outputs['Position'], sep.inputs['Vector'])
        aov('height', sep.outputs['Z'])
        if host is None:
            continue
        if mat.name.startswith('hand_'):  # a posed hand's cuff: never tinted, so black like the mitt
            host.outputs[0].default_value = A.srgb('#131717')
            continue
        host.outputs[0].default_value = grey
        at = next((n for n in nt.nodes if n.type == 'ATTRIBUTE' and n.attribute_name == 'cls'), None)
        if at is None:
            aov('tint', value=1.0)
            continue
        # the body: teal where R wins, minus where black or glow are drawn over it (as body_material's mixes)
        sc = nt.nodes.new('ShaderNodeSeparateColor')
        nt.links.new(at.outputs['Color'], sc.inputs[0])

        def sharp(sock):
            mr = nt.nodes.new('ShaderNodeMapRange')
            mr.inputs['From Min'].default_value, mr.inputs['From Max'].default_value = 0.47, 0.53
            nt.links.new(sock, mr.inputs['Value'])
            return mr.outputs['Result']

        def mul(a, b, invert_b=False):
            if invert_b:
                inv = nt.nodes.new('ShaderNodeMath')
                inv.operation = 'SUBTRACT'
                inv.inputs[0].default_value = 1.0
                nt.links.new(b, inv.inputs[1])
                b = inv.outputs[0]
            m = nt.nodes.new('ShaderNodeMath')
            m.operation = 'MULTIPLY'
            nt.links.new(a, m.inputs[0])
            nt.links.new(b, m.inputs[1])
            return m.outputs[0]
        t = mul(mul(sharp(sc.outputs[0]), sharp(sc.outputs[2]), True), sharp(at.outputs['Alpha']), True)
        aov('tint', t)
    for m in ('mat_face', 'mat_eye'):  # the faces: white emissive masks, coloured by the runtime
        mat = bpy.data.materials.get(m)
        if mat:
            b = mat.node_tree.nodes['Principled BSDF']
            for n in mat.node_tree.nodes:
                if n.type == 'RGB':
                    n.outputs[0].default_value = (1, 1, 1, 1)
            b.inputs['Emission Strength'].default_value = 1.5


def render_all() -> None:
    import bpy
    sys.path[:0] = [str(HERE), str(MOTION)]
    import artlib as A
    import bakeoff as K  # the studio and the framing; the camera angle is the floor's
    from mathutils import Vector
    import render_motion as RM

    A.require_blender()
    only = os.environ.get('SPRITES_ONLY')  # e.g. "Idle:S" while iterating
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {'seat_point': [0.0, 0.0, RM.SEAT_H], 'desk_top': RM.W.DESK_H, 'seat_floor': 0.0,
                'bench': RM.BENCH, 'height_m': None, 'px_per_m_4x': K.PX_PER_M * 4, 'view': dict(RM.FLOOR_VIEW),
                'clips': {}}
    for clip, opt in CLIPS.items():
        dirs = opt.get('dirs', ALL)
        count, loop = opt['frames'], opt.get('loop', False)
        manifest['clips'][clip] = {'loop': loop, 'hold_last': clip in HOLD_LAST, 'count': count,
                                   'items': opt.get('items', []), 'src': opt['src'], 'dirs': {}}
        todo = [d for d in dirs if not (only and only not in (clip, f'{clip}:{d}'))]
        cached = {d: FRAMES_DIR / f'{clip}_{d}.json' for d in dirs}
        for d in dirs:
            if cached[d].exists():
                manifest['clips'][clip]['dirs'][d] = json.loads(cached[d].read_text())
        redo = os.environ.get('SPRITES_LAYERS')  # re-render only these layers of finished frames, e.g. face_eyes
        todo = [d for d in todo if not cached[d].exists()] if not redo else [d for d in todo if cached[d].exists()]
        meta_file = FRAMES_DIR / f'{clip}.meta.json'
        if not todo:
            if meta_file.exists():
                manifest['clips'][clip] |= json.loads(meta_file.read_text())
            print('cached', clip, flush=True)
            continue
        A.reset()
        K.VIEW.update(pitch=RM.FLOOR_VIEW['pitch'], yaw=RM.FLOOR_VIEW['yaw'])   # (the studio's key light follows it)
        arm, turn, groups, info = clip_scene(bpy, clip, opt)
        manifest['height_m'] = RM.R.body_meta()['height_m']
        scene = bpy.context.scene
        sprite_materials(bpy, A)
        fr = list(RM.R.frames(arm))
        f0, f1 = fr[0], fr[-1]
        if clip in HOLD_LAST or not loop:
            times = [f0 + (f1 - f0) * i / (count - 1) for i in range(count)]
        else:
            times = [f0 + (f1 - f0) * i / count for i in range(count)]
        fps = (count - 1 if not loop else count) / (max(1, f1 - f0) / 30)  # Mixamo clips are 30 fps
        meta = {'fps': round(fps, 3), 'info': info}
        meta_file.write_text(json.dumps(meta, default=str))
        manifest['clips'][clip] |= meta
        views, catcher, tmp = setup_layers(bpy, A, K, scene, groups, opt.get('seated', False))
        right, down, _ = A.canonical_axes()
        watched = groups['body'] + [o for k, v in groups.items() if k.startswith(('acc_', 'item_')) for o in v]
        for d in todo:
            turn.rotation_euler.z = math.radians(dict(DIRS)[d])
            pts = []
            for t in times:
                scene.frame_set(int(t), subframe=t - int(t))
                pts += points(bpy, watched)
            floor = [Vector((p.x, p.y, 0.0)) for p in pts]
            ref = Vector((0, 0, 0))
            ppm = round(K.PX_PER_M * 4, 3)
            framed = A.canonical_frame(scene, pts + floor, ppm, 0.25, 'sprite_cam', grid=8)   # (8: the crop grid, see collect)
            (w4, h4), (u0, v0) = framed['size'], framed['origin']
            print('canvas', clip, d, w4, h4, flush=True)
            if os.environ.get('SPRITES_DRY'):
                continue

            def to_px(p):
                return [round((p.dot(right) - u0) * ppm, 2), round((p.dot(down) - v0) * ppm, 2)]

            frames = []
            for i, t in enumerate(times):
                scene.frame_set(int(t), subframe=t - int(t))
                active = {'body', 'shadow', 'face_eyes', 'face_band', *[f'acc_{k}' for k in ACCESSORIES],
                          *[f'item_{k}' for k in opt.get('items', [])]}
                if redo:
                    active = set(redo.split(','))
                for name, vl in views.items():
                    vl.use = name in active
                here = points(bpy, watched)
                px = [to_px(q) for q in here + [Vector((q.x, q.y, 0.0)) for q in here]]
                pad = 0.25 * ppm
                x0, x1 = min(q[0] for q in px) - pad, max(q[0] for q in px) + pad
                y0, y1 = min(q[1] for q in px) - pad, max(q[1] for q in px) + pad
                r = scene.render
                r.use_border, r.use_crop_to_border = True, False
                r.border_min_x, r.border_max_x = max(0.0, x0 / w4), min(1.0, x1 / w4)
                r.border_min_y, r.border_max_y = max(0.0, 1 - y1 / h4), min(1.0, 1 - y0 / h4)
                render_frame(bpy, scene, tmp)
                key = f'{clip}_{d}_{i}'
                split = RM.W.DESK_H if opt.get('seated') else None
                if redo:  # merge the new layers into the frame's stored ones
                    import numpy as np
                    old = dict(np.load(FRAMES_DIR / f'{key}.npz'))
                    saved = collect(bpy, tmp, views, active, split, tmp / 'redo.npz')
                    new = dict(np.load(tmp / 'redo.npz'))
                    for k in [k for k in old if k.split('.')[0] in active]:
                        del old[k]
                    np.savez_compressed(FRAMES_DIR / f'{key}.npz', **(old | new))
                    prev = manifest['clips'][clip]['dirs'][d]['frames'][i]
                    prev['layers'] = {k: v for k, v in prev['layers'].items() if k not in active} | saved
                    frames.append(prev)
                    print('redo', key, list(saved), flush=True)
                    continue
                saved = collect(bpy, tmp, views, active, split, FRAMES_DIR / f'{key}.npz')
                frames.append({'key': key, 'layers': saved, 'anchors': anchors(bpy, arm, groups, to_px)})
                print('frame', key, len(saved), flush=True)
            if redo:
                assert manifest['clips'][clip]['dirs'][d]['canvas'] == [w4, h4], (clip, d)  # same framing as before
            dd = {'canvas': [w4, h4], 'foot': to_px(ref), 'frames': frames,
                  **({'seat': to_px(Vector((0, 0, RM.SEAT_H)))} if opt.get('seated') else {})}
            manifest['clips'][clip]['dirs'][d] = dd
            cached[d].write_text(json.dumps(dd))
    (BUILD / 'frames.json').write_text(json.dumps(manifest, default=str))
    print('RENDERED', BUILD / 'frames.json')


def setup_layers(bpy, A, K, scene, groups: dict, seated: bool):
    """Collections, lights and view layers for one clip's scene (see render_all's notes in the module docstring)."""
    def collection(name, objs):
        c = bpy.data.collections.new(name)
        scene.collection.children.link(c)
        for o in objs:
            for old in list(o.users_collection):
                old.objects.unlink(o)
            c.objects.link(o)
        return c

    layers = {name: collection(name, objs) for name, objs in groups.items()}
    rest = [o for o in scene.collection.objects if o.type == 'MESH']  # anything left over stays out of the render
    for o in rest:
        o.hide_render = True
    K.shadow_catcher()
    catcher = bpy.data.objects['catcher']
    layers['shadow'] = collection('catcher', [catcher])
    K.studio(__import__('mathutils').Vector((0, 0, 0.6)))
    key = collection('key_light', [bpy.data.objects['key']])
    A.light('contact', 'AREA', (0, 0, 10.0), 3000, size=5.0)
    contact = collection('contact_light', [bpy.data.objects['contact']])
    c = scene.cycles
    c.samples = SAMPLES['body']
    c.use_denoising, c.denoiser = True, 'OPTIX'
    c.adaptive_threshold, c.adaptive_min_samples = 0.02, 4
    c.max_bounces, c.diffuse_bounces, c.glossy_bounces, c.transmission_bounces, c.transparent_max_bounces = 6, 2, 2, 4, 4
    scene.render.film_transparent = True
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
        vl.use_sky = name != 'shadow'
        for cname, coll in layers.items():
            lc = vl.layer_collection.children[coll.name]
            lc.exclude, lc.holdout, lc.indirect_only = True, False, False
            if cname == name:
                lc.exclude = False
            elif cname == 'body' and name != 'body':
                lc.exclude = False
                if name == 'shadow':
                    lc.indirect_only = True
                else:
                    lc.holdout = True
            elif name == 'shadow' and cname.startswith('item_'):
                lc.exclude, lc.indirect_only = False, True  # held items cast their shadow too
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
    everything = scene.view_layers.new('everything')
    everything.use = False
    everything.layer_collection.children['catcher'].exclude = True
    global EVAL
    EVAL = everything
    return views, catcher, tmp


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


def anchors(bpy, arm, groups: dict, to_px) -> dict:
    """World points of interest, in 4x canvas pixels: the helmet's top and each kit's top as seen (the highest point
    on screen, not in the world: from above, that is towards the back of the helmet), and both hands' centres."""
    dg = evaluated()

    def top_of(objs):
        best = None
        for o in objs:
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            vs = me.vertices
            q = min((to_px(ev.matrix_world @ vs[i].co) for i in range(0, len(vs), 3)), key=lambda q: q[1])
            ev.to_mesh_clear()
            best = q if best is None or q[1] < best[1] else best
        return best

    def centre(o):
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        vs = me.vertices
        pts = [ev.matrix_world @ vs[i].co for i in range(0, len(vs), 7)]
        ev.to_mesh_clear()
        return to_px(sum(pts, pts[0] * 0) / len(pts))

    name = lambda o: o.name.split('.')[0]  # noqa: E731
    head = [o for o in groups['body'] if name(o) in ('r_head', 'r_ear_L', 'r_ear_R')]
    top = top_of(head)
    out = {'head_top': top, 'kit_top': {}}
    for k in ('backpack', 'antenna', 'halo', 'crest'):
        q = top_of(groups[f'acc_{k}'])
        out['kit_top'][k] = q if q[1] < top[1] else top
    for side, key in (('L', 'hand_l'), ('R', 'hand_r')):
        hand = next(o for o in groups['body'] if '_hand_' in o.name and name(o).endswith(f'_{side}'))
        out[key] = centre(hand)
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
    # a partial render packs what it has; a clip or facing dropped from CLIPS since it was rendered is left out
    meta['clips'] = {k: c | {'dirs': {d: dd for d, dd in c['dirs'].items() if d in CLIPS[k].get('dirs', ALL)}}
                     for k, c in meta['clips'].items() if c['dirs'] and k in CLIPS}
    entries = []
    for clip, c in meta['clips'].items():
        if not c['dirs']:
            continue
        for d, dd in c['dirs'].items():
            for i, fr in enumerate(dd['frames']):
                for layer, rect in fr['layers'].items():
                    if layer == 'body' and 'body_low' in fr['layers']:
                        continue  # seated: drawn as the desk split
                    entries.append((clip, d, i, layer, FRAMES_DIR / f"{fr['key']}.npz", rect))
    uniq, ref = dedupe(entries)
    manifest = {
        'version': 2,
        'camera': CAMERA | {'px_per_m_1x': round(ppm1, 3)},
        'robot': {'height_m': meta['height_m'], 'source': 'art/motion-test (whole-body model, Mixamo clips)'},
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
        # the seated robot's furniture: the kit's chair with its gas lift raised to seat_point_m's height, its centre
        # chair_behind_m behind the seat point, and the desk's far edge desk_edge_ahead_m ahead of it (robot frame)
        'seat_furniture': {'seat_height_m': round(meta['seat_point'][2], 4), 'chair_behind_m': meta['bench']['chair_y'],
                           'desk_edge_ahead_m': meta['bench']['desk_edge'], 'desk_top_m': round(meta['desk_top'], 4)},
        'clips': {},
    }
    footprint_m = 0.3
    depression = math.radians(CAMERA['depression_deg'])

    def s4(p):  # 4x pixels to 1x, a tenth of a pixel is plenty
        return [round(p[0] / 4, 1), round(p[1] / 4, 1)]

    for clip, c in meta['clips'].items():
        mc = {'fps': c['fps'], 'loop': c['loop'], 'hold_last': c['hold_last'], 'frames': c['count'],
              'items': [f'item_{k}' for k in c['items']], 'seated': clip in SEATED, 'source': c['src'], 'dirs': {}}
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
                             'footprint': [round(footprint_m * ppm1, 2), round(footprint_m * ppm1 * math.tan(depression), 2)],
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
                            mw, mh = -(-w // ms), -(-h // ms)  # rounded up: the packer reserves whole mask cells
                            mk = np.asarray(mk.resize((mw, mh), Image.LANCZOS))
                            mask[y // ms:y // ms + mh, x // ms:x // ms + mw] = np.round(mk / 255 * (MASK_LEVELS - 1))
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


class Composer:
    """Recomposes frames from the atlases and sprites.json alone, as the runtime does: tint through the mask,
    shadows scaled up, layers at their offsets in the frame's canvas."""

    def __init__(self, res: str):
        import numpy as np
        from PIL import Image
        self.np, self.Image = np, Image
        self.man = man = json.loads((OUT / 'sprites.json').read_text())
        self.res, self.ri = res, list(man['resolutions']).index(res)
        r = man['resolutions'][res]
        self.color = [Image.open(OUT / pg['color']).convert('RGBA') for pg in r['pages']]
        self.mask = [Image.open(OUT / pg['mask']).convert('L') for pg in r['pages']]
        self.shadow = [Image.open(OUT / pg['image']).convert('RGBA') for pg in r['shadow_pages']]
        self.scale, self.ms = r['scale'], r['mask_scale']
        self.grey = np.array([int(man['grey'][i:i + 2], 16) for i in (1, 3, 5)], np.float32)

    def frame(self, clip: str, d: str, i: int, host: str, layers: list[str], bg=(0, 0, 0, 0), face='#7ce7ff'):
        np, Image, man = self.np, self.Image, self.man
        dd = man['clips'][clip]['dirs'][d]
        fr = dd['frames'][i]
        canvas = Image.new('RGBA', (dd['canvas'][0] * self.scale, dd['canvas'][1] * self.scale), bg)
        tint = np.array([int(host[k:k + 2], 16) for k in (1, 3, 5)], np.float32) / self.grey
        for layer in layers:
            if layer not in fr['layers']:
                continue
            pg, x, y, w, h, ox, oy = fr['layers'][layer][self.ri]
            im = (self.shadow if layer == 'shadow' else self.color)[pg].crop((x, y, x + w, y + h))
            if layer == 'shadow':
                im = im.resize((w * man['shadow_scale'], h * man['shadow_scale']), Image.BILINEAR)
            if layer in man['faces']:  # white emissive: multiplied by the agent's colour
                a = np.asarray(im).astype(np.float32)
                a[..., :3] *= np.array([int(face[k:k + 2], 16) for k in (1, 3, 5)], np.float32) / 255
                im = Image.fromarray(a.astype(np.uint8), 'RGBA')
            elif layer in man['masked'] or layer in man['tinted_whole']:
                a = np.asarray(im).astype(np.float32)
                m = 1.0
                if layer in man['masked']:
                    ms = self.ms
                    mk = self.mask[pg].crop((x // ms, y // ms, (x + w) // ms, (y + h) // ms)).resize((w, h), Image.BILINEAR)
                    m = np.asarray(mk).astype(np.float32)[..., None] / 255
                a[..., :3] = np.clip(a[..., :3] * (1 - m + m * tint), 0, 255)
                im = Image.fromarray(a.astype(np.uint8), 'RGBA')
            canvas.alpha_composite(im, (ox, oy))
        return canvas


def preview(res: str = '2x', host: str = '#2dd4bf', face: str = 'face_eyes', kit: str = 'acc_antenna') -> Path:
    """A contact sheet recomposed from the atlases: per clip and direction, frames 0 and middle, tinted `host`,
    with one face and kit and the clip's first item, and its anchors drawn over it."""
    from PIL import ImageDraw

    comp = Composer(res)
    man, scale = comp.man, comp.scale
    tiles = []
    for clip, c in man['clips'].items():
        for d, dd in c['dirs'].items():
            for i in (0, c['frames'] // 2):
                fr = dd['frames'][i]
                order = ['shadow', 'body_low', 'body', 'body_high', face, kit] + c['items']
                canvas = comp.frame(clip, d, i, host, order, bg=(236, 240, 245, 255),
                                    face='#7ce7ff' if face == 'face_eyes' else '#ff8f6b')
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
    from PIL import Image
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


def _grid(tiles, cols, bg=(236, 240, 245)):
    from PIL import Image
    th, tw = max(t.height for t in tiles), max(t.width for t in tiles)
    rows = -(-len(tiles) // cols)
    sheet = Image.new('RGB', (cols * tw, rows * th), bg)
    for n, t in enumerate(tiles):
        sheet.paste(t, ((n % cols) * tw + (tw - t.width) // 2, (n // cols) * th + th - t.height))
    return sheet


def mix(a: str, b: str, k: float) -> str:
    ca, cb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * k):02x}' for x, y in zip(ca, cb))


def faces_and_kits(res: str = '2x') -> Path:
    """Idle, facing S and N: both faces with each of the four kits in the four fixed host colours, then the normal,
    stalled and resting looks (motion.js tone), recomposed from the atlases as the runtime draws them."""
    from PIL import ImageDraw
    comp = Composer(res)
    agents = {'face_eyes': ('codex', '#7ce7ff'), 'face_band': ('claude', '#ff8f6b')}
    hosts = ['#ff9340', '#2dd4bf', '#a78bfa', '#facc15']
    tiles = []

    def tile(d, host, face, kit, label, face_col=None, clip='Idle'):
        order = ['shadow', 'body', face, f'acc_{kit}']
        im = comp.frame(clip, d, 0, host, order, bg=(236, 240, 245, 255), face=face_col or agents[face][1])
        ImageDraw.Draw(im).text((4, 4), label, fill=(40, 40, 40))
        return im
    for face, (agent, _) in agents.items():
        for kit, host in zip(ACCESSORIES, hosts):
            for d in ('S', 'N'):
                tiles.append(tile(d, host, face, kit, f'{agent} {kit} {d}'))
    for face, (agent, colour) in agents.items():
        for look, body, fc in (('normal', '#2dd4bf', colour), ('stalled', mix('#2dd4bf', '#475163', 0.55), mix(colour, '#1b2333', 0.6)),
                               ('resting', '#2dd4bf', mix(colour, '#1b2333', 0.35))):
            tiles.append(tile('S', body, face, 'antenna', f'{agent} {look}', fc))
    dest = BUILD / 'faces-kits.png'
    _grid(tiles, 8).save(dest)
    return dest


def death_sequence(res: str = '2x') -> Path:
    """Every frame of Death, in each facing, one row per facing."""
    comp = Composer(res)
    c = comp.man['clips']['Death']
    tiles = [comp.frame('Death', d, i, '#ff9340', ['shadow', 'body', 'face_eyes', 'acc_antenna'], bg=(236, 240, 245, 255))
             for d in c['dirs'] for i in range(c['frames'])]
    dest = BUILD / 'death.png'
    _grid(tiles, c['frames']).save(dest)
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

    def close(x, y, layer=''):
        diff = np.abs(x - y)
        k = 3 if layer == 'shadow' else 1  # shadows are soft blurs stored at a quarter size: small changes vanish
        return diff.mean() <= DEDUPE_MEAN * k and np.percentile(diff, 99) <= DEDUPE_P99 * k

    for clip, d, i, layer, npz, rect in entries:
        a, m = load(npz, layer)
        match = None
        for n, a2, m2 in seen.get((clip, d, layer), []):
            if uniq[n][2] == rect and close(a, a2, layer) and (m is None or close(m, m2)):
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
        print('FACES', faces_and_kits())
        print('DEATH', death_sequence())
        return
    render_all()


if __name__ == '__main__':
    main()
