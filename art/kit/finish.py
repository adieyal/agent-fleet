#!/usr/bin/env python3
"""Build the floor kit (fleet/web/assets/world/kit/) from its three sources:

- AI props: the kept generations in raw/ (see generate.sh), trimmed, split where one generation drew several
  objects, scaled from each object's real size, given a soft contact shadow, and anchored at their base centre;
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
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

KIT = Path(__file__).resolve().parent
REPO = KIT.parent.parent
RAW = KIT / 'raw'
BLENDER = REPO / 'art' / 'build' / 'kit'
OUT = REPO / 'fleet' / 'web' / 'assets' / 'world' / 'kit'
B2 = REPO / 'art' / 'bakeoff' / 'B2'

PITCH, YAW = 44.5, 21.25
_p, _y = math.radians(PITCH), math.radians(YAW)
# a world offset (x, y, z) in metres to screen metres (right, down): the engine's projection.js
PX = (math.cos(_y), math.sin(_y), 0.0)
PY = (math.sin(_p) * math.sin(_y), -math.sin(_p) * math.cos(_y), -math.cos(_p))
PPM_1X = 941 / 5.486                  # l2's framing, 171.528 px/m
TIERS = (PPM_1X / 2, PPM_1X, PPM_1X * 2)
QUALITY = 88


def plane(p) -> tuple[float, float]:
    x, y, z = p
    return PX[0] * x + PX[1] * y, PY[0] * x + PY[1] * y + PY[2] * z


def box_rect(b) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) in screen metres of a world box [x0, y0, z0, x1, y1, z1]."""
    pts = [plane((x, y, z)) for x in (b[0], b[3]) for y in (b[1], b[4]) for z in (b[2], b[5])]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


# --- AI props -------------------------------------------------------------------------------------

@dataclass
class Prop:
    raw: str                         # generation (raw/<raw>.png)
    size: tuple[float, float, float]  # real width (x), depth (y), height (z), metres
    fit: str = 'w'                   # scale by the image's width ('w') or height ('h') against the box's
    part: int | None = None          # which object, when the generation drew several (row-major order)
    parts: int = 1                   # how many objects that generation drew
    rows: int = 1
    base: float = 0.0                # anchor height within the box (the lantern hangs from its centre)
    shadow: float = 0.28             # contact shadow opacity; 0 for none
    slots: dict = field(default_factory=dict)
    doc: str = ''


PROPS: dict[str, Prop] = {
    'bench': Prop('bench-v2', (5.4, 0.8, 0.74), doc='three desks end to end; top empty; no lamps, no chairs',
                  slots={'seats': [[-1.8 + 1.8 * i, 0.54, 0.47] for i in range(3)],
                         'lamps': [[-2.52 + 1.8 * i, 0.28, 0.74] for i in range(3)],
                         'desk_top': [[-1.8 + 1.8 * i, 0, 0.74] for i in range(3)]}),
    'terminal-desk': Prop('terminal-desk', (1.6, 0.8, 0.74), doc='one desk with monitor and keyboard',
                          slots={'seat': [0, -0.55, 0.47], 'screen': [0, 0.2, 1.05]}),
    'chair-back': Prop('chairs', (0.64, 0.64, 1.0), 'h', part=0, parts=2, doc='office chair, near side of a desk, seen from behind'),
    'chair-front': Prop('chairs', (0.64, 0.64, 1.0), 'h', part=1, parts=2, doc='office chair, far side of a desk, facing the viewer'),
    'lamp': Prop('lamp', (0.2, 0.2, 0.46), 'h', shadow=0.2, doc='desk lamp, switched off (its light is glow-* sprites)',
                 slots={'shade': [-0.16, -0.08, 0.36]}),
    'laptop': Prop('desk-props', (0.33, 0.24, 0.22), part=0, parts=8, rows=2, shadow=0.2),
    'pen-pot': Prop('desk-props', (0.09, 0.09, 0.2), 'h', part=1, parts=8, rows=2, shadow=0.2),
    'paper-stack': Prop('desk-props', (0.3, 0.21, 0.05), part=2, parts=8, rows=2, shadow=0.15),
    'sketch': Prop('desk-props', (0.32, 0.22, 0.01), part=3, parts=8, rows=2, shadow=0.1),
    'mug': Prop('desk-props', (0.11, 0.08, 0.1), part=4, parts=8, rows=2, shadow=0.2),
    'desk-plant': Prop('desk-props', (0.1, 0.1, 0.16), part=5, parts=8, rows=2, shadow=0.2),
    'books': Prop('desk-props', (0.25, 0.17, 0.08), part=6, parts=8, rows=2, shadow=0.2),
    'paper-tray': Prop('desk-props', (0.34, 0.26, 0.13), part=7, parts=8, rows=2, shadow=0.2),
    'plant-tall': Prop('plants', (0.45, 0.45, 1.4), 'h', part=0, parts=3, doc='square concrete planter, as l2'),
    'plant-bush': Prop('plants', (0.42, 0.42, 0.95), 'h', part=1, parts=3),
    'plant-small': Prop('plants', (0.2, 0.2, 0.42), 'h', part=2, parts=3),
    'shelf': Prop('shelf-v2', (1.2, 0.4, 1.8), 'h', doc='against the back wall; binders'),
    'book-cart': Prop('book-cart', (0.9, 0.45, 1.05), 'h', doc="the librarian's cart"),
    'librarian-desk': Prop('librarian-desk', (1.5, 0.7, 0.95), doc="the librarian's desk with card drawers"),
    'podium': Prop('podium', (0.9, 0.65, 1.1), doc="the orchestrator's podium"),
    'whiteboard': Prop('whiteboard', (1.5, 0.5, 1.9), 'h', doc='the briefing board, on castors'),
    'question-desk': Prop('question-desk', (1.2, 0.7, 0.9), doc='with its "?" tent card; the lantern hangs over it',
                          slots={'lantern': [0, 0.1, 2.0], 'card': [0.1, 0.05, 0.85]}),
    'crate': Prop('crate', (0.7, 0.7, 0.7), doc='the waiting crate, hourglass on its front'),
    # (l2's diamond is ~70 px wide at its 171.5 px/m)
    'lantern': Prop('lantern', (0.36, 0.36, 0.66), base=-0.33, shadow=0, doc='the attention lantern: magenta only here; '
                    'anchor at the diamond centre, cable above; the front facet is blank for the glyph',
                    slots={'glyph': [0, -0.1, 0.0], 'cable_top': [0, 0, 1.2]}),
}


