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

PITCH, YAW = 28.0, 33.0   # the world camera (build_kit.py): the image model's, measured
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
    shadow: float = 0.42             # contact shadow opacity; 0 for none
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
    'monitor': Prop('monitor', (0.55, 0.42, 0.45), shadow=0.18, doc='monitor, keyboard and mouse, facing the viewer'),
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
    'crate-stack': Prop('crate-stack', (1.4, 1.1, 1.55), doc='plain crates on a pallet: storage furniture (the hourglass crate means waiting)'),
    # (l2's diamond is ~70 px wide at its 171.5 px/m)
    'lantern': Prop('lantern', (0.36, 0.36, 0.66), base=-0.33, shadow=0, doc='the attention lantern: magenta only here; '
                    'anchor at the diamond centre, cable above; the front facet is blank for the glyph',
                    slots={'glyph': [0, -0.1, 0.0], 'cable_top': [0, 0, 1.2]}),
}


def components(im: Image.Image, n: int, rows: int, close: int = 5) -> list[tuple[int, int, int, int]]:
    """The bounding boxes of the n largest separate objects in a generation, row-major: alpha closed over small
    gaps (chair castors, pens) and flood-filled on a quarter-size grid."""
    k = 4
    a = im.getchannel('A').point(lambda v: 255 if v > 40 else 0).reduce(k).point(lambda v: 255 if v > 0 else 0)
    if close > 1:
        a = a.filter(ImageFilter.MaxFilter(close))
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
    # two layers, as a baked contact shadow reads: a tight dark core where the object meets the floor, and a wide
    # soft falloff around it
    def layer(g_m, blur_m, strength):
        cs = [plane((x, y, 0)) for x, y in ((-w / 2 - g_m, -d / 2 - g_m), (w / 2 + g_m, -d / 2 - g_m),
                                              (w / 2 + g_m, d / 2 + g_m), (-w / 2 - g_m, d / 2 + g_m))]
        m = Image.new('L', (W, H), 0)
        ImageDraw.Draw(m).polygon([((cx - x0) * ppm, (cy - y0) * ppm) for cx, cy in cs], fill=strength)
        return m.filter(ImageFilter.GaussianBlur(max(1, blur_m * ppm)))
    a = ImageChops.lighter(layer(-0.02, 0.025, 255), layer(grow + 0.06, 0.1, 150))
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


# --- bench pieces: one generation of a left end, a middle module and a right end, cut to tile -----------------------

DEPTH, TOP_Z, TOP_T = 0.8, 0.74, 0.04   # the desk top's depth, height and thickness, metres
BAY_M = 3.6                              # a structural bay, the length of repeating pieces


def _wood(im: Image.Image):
    """Pixels of the light-oak desk top: warm, light, opaque."""
    px = im.load()
    out = []
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a > 200 and r > 170 and r - b > 45 and g > 120:
                out.append((x, y))
    return out


MODULE = 1.6   # metres of bench per seat (as asked of the generation; 1.8 drew the bench a fifth larger than l2)
INSET = 0.08   # seams this share of a top's length in from its ends
OVERLAP = 0.05 # metres a piece runs on under the next


def _far_edge(wd) -> dict:
    """The topmost wood pixel of each column: the desk top's far edge."""
    top = {}
    for x, y in wd:
        if x not in top or y < top[x]:
            top[x] = y
    return top


def _slope(points) -> float:
    n = len(points)
    mx, my = sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
    return sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x, _ in points)


def _shear(im: Image.Image, k: float) -> Image.Image:
    """Shear vertically so a line of slope k becomes level (y' = y - k x); verticals stay vertical."""
    h_extra = int(abs(k) * im.width) + 2
    out = im.transform((im.width, im.height + h_extra), Image.Transform.AFFINE,
                       (1, 0, 0, k, 1, -h_extra if k < 0 else 0), resample=Image.Resampling.BICUBIC)
    return out


