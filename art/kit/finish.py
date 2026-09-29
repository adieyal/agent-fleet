#!/usr/bin/env python3
"""Build the floor kit (fleet/web/assets/world/kit/) from its sources:

- props from 3D models: art/build/props/ as rendered by art/scripts/render_props.py (on host home);
- Blender pieces: art/build/kit/ as rendered by art/scripts/build_kit.py (on host home), already anchored;
- procedural sprites: footprints and the glow sprites (lamp pools, wall wash, shade glow, floor spill,
  lantern halo), drawn here in their plane and mapped onto the screen by the camera's affine projection.

Writes the WebP tiers, manifest.json (the sprite engine's format plus source, scale and slots) and a contact
sheet. Run with the dev group: uv run --group dev python art/kit/finish.py [contact.jpg]
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

KIT = Path(__file__).resolve().parent
REPO = KIT.parent.parent
BLENDER = REPO / 'art' / 'build' / 'kit'
OUT = REPO / 'fleet' / 'web' / 'assets' / 'world' / 'kit'
B2 = REPO / 'art' / 'bakeoff' / 'B2'

# The canonical camera (artlib.canonical_projection; docs/design/art-direction.md, "Camera"): oblique, yaw 30 deg,
# rays falling at atan(1/2). A world offset (x, y, z) in metres to screen metres (right, down): the engine's projection.js
YAW, DEPRESSION = 30.0, math.degrees(math.atan(0.5))
_y, _t = math.radians(YAW), 0.5
PX = (math.cos(_y), math.sin(_y), 0.0)
PY = (_t * math.sin(_y), -_t * math.cos(_y), -1.0)
CAMERA = {'name': 'canonical', 'projection': 'oblique', 'yaw_deg': YAW, 'depression_deg': round(DEPRESSION, 4),
          'axes_px_per_m': [[round(PX[i], 5), round(PY[i], 5)] for i in range(3)]}
PPM_1X = 941 / 5.486                  # l2's framing, 171.528 px/m
TIERS = (PPM_1X / 2, PPM_1X, PPM_1X * 2)
QUALITY = 88


def plane(p) -> tuple[float, float]:
    x, y, z = p
    return PX[0] * x + PX[1] * y, PY[0] * x + PY[1] * y + PY[2] * z


# --- model props: rendered from the 3D models by art/scripts/render_props.py (floor review 2) ------------------------

MODELS = REPO / 'art' / 'build' / 'props'
SHADOW = 0.62   # the rendered contact shadow's strength: a catcher's full shadow is black, too heavy against l1's


def _shadow(path: Path) -> tuple[Image.Image, tuple[int, int]]:
    """A shadow render, weakened and feathered at its border; returns it and its offset in the render (none: the
    render's own frame is kept, so every tier covers the same area)."""
    im = Image.open(path).convert('RGBA')
    # (the faintest steps go to zero, so the shadow fades out instead of ending in a faint rectangle)
    a = im.getchannel('A').point(lambda v: max(0, int(v * SHADOW) - 3))
    im = Image.new('RGBA', im.size, (38, 48, 70, 0))
    im.putalpha(a)
    # a feathered border: the render's noise can reach its frame, and must not end there in a line
    w, h = im.size
    k = max(3, round(0.06 * min(w, h)))
    ramp = Image.new('L', (w, h))
    ramp.putdata([int(255 * min(1, x / k, (w - 1 - x) / k, y / k, (h - 1 - y) / k)) for y in range(h) for x in range(w)])
    im.putalpha(ImageChops.multiply(im.getchannel('A'), ramp))
    return im, (0, 0)


def model_props() -> dict:
    """The props rendered from models. On a desk, the shadow is laid under the prop in one sprite (there is no floor
    under it in the ground snapshot); on the floor or a wall it is its own ground sprite, `<prop>-shadow`, which the
    layout places with the prop. The desk module becomes the bench's left, middle and right pieces."""
    info = json.loads((MODELS / 'props.json').read_text())
    out = {}
    for name, p in info['props'].items():
        tiers, shadows = [], []
        for t, st in zip(p['tiers'], p.get('shadow') or [None] * len(p['tiers'])):
            spr = Image.open(MODELS / t['file']).convert('RGBA')
            ax, ay = t['anchor_px']
            if st:
                sh, (ox, oy) = _shadow(MODELS / st['file'])
                sx, sy = st['anchor_px'][0] - ox, st['anchor_px'][1] - oy   # the anchor in the trimmed shadow
                if p['on'] == 'desk':
                    left, top = min(0, round(ax - sx)), min(0, round(ay - sy))
                    W = max(spr.width, round(ax - sx) + sh.width) - left
                    H = max(spr.height, round(ay - sy) + sh.height) - top
                    canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
                    canvas.alpha_composite(sh, (round(ax - sx) - left, round(ay - sy) - top))
                    canvas.alpha_composite(spr, (-left, -top))
                    spr, ax, ay = canvas, ax - left, ay - top
                else:
                    shadows.append((t['ppm'], sh, (sx, sy)))
            tiers.append((t['ppm'], spr, (ax, ay)))
        fp = p['footprint']
        common = {'source': 'blender', 'from': f"model {p['from']} (art/scripts/render_props.py)", 'footprint': fp, 'hit': 'alpha',
                  **({'slots': p['slots']} if p.get('slots') else {}), **({'doc': p['doc']} if p.get('doc') else {}),
                  **({'wall': p['wall']} if p.get('wall') else {})}
        names = ['bench-left', 'bench-mid', 'bench-right'] if name == 'desk-module' else [name]
        for n in names:
            out[n] = {**common, 'tiers': [save(n, ppm, im, a) for ppm, im, a in tiers]}
            if name == 'desk-module':
                out[n].update({'layer': 'standing', 'size_m': [p['module_m'], 0.8, 0.74], 'module_m': p['module_m'], 'anchor': p['anchor']})
            if shadows:
                out[n]['shadow'] = f'{n}-shadow'
                out[f'{n}-shadow'] = {'source': 'blender', 'from': common['from'], 'layer': 'ground', 'hit': 'none',
                                      'footprint': [fp[0], fp[1], fp[2], fp[3], fp[4], fp[2] + 0.01],
                                      'tiers': [save(f'{n}-shadow', ppm, im, a) for ppm, im, a in shadows],
                                      'doc': f'the contact shadow of {n}, rendered: place it with the prop'}
    return out


def save(name: str, ppm: float, im: Image.Image, anchor, frames: int = 1) -> dict:
    file = f'{name}@{round(ppm)}.webp'
    im.save(OUT / file, 'WEBP', quality=QUALITY, method=4)
    return {'ppm': round(ppm, 3), 'file': file, 'size': [im.width // frames, im.height],
            'anchor_px': [round(anchor[0], 2), round(anchor[1], 2)], **({'frames': frames} if frames > 1 else {})}


# --- Blender pieces -------------------------------------------------------------------------------

LAYERS = {'pilaster': 'ground', 'wall-cap-x': 'ground', 'wall-cap-y': 'ground', 'wall-corner': 'ground',
          'wall-end-back': 'ground', 'wall-end-left': 'ground', 'slab-front': 'ground', 'slab-side': 'ground',
          'plan-wall': 'ground', 'lift-panel': 'ground'}
# (the lift's doors change, so it stands: a ground change repaints the snapshot; the alcove stands too, around its crate)


def blender_pieces() -> dict:
    info = json.loads((BLENDER / 'pieces.json').read_text())
    out = {}
    for name, piece in info['pieces'].items():
        tiers = []
        for t in piece['tiers']:
            im = Image.open(BLENDER / t['file']).convert('RGBA')
            frames = t.get('frames', 1)
            tiers.append(save(name, t['ppm'], im, t['anchor_px'], frames))
        out[name] = {'source': 'blender', 'from': 'art/scripts/build_kit.py', 'footprint': piece['footprint'],
                     'hit': 'alpha', 'tiers': tiers, 'layer': LAYERS.get(name, 'standing'),
                     **({'slots': piece['slots']} if piece['slots'] else {}), **({'doc': piece['doc']} if piece.get('doc') else {})}
    out['lift']['cells'] = ['shut', 'quarter', 'half', 'three-quarters', 'open']
    return out


# --- procedural: footprints and glow ----------------------------------------------------------------

def on_plane(src: Image.Image, src_ppm: float, origin_m, u_axis, v_axis, ppm: float) -> tuple[Image.Image, tuple[float, float]]:
    """Map an image drawn flat in a plane (its pixel (0, 0) at world `origin_m`, x along world `u_axis`, y along
    `v_axis`, at src_ppm) onto the screen at ppm. Returns the sprite and where world (0, 0, 0) falls in it."""
    # (a clear border first, so resampling fades the image out inside the sprite instead of cutting it at its edge)
    pad = math.ceil(3 / TIERS[0] * src_ppm)   # (three pixels of the coarsest tier: the same area at every tier)
    framed = Image.new('RGBA', (src.width + 2 * pad, src.height + 2 * pad), src.getpixel((0, 0))[:3] + (0,))
    framed.paste(src, (pad, pad))
    src = framed
    origin_m = tuple(o - pad / src_ppm * (u + v) for o, u, v in zip(origin_m, u_axis, v_axis))
    ux, uy = plane(u_axis)
    vx, vy = plane(v_axis)
    ox, oy = plane(origin_m)
    s = ppm / src_ppm
    a, b, c, d = ux * s, vx * s, uy * s, vy * s   # screen = [[a b] [c d]] . src + o
    corners = [(0, 0), (src.width, 0), (0, src.height), (src.width, src.height)]
    xs = [a * x + b * y for x, y in corners]
    ys = [c * x + d * y for x, y in corners]
    X0, Y0 = math.floor(min(xs)), math.floor(min(ys))
    W, H = math.ceil(max(xs)) - X0, math.ceil(max(ys)) - Y0
    det = a * d - b * c
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det   # output pixel (X, Y) shows src = inverse . (X + X0, Y + Y0)
    out = src.transform((W, H), Image.Transform.AFFINE, (ia, ib, ia * X0 + ib * Y0, ic, id_, ic * X0 + id_ * Y0),
                        resample=Image.Resampling.BICUBIC)
    # world (0,0,0) is at screen offset -o (in metres) from the source origin
    return out, (-X0 - ox * ppm, -Y0 - oy * ppm)


def radial(size_px: int, color, inner: float = 0.6) -> Image.Image:
    """A soft disc filling the square: zero at its radius. (PIL's radial_gradient only reaches white in the corners,
    181 at the sides, so a disc from it was cut off by its square and every glow drew a faint rectangle: floor review 2.)"""
    img = Image.new('RGBA', (size_px, size_px), (*color, 0))
    a = Image.new('L', (size_px, size_px))
    c, r = (size_px - 1) / 2, size_px / 2
    a.putdata([int(255 * max(0.0, 1 - math.hypot(x - c, y - c) / r) ** 1.6 * inner) for y in range(size_px) for x in range(size_px)])
    img.putalpha(a)
    return img


def hexrgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


SRC = 300  # px per metre in the flat drawings
BAY_M = 3.6  # a structural bay, the length of repeating pieces


def footprints(angle: float) -> Image.Image:
    """A pair of robot footprints in the floor plane, walking along `angle` (degrees from +x, anticlockwise
    seen from above), centred on the drawing's centre. l2's dark grey prints, soft."""
    side = int(0.8 * SRC)
    img = Image.new('L', (side, side), 0)
    g = ImageDraw.Draw(img)
    for sx, sy in ((-0.1, -0.13), (0.1, 0.13)):   # left foot back, right foot ahead; l2's prints are ~25 cm long
        cx, cy = side / 2 + sx * SRC, side / 2 - sy * SRC
        g.rounded_rectangle((cx - 0.065 * SRC, cy - 0.125 * SRC, cx + 0.065 * SRC, cy + 0.125 * SRC), radius=0.06 * SRC, fill=255)
    img = img.rotate(angle - 90, resample=Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(2))
    out = Image.new('RGBA', img.size, (70, 72, 82, 0))
    out.putalpha(img.point(lambda v: int(v * 0.5)))
    return out


def procedural(module: float) -> dict:
    out = {}
    flat = lambda img, w_m: (img, (-w_m / 2, w_m / 2, 0))  # noqa: E731  (image centred on the anchor, in the floor)

    def floor_sprite(name, img, doc, layer, extra=None):
        w_m = img.width / SRC
        tiers = []
        for ppm in TIERS:
            spr, anchor = on_plane(img, SRC, (-w_m / 2, img.height / SRC / 2, 0), (1, 0, 0), (0, -1, 0), ppm)
            tiers.append(save(name, ppm, spr, anchor))
        half_w, half_d = w_m / 2, img.height / SRC / 2
        out[name] = {'source': 'procedural', 'from': 'art/kit/finish.py', 'layer': layer, 'hit': 'box' if layer != 'light' else 'none',
                     'footprint': [-half_w, -half_d, 0, half_w, half_d, 0.01], 'tiers': tiers, 'doc': doc, **(extra or {})}

    def wall_sprite(name, img, doc, top_m):
        """Drawn on the wall plane (x right, z up), its top edge `top_m` above the anchor, centred on it."""
        w_m = img.width / SRC
        tiers = []
        for ppm in TIERS:
            spr, anchor = on_plane(img, SRC, (-w_m / 2, 0, top_m), (1, 0, 0), (0, 0, -1), ppm)
            tiers.append(save(name, ppm, spr, anchor))
        out[name] = {'source': 'procedural', 'from': 'art/kit/finish.py', 'layer': 'light', 'hit': 'none',
                     'footprint': [-w_m / 2, -0.01, top_m - img.height / SRC, w_m / 2, 0, top_m], 'tiers': tiers, 'doc': doc,
                     'blend': 'lighter'}

    def plane_sprite(name, img, origin, u, v, footprint, doc, layer='ground', extra=None):
        """An image drawn flat in any plane (its pixel (0, 0) at `origin`, x along `u`, y along `v`)."""
        tiers = []
        for ppm in TIERS:
            spr, anchor = on_plane(img, SRC, origin, u, v, ppm)
            tiers.append(save(name, ppm, spr, anchor))
        out[name] = {'source': 'procedural', 'from': 'art/kit/finish.py', 'layer': layer, 'hit': 'none',
                     'footprint': footprint, 'tiers': tiers, 'doc': doc, **(extra or {})}

    def band(length_m, depth_m, strength, power=1.6):
        """A dark gradient, darkest along its top edge (y = 0) and fading over depth_m; uniform along its length,
        so bays placed end to end join without a seam."""
        w, h = int(length_m * SRC), int(depth_m * SRC)
        a = Image.new('L', (w, h))
        a.putdata([int(strength * (1 - y / h) ** power) for y in range(h) for _ in range(w)])
        img = Image.new('RGBA', (w, h), (40, 44, 60, 0))
        img.putalpha(a)
        return img

    # ambient occlusion where the walls meet the floor (floor review 1, point 4): on the floor along the back wall
    # (anchor: the wall's foot at the bay's left end) and along the left wall (anchor: its foot at the bay's front
    # end); and on each wall's foot, fading upwards
    ao = band(BAY_M, 1.1, 120)
    plane_sprite('ao-floor-x', ao, (0, 0, 0), (1, 0, 0), (0, -1, 0), [0, -1.1, 0, BAY_M, 0, 0.01],
                 'occlusion on the floor along the back wall, one bay')
    plane_sprite('ao-floor-y', ao.rotate(0), (0, BAY_M, 0), (0, -1, 0), (1, 0, 0), [0, 0, 0, 1.1, BAY_M, 0.01],
                 'occlusion on the floor along the left wall, one bay')
    foot = band(BAY_M, 0.7, 95).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    plane_sprite('ao-wall-x', foot, (0, 0, 0.7), (1, 0, 0), (0, 0, -1), [0, -0.01, 0, BAY_M, 0, 0.7],
                 'occlusion at the foot of the back wall, one bay')
    plane_sprite('ao-wall-y', foot, (0, 0, 0.7), (0, 1, 0), (0, 0, -1), [0, 0, 0, 0.01, BAY_M, 0.7],
                 'occlusion at the foot of the left wall, one bay')
    # daylight from the windows of the cut-away front wall: long soft patches on the floor, cool against the lamps
    wl, wd = int(1.3 * SRC), int(2.8 * SRC)
    m = Image.new('L', (wl, wd), 0)
    ImageDraw.Draw(m).rectangle((0.2 * SRC, 0.2 * SRC, 1.1 * SRC, 2.6 * SRC), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(0.16 * SRC)).point(lambda v: int(v * 0.5))
    win = Image.new('RGBA', (wl, wd), (236, 242, 255, 0))
    win.putalpha(m)
    floor_sprite('glow-window', win, 'daylight through a window of the cut-away front wall, on the floor', 'ground',
                 {'blend': 'lighter', 'hit': 'none'})

    for a in range(0, 360, 45):
        floor_sprite(f'footprints-{a:03d}', footprints(a), f'a pair of prints walking {a}° from +x (anticlockwise from above)', 'ground')
    floor_sprite('glow-desk-pool', radial(int(1.15 * SRC), hexrgb('#ffcf73'), 0.9),
                 'warm pool under a desk lamp, on the desk top (place at desk height)', 'light', {'blend': 'lighter'})
    floor_sprite('glow-floor-spill', radial(int(3.2 * SRC), hexrgb('#fee095'), 0.28),
                 'low warm spill on the floor in front of an active bench', 'light', {'blend': 'lighter'})
    # contact shadows drawn as one piece, so tiled bench modules don't darken their seams: a whole bench of n seats
    # (anchor: the bench's centre on the floor). (A seated robot's shadow comes with its sprites: robots.js)
    def soft_rect(w_m, d_m, core, blur_m, strength):
        img = Image.new('L', (int((w_m + 0.8) * SRC), int((d_m + 0.8) * SRC)), 0)
        g = ImageDraw.Draw(img)
        g.rounded_rectangle((0.4 * SRC, 0.4 * SRC, (0.4 + w_m) * SRC, (0.4 + d_m) * SRC), radius=core * SRC, fill=strength)
        return img.filter(ImageFilter.GaussianBlur(blur_m * SRC))

    def shadow(a):
        out_img = Image.new('RGBA', a.size, (38, 48, 70, 0))
        out_img.putalpha(a)
        return out_img
    for n in (3, 4):
        a = ImageChops.lighter(soft_rect(n * module + 0.1, 0.78, 0.05, 0.035, 195), soft_rect(n * module + 0.4, 1.1, 0.25, 0.15, 115))
        floor_sprite(f'shadow-bench-{n}', shadow(a), f'the soft contact shadow of a {n}-seat bench, as one piece', 'ground', {'hit': 'none'})
    # sheen: the soft reflection of a ceiling light in l1's satin floor, a wide blurred panel of warm white
    sw, sd = int(2.0 * SRC), int(1.4 * SRC)
    sheen = Image.new('L', (sw, sd), 0)
    ImageDraw.Draw(sheen).rounded_rectangle((0.25 * SRC, 0.3 * SRC, 1.75 * SRC, 1.1 * SRC), radius=0.3 * SRC, fill=255)
    sheen = sheen.filter(ImageFilter.GaussianBlur(0.3 * SRC)).point(lambda v: int(v * 0.07))   # faint: a sheen, not spots
    img = Image.new('RGBA', (sw, sd), (*hexrgb('#fff4e6'), 0))
    img.putalpha(sheen)
    floor_sprite('floor-sheen', img, 'a ceiling light\'s soft reflection in the satin floor; one per bay, idle or not', 'ground',
                 {'blend': 'lighter', 'hit': 'none'})
    # wall washer: brightest just under the fitting, spreading and fading downwards
    ww, wh = int(1.3 * SRC), int(1.8 * SRC)
    wash = Image.new('RGBA', (ww, wh), (*hexrgb('#fed9a1'), 0))
    a = Image.new('L', (ww, wh), 0)
    px = a.load()
    for y in range(wh):
        t = y / wh
        spread = 0.18 + 0.32 * t ** 0.7
        for x in range(ww):
            u = abs(x / ww - 0.5) / spread
            px[x, y] = int(255 * 0.62 * max(0, 1 - u * u) * (1 - t) ** 1.5)
    wash.putalpha(a.filter(ImageFilter.GaussianBlur(4)))
    wall_sprite('glow-wall-wash', wash, 'a wall washer\'s scallop on the plan-wall bay; anchor at the fitting', 0.0)
    halo = radial(int(1.0 * SRC), hexrgb('#8e577b'), 0.7)
    wall_sprite('glow-lantern-halo', halo, 'the lantern\'s magenta halo on the wall behind it (attention only); anchor at its centre', 0.5)
    shade = radial(int(0.3 * SRC), hexrgb('#fefddd'), 0.95)
    wall_sprite('glow-shade', shade, 'a lit lamp shade\'s mouth; anchor at the lamp\'s shade slot', 0.15)
    return out


# --- textures ---------------------------------------------------------------------------------------

def _tileable_noise(size: int, cells: int, seed: int) -> Image.Image:
    """Smooth value noise that wraps: random values on a coarse grid, upscaled bicubically from a 3 x 3 tiling and
    cropped to the middle, so the left edge continues into the right and the top into the bottom."""
    import random
    rnd = random.Random(seed)
    small = Image.new('L', (cells, cells))
    small.putdata([rnd.randrange(256) for _ in range(cells * cells)])
    big = Image.new('L', (cells * 3, cells * 3))
    for i in range(3):
        for j in range(3):
            big.paste(small, (i * cells, j * cells))
    big = big.resize((size * 3, size * 3), Image.Resampling.BICUBIC)
    return big.crop((size, size, 2 * size, 2 * size))


def _surface(size: int, base: tuple[int, int, int], octaves, seed: int) -> Image.Image:
    """A continuous material: a base colour moved by a few octaves of wrapping noise (cells across, strength)."""
    lum = Image.new('F', (size, size), 0.0)
    for k, (cells, strength) in enumerate(octaves):
        n = _tileable_noise(size, cells, seed + k).point(lambda v, s=strength: (v - 128) / 128 * s, 'F')
        lum = _add_f(lum, n)
    px = lum.load()
    out = Image.new('RGB', (size, size))
    op = out.load()
    for y in range(size):
        for x in range(size):
            f = 1 + px[x, y]
            op[x, y] = tuple(max(0, min(255, round(c * f))) for c in base)
    return out


def _add_f(a: Image.Image, b: Image.Image) -> Image.Image:
    pa, pb = a.load(), b.load()
    out = Image.new('F', a.size)
    po = out.load()
    for y in range(a.height):
        for x in range(a.width):
            po[x, y] = pa[x, y] + pb[x, y]
    return out


def textures() -> dict:
    """The floor and the walls as continuous materials (floor review 1, point 3): no tiles or grid, only soft
    large-scale variation and a fine grain, generated to wrap seamlessly over a large repeat (4.8 m of floor, 4 m of
    wall) so the repeat isn't seen. Colours: the concept images' floor and plaster."""
    out = {}
    for name, size, metres, base, octaves, seed in (
        ('floor-tile', 512, 4.8, (224, 222, 228), [(3, 0.022), (9, 0.016), (48, 0.010), (170, 0.008)], 11),
        ('wall-tile', 512, 4.0, (206, 200, 196), [(2, 0.030), (7, 0.014), (60, 0.008), (200, 0.010)], 23),
    ):
        img = _surface(size, base, octaves, seed)
        file = f'{name}.webp'
        img.save(OUT / file, 'WEBP', quality=90, method=4)
        out[name] = {'file': file, 'metres': metres, 'source': 'procedural', 'from': 'art/kit/finish.py',
                     'doc': 'seamless: wraps left to right and top to bottom; no tile grid'}
    return out


# --- contact sheet ----------------------------------------------------------------------------------

def contact(manifest: dict, dest: Path) -> None:
    cells = []
    for name, s in manifest['sprites'].items():
        t = next(t for t in s['tiers'] if abs(t['ppm'] - PPM_1X) < 1) if any(abs(t['ppm'] - PPM_1X) < 1 for t in s['tiers']) else s['tiers'][-1]
        im = Image.open(OUT / t['file']).convert('RGBA')
        if t.get('frames'):
            im = im.crop((0, 0, t['size'][0], t['size'][1]))
        cells.append((name, s['source'], im, t['ppm']))
    cw, ch, cols = 300, 300, 8
    rows = math.ceil(len(cells) / cols)
    sheet = Image.new('RGB', (cw * cols, ch * rows), '#d7ecfd')
    g = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, (name, source, im, ppm) in enumerate(cells):
        x, y = (i % cols) * cw, (i // cols) * ch
        g.rectangle((x, y, x + cw - 1, y + ch - 1), outline='#b9c6d8')
        im = im.copy()
        im.thumbnail((cw - 20, ch - 44))
        bg = Image.new('RGBA', im.size, (215, 236, 253, 255))
        if name.startswith('glow'):
            bg = Image.new('RGBA', im.size, (60, 64, 76, 255))
        bg.alpha_composite(im)
        sheet.paste(bg.convert('RGB'), (x + (cw - im.width) // 2, y + 8 + (ch - 44 - im.height) // 2))
        g.text((x + 8, y + ch - 32), name, fill='#2b3240', font=font)
        g.text((x + 8, y + ch - 18), f'{source}, {round(ppm)} px/m', fill='#5d6675', font=font)
    sheet.save(dest, quality=88)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    sprites = {}
    sprites.update(blender_pieces())
    models = model_props()
    sprites.update(models)
    sprites.update(procedural(models['bench-mid']['module_m']))
    manifest = {
        'version': 1,
        'about': 'The floor kit: see art/kit/README.md. Sprites are anchored at their base centre unless their doc '
                 'says otherwise; footprints and slots are metres from the anchor (x along the back wall, y towards '
                 'it, z up). layer is a hint: ground (in the ground snapshot), standing (sorted), light (additive).',
        'camera': {**CAMERA, 'px_per_m_1x': round(PPM_1X, 3)},
        'scale': SCALE,
        'sprites': sprites,
        'textures': textures(),
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    if len(sys.argv) > 1:
        contact(manifest, Path(sys.argv[1]))
    total = sum(f.stat().st_size for f in OUT.iterdir())
    print(f'kit: {len(sprites)} sprites, {len(list(OUT.iterdir()))} files, {total / 1e6:.2f} MB')


# How the kit's scale was set, and how it measures against the concept images (1672 x 941 px each).
SCALE = {
    'px_per_m_1x': round(PPM_1X, 3),
    'tiers_px_per_m': [round(t, 3) for t in TIERS],
    'basis': "l2.png's framing as fitted by art/scripts/fit_camera.py: 941 px over 5.486 m (171.528 px/m). "
             'Every object is modelled at its real size in metres; the tiers are 0.5x, 1x and 2x that density.',
    'measured': {
        'l2': {'bench_width_px': 930, 'bench_m': '3 desks of 1.8 m (5.4 m x 0.8 m)', 'px_per_m': 171.5,
               'note': 'the bench, desks and robots agree with the fitted camera within a few percent'},
        'l1': {'active_bench_width_px': 380, 'bench_m': 'assumed 4 desks of 1.8 m (7.2 m)', 'px_per_m': 54,
               'lift_door_height_px': 170, 'lift_door_m': 2.3, 'lift_px_per_m': 104,
               'note': 'l1 is not drawn to one scale: measured against its benches, its lift is about twice the '
                       'size real proportions give. The kit keeps real sizes; the floor framing of l1 is about '
                       '45-55 px/m on a 1672 px screen.'},
    },
}

if __name__ == '__main__':
    main()