def components(im: Image.Image, n: int, rows: int) -> list[tuple[int, int, int, int]]:
    """The bounding boxes of the n largest separate objects in a generation, row-major: alpha closed over small
    gaps (chair castors, pens) and flood-filled on a quarter-size grid."""
    k = 4
    a = im.getchannel('A').point(lambda v: 255 if v > 40 else 0).reduce(k).point(lambda v: 255 if v > 0 else 0)
    a = a.filter(ImageFilter.MaxFilter(5))
    w, h = a.size
    px = a.load()
    seen = bytearray(w * h)
    boxes = []
    for y0 in range(h):
        for x0 in range(w):
            if seen[y0 * w + x0] or not px[x0, y0]:
                continue
            stack, box, count = [(x0, y0)], [x0, y0, x0, y0], 0
            seen[y0 * w + x0] = 1
            while stack:
                x, y = stack.pop()
                count += 1
                box = [min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y)]
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and px[nx, ny]:
                        seen[ny * w + nx] = 1
                        stack.append((nx, ny))
            boxes.append((count, [box[0] * k, box[1] * k, (box[2] + 1) * k, (box[3] + 1) * k]))
    boxes = [b for _, b in sorted(boxes, reverse=True)[:n]]
    if len(boxes) < n:
        sys.exit(f'finish: expected {n} objects, found {len(boxes)}')
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    per = math.ceil(n / rows)
    ordered = []
    for r in range(rows):
        ordered += sorted(boxes[r * per:(r + 1) * per], key=lambda b: b[0])
    return [tuple(b) for b in ordered]


def trim(im: Image.Image) -> Image.Image:
    return im.crop(im.getchannel('A').point(lambda a: 255 if a > 8 else 0).getbbox())


def contact_shadow(size: tuple[float, float, float], ppm: float) -> tuple[Image.Image, tuple[float, float]]:
    """A soft shadow of the footprint on the floor: the base rectangle projected, blurred. Returns the image (alpha
    only, dark) and where the anchor falls in it."""
    w, d, _ = size
    grow = 0.06
    corners = [plane((x, y, 0)) for x, y in ((-w / 2 - grow, -d / 2 - grow), (w / 2 + grow, -d / 2 - grow),
                                               (w / 2 + grow, d / 2 + grow), (-w / 2 - grow, d / 2 + grow))]
    pad = 0.25
    x0, y0 = min(c[0] for c in corners) - pad, min(c[1] for c in corners) - pad
    x1, y1 = max(c[0] for c in corners) + pad, max(c[1] for c in corners) + pad
    W, H = max(1, round((x1 - x0) * ppm)), max(1, round((y1 - y0) * ppm))
    a = Image.new('L', (W, H), 0)
    ImageDraw.Draw(a).polygon([((cx - x0) * ppm, (cy - y0) * ppm) for cx, cy in corners], fill=255)
    a = a.filter(ImageFilter.GaussianBlur(max(1, 0.07 * ppm)))
    img = Image.new('RGBA', (W, H), (38, 48, 70, 0))
    img.putalpha(a)
    return img, (-x0 * ppm, -y0 * ppm)