def bench_pieces() -> tuple[dict, float]:
    """The three pieces as sprites of exactly one seat module each, so any number of middles between the two ends
    makes one continuous bench. The generation draws furniture at its own angle (the desk's long edges slope 0.37
    where this camera's slope 0.27, like every AI prop here), so each piece is sheared vertically until its far edge
    has the camera's slope, which makes consecutive modules meet along the bench. The seam then runs along the
    piece's own depth edge (measured), the module is the middle piece's far edge (one seat, MODULE metres), and
    each piece is anchored at its top's far-left corner."""
    raw = Image.open(RAW / 'bench-pieces.png').convert('RGBA')
    pieces = [raw.crop(b) for b in components(raw, 3, 1, close=1)]
    k_cam = PY[0] / PX[0]
    fixed, meta = [], []
    for im in pieces:
        top = _far_edge(_wood(im))
        xs = sorted(top)[len(top) // 5: 4 * len(top) // 5]
        k_ai = _slope([(x, top[x]) for x in xs])
        im = _shear(im, k_ai - k_cam)
        wd = _wood(im)
        top = _far_edge(wd)
        corner = min(wd, key=lambda p: (p[1] - k_cam * p[0], p[0]))   # far-left: highest above the far edge's line
        # the top's left edge (the depth direction): the leftmost wood pixel of each row below the corner
        left = {}
        for x, y in wd:
            if y > corner[1] and (y not in left or x < left[y]):
                left[y] = x
        rows = sorted(left)[: max(3, len(left) // 3)]   # the upper part of that edge: the top face, not the front
        kd = _slope([(left[y], y) for y in rows]) if len(rows) > 2 else -1.0
        dd = (1.0, kd)                                    # along the depth edge, screen px per px
        # the near-left corner: the lowest wood pixel still on that edge line (below it: the top's front face, legs)
        on_edge = [(x, y) for x, y in wd if y > corner[1] and abs(x - (corner[0] + (y - corner[1]) / kd)) <= 2.5]
        near = max(on_edge, key=lambda p: p[1]) if on_edge else corner
        far_right = max(top)                              # the far edge's rightmost column
        # the far edge as a line (it now has the camera's slope): its offset, from the middle of the edge, where the
        # corners' rounding doesn't reach
        cols = sorted(top)[len(top) // 5: 4 * len(top) // 5]
        c_far = sorted(top[x] - k_cam * x for x in cols)[len(cols) // 2]
        fixed.append(im)
        meta.append({'corner': corner, 'near': near, 'dd': dd, 'far_right': far_right, 'c_far': c_far})
    mid = meta[1]
    # seams sit a little inside each top (its ends are not quite parallel to its depth edge), so both sides of every
    # seam have wood; all seams take the middle piece's shape, so neighbouring pieces are exact complements
    far_mid = mid['far_right'] - mid['corner'][0]
    inset = INSET * far_mid
    pitch_x = far_mid - 2 * inset
    pitch = (pitch_x, pitch_x * k_cam)
    s = pitch_x / (MODULE * PX[0])                         # source px per metre
    kd, ny = mid['dd'][1], mid['near'][1] - mid['corner'][1]

    def side(x, y, qx, qy):
        # how far right of the seam through q a pixel lies, horizontally. The seam is the plane x = const: across
        # the top it runs along the depth edge from q to the near edge; below that (the top's front face, the
        # pedestals, the legs) it is vertical
        return x - (qx + min(y - qy, ny) / kd)

    out = {}
    names = (('bench-left', False, True), ('bench-mid', True, True), ('bench-right', True, False))
    for (name, cut_left, cut_right), im, m in zip(names, fixed, meta):
        cx, cy = m['corner']
        own = m['far_right'] - cx
        # the module's left seam (the piece's anchor): an inset in from the left end; for the left end piece, one
        # module before its right seam, so its legs and end stand to the left of the anchor
        ax = cx + (own - inset - pitch_x if name == 'bench-left' else inset)
        anchor = (ax, m['c_far'] + k_cam * ax)   # on the far edge's line
        keep = Image.new('L', im.size, 0)
        kp = keep.load()
        for y in range(im.height):
            for x in range(im.width):
                a = side(x, y, *anchor)
                b = side(x, y, anchor[0] + pitch[0], anchor[1] + pitch[1])
                # (a piece runs OVERLAP past its right seam, under the next piece, which draws after it: its wood
                # fills any sliver where the two tops' edges don't quite meet)
                if (not cut_left or a >= -0.5) and (not cut_right or b < OVERLAP * s):
                    kp[x, y] = 255
        im.putalpha(ImageChops.multiply(im.getchannel('A'), keep))
        m['corner'] = anchor
        t = trim_keep(im, m['corner'])
        tiers = []
        for ppm in [t_ for t_ in TIERS if t_ <= s * 1.02] + ([s] if s > TIERS[-1] * 1.2 else []):
            k = ppm / s
            spr = t['img'].resize((max(1, round(t['img'].width * k)), max(1, round(t['img'].height * k))), Image.Resampling.LANCZOS)
            tiers.append(save(name, ppm, spr, (t['anchor'][0] * k, t['anchor'][1] * k)))
        out[name] = {'source': 'ai', 'from': f'art/kit/raw/bench-pieces.png (piece {len(out) + 1} of 3)', 'layer': 'standing',
                     'size_m': [MODULE, DEPTH, TOP_Z], 'module_m': MODULE, 'hit': 'alpha', 'tiers': tiers,
                     'scale': {'fit': 'far edge = one module', 'source_px_per_m': round(s, 1),
                               'sheared_to_camera': round(k_cam, 4)},
                     # anchored at the desk top's far-left corner: the footprint and slots are metres from it (the
                     # module sits at x 0..MODULE, the top's far edge at y 0, its surface at z 0)
                     'anchor': 'desk top, far edge, at the module\'s left seam',
                     'footprint': [0, -DEPTH - 0.02, -TOP_Z, MODULE, 0, 0],
                     # the seat 0.3 m behind the far edge: a seated robot's legs are under the top (floor review 1)
                     'slots': {'seat': [MODULE / 2, 0.3, 0.47 - TOP_Z], 'lamp': [0.18, -0.12, 0],
                               'desk_top': [MODULE / 2, -DEPTH / 2, 0], 'floor_centre': [MODULE / 2, -DEPTH / 2, -TOP_Z]},
                     'doc': 'one seat module of the long bench: tile the left end, middles and right end MODULE apart'}
    return out, MODULE


def trim_keep(im: Image.Image, point) -> dict:
    """Trim to the alpha bounds, keeping track of where `point` lands."""
    box = im.getchannel('A').point(lambda a: 255 if a > 8 else 0).getbbox()
    return {'img': im.crop(box), 'anchor': (point[0] - box[0], point[1] - box[1])}


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
    # (anchor: the bench's centre on the floor) and a seated robot on its chair (anchor: the seat's floor point)
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
    a = ImageChops.lighter(soft_rect(0.5, 0.45, 0.2, 0.04, 140), soft_rect(0.7, 0.62, 0.3, 0.1, 70))
    floor_sprite('shadow-seat', shadow(a), 'a seated robot and its chair\'s contact shadow', 'ground', {'hit': 'none'})
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
    for name, p in PROPS.items():
        sprites[name] = fit_prop(name, p)
        print(f'{name:16s} ai {sprites[name]["scale"]["source_px_per_m"]:7.1f} px/m src', flush=True)
    sprites.update(blender_pieces())
    pieces, module = bench_pieces()
    sprites.update(pieces)
    sprites.update(procedural(module))
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