def fit_prop(name: str, p: Prop) -> dict:
    im = Image.open(RAW / f'{p.raw}.png').convert('RGBA')
    if p.part is not None:
        im = im.crop(components(im, p.parts, p.rows)[p.part])
    im = trim(im)
    w, d, h = p.size
    box = [-w / 2, -d / 2, p.base, w / 2, d / 2, p.base + h]
    rx0, ry0, rx1, ry1 = box_rect(box)
    src_ppm = im.width / (rx1 - rx0) if p.fit == 'w' else im.height / (ry1 - ry0)
    # the image's centre line and bottom sit on the box's: the anchor (0, 0, 0) is then here in the image
    anchor = (im.width / 2 - (rx0 + rx1) / 2 * src_ppm, im.height - ry1 * src_ppm)
    tiers = [t for t in TIERS if t <= src_ppm * 1.02]
    if src_ppm > TIERS[-1] * 1.2 or not tiers or src_ppm > tiers[-1] * 1.2:
        tiers.append(min(src_ppm, TIERS[-1] * 2))
    out = []
    for ppm in tiers:
        k = ppm / src_ppm
        spr = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.Resampling.LANCZOS)
        ax, ay = anchor[0] * k, anchor[1] * k
        if p.shadow:
            sh, (sx, sy) = contact_shadow(p.size, ppm)
            sh.putalpha(sh.getchannel('A').point(lambda v: int(v * p.shadow)))
            left, top = min(0, round(ax - sx)), min(0, round(ay - sy))
            W = max(spr.width, round(ax - sx) + sh.width) - left
            H = max(spr.height, round(ay - sy) + sh.height) - top
            canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            canvas.alpha_composite(sh, (round(ax - sx) - left, round(ay - sy) - top))
            canvas.alpha_composite(spr, (-left, -top))
            spr, ax, ay = canvas, ax - left, ay - top
        out.append(save(name, ppm, spr, (ax, ay)))
    return {'source': 'ai', 'from': f'art/kit/raw/{p.raw}.png' + (f' (object {p.part + 1} of {p.parts})' if p.part is not None else ''),
            'size_m': list(p.size), 'scale': {'fit': p.fit, 'source_px_per_m': round(src_ppm, 1)},
            'footprint': [round(v, 3) for v in box], 'hit': 'alpha', 'tiers': out,
            **({'slots': p.slots} if p.slots else {}), **({'doc': p.doc} if p.doc else {})}


def save(name: str, ppm: float, im: Image.Image, anchor, frames: int = 1) -> dict:
    file = f'{name}@{round(ppm)}.webp'
    im.save(OUT / file, 'WEBP', quality=QUALITY, method=4)
    return {'ppm': round(ppm, 3), 'file': file, 'size': [im.width // frames, im.height],
            'anchor_px': [round(anchor[0], 2), round(anchor[1], 2)], **({'frames': frames} if frames > 1 else {})}


# --- Blender pieces -------------------------------------------------------------------------------

LAYERS = {'pilaster': 'ground', 'wall-cap-x': 'ground', 'wall-cap-y': 'ground', 'wall-corner': 'ground',
          'wall-end-back': 'ground', 'wall-end-left': 'ground', 'slab-front': 'ground', 'slab-side': 'ground',
          'plan-wall': 'ground'}   # (the lift's doors change, so it stands: a ground change repaints the snapshot)


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
    img = Image.new('RGBA', (size_px, size_px), (*color, 0))
    a = Image.radial_gradient('L').resize((size_px, size_px)).point(lambda v: int(255 * max(0, 1 - v / 255) ** 1.6 * inner))
    img.putalpha(a)
    return img


def hexrgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


SRC = 300  # px per metre in the flat drawings


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


def procedural() -> dict:
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

    for a in range(0, 360, 45):
        floor_sprite(f'footprints-{a:03d}', footprints(a), f'a pair of prints walking {a}° from +x (anticlockwise from above)', 'ground')
    floor_sprite('glow-desk-pool', radial(int(0.9 * SRC), hexrgb('#fee095'), 0.75),
                 'warm pool under a desk lamp, on the desk top (place at desk height)', 'light', {'blend': 'lighter'})
    floor_sprite('glow-floor-spill', radial(int(3.2 * SRC), hexrgb('#fee095'), 0.28),
                 'low warm spill on the floor in front of an active bench', 'light', {'blend': 'lighter'})
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

def textures() -> dict:
    sprites = json.loads((B2 / 'sprites.json').read_text())['textures']
    out = {}
    for name in ('floor-tile', 'wall-tile'):
        t = sprites[name]
        shutil.copyfile(B2 / t['file'], OUT / t['file'])
        out[name] = {'file': t['file'], 'metres': t['metres'], 'source': 'ai',
                     'from': f'art/bakeoff/B2/{t["file"]} (bake-off generation, reused)'}
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
    for name, p in PROPS.items():
        sprites[name] = fit_prop(name, p)
        print(f'{name:16s} ai {sprites[name]["scale"]["source_px_per_m"]:7.1f} px/m src', flush=True)
    sprites.update(blender_pieces())
    sprites.update(procedural())
    manifest = {
        'version': 1,
        'about': 'The floor kit: see art/kit/README.md. Sprites are anchored at their base centre unless their doc '
                 'says otherwise; footprints and slots are metres from the anchor (x along the back wall, y towards '
                 'it, z up). layer is a hint: ground (in the ground snapshot), standing (sorted), light (additive).',
        'camera': {'pitch': PITCH, 'yaw': YAW},
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
             'Every object is modelled or scaled from its real size in metres; the tiers are 0.5x, 1x and 2x that '
             'density, plus an AI prop\'s own density when it is finer.',
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
