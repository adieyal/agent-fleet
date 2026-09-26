#!/usr/bin/env python3
"""Turn the kept raw generations into runtime sprites: trim to the alpha bounds, scale so each
object has its l2 size times SCALE, save WebP, and write sprites.json for the comparison page.
Also writes contact.jpg (every raw generation) and tint.jpg (grey, tinted, generated teal).
Raw PNGs are not in git (14 MB); their sidecars are."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

B2 = Path(__file__).resolve().parent
RAW = B2 / 'raw'
SCALE = 1.5  # sprites are drawn at up to l2 framing on 2x screens; 1.5x l2 is the compromise
# l2 framing is 171.528 px/m (941 px over 5.486 m, the prototype's l2 camera), so sprites hold:
PX_PER_M = round(171.528 * SCALE, 3)
PITCH_COS = 0.7133  # cos 44.5 deg: a metre of height is this many metres of screen
DESK_SLOPE = 0.2726  # screen dy/dx of the bench's long axis at yaw 21.25, pitch 44.5
DESK_TOP_Z = 0.74

ROBOT_H = 205  # seated robot with chair, head to castors, in l2 px (matched by head size)
# name: (raw generation, l2 measure in px at 1672 wide: ('w'|'h', px))
KEEP: dict[str, tuple[str, tuple[str, int]]] = {
    'bench': ('bench-v1', ('w', 930)),
    'plant': ('plant-v1', ('h', 240)),
    'robot-typing': ('robot-typing-grey-v2', ('h', ROBOT_H)),
    'robot-pencil': ('robot-pencil-grey-v2', ('h', ROBOT_H)),
    'robot-tube': ('robot-tube-grey-v2', ('h', ROBOT_H)),
    'robot-typing-teal': ('robot-typing-teal-v2', ('h', ROBOT_H)),
    'robot-pencil-teal': ('robot-pencil-teal-v2', ('h', ROBOT_H)),
    'robot-tube-teal': ('robot-tube-teal-v2', ('h', ROBOT_H)),
    'lantern': ('lantern-v1', ('k', 70 * SCALE / 600)),  # l2's diamond is ~70 px wide, the raw one 600
    'pilaster': ('pilaster-v2', ('h', 440)),       # a 3.2 m pilaster at l2 framing
    # the consistency test: the same objects from other generations, at the same scale rules
    'mixed/bench': ('bench-v3', ('w', 930)),
    'mixed/plant': ('plant-v2', ('h', 240)),
    'mixed/robot-typing': ('robot-typing-grey-v1', ('h', ROBOT_H)),
    'mixed/robot-pencil': ('robot-pencil-grey-v1', ('h', ROBOT_H)),
    'mixed/robot-tube': ('robot-tube-grey-v1', ('h', ROBOT_H)),
}
TINTABLE = ('robot-typing', 'robot-pencil', 'robot-tube')
TEAL = (38, 178, 170)  # the l2 teal robot's lit shell
# Where the chair seat is in each robot sprite (px, read off a grid); others use SEAT_RULE.
SEATS = {'robot-typing': (160, 210), 'robot-pencil': (140, 210), 'robot-tube': (142, 205)}
SEAT_RULE = (0.63, 0.68)  # the seats above as fractions of the sprite box, averaged
# How far below the seat (px) the desk-top cut runs at the seat's x, sloping with the desk: the
# part above is drawn after the bench (hands, laptop, paper), the part below before it.
CUTS = {'robot-typing': 30, 'robot-pencil': 23, 'robot-tube': 0}
CUT_DEFAULT = 20
ANIMS = {'robot-typing': 'anim-typing-v1', 'robot-pencil': 'anim-pencil-v1'}  # 4-cell sheets


def trim(im: Image.Image) -> Image.Image:
    alpha = im.getchannel('A').point(lambda a: 255 if a > 8 else 0)
    return im.crop(alpha.getbbox())


def tint_mask(im: Image.Image) -> Image.Image:
    """Where the grey shell is: light, unsaturated, opaque, with soft edges. Chair, visor and
    laptop are too dark; flat near-white areas bigger than a highlight (paper) are cut out."""
    _, s, v = im.convert('RGB').convert('HSV').split()
    a = im.getchannel('A')
    shell = ImageChops.multiply(ImageChops.multiply(
        s.point(lambda x: 255 if x < 35 else max(0, 255 - (x - 35) * 12)),
        v.point(lambda x: 0 if x < 95 else min(255, (x - 95) * 12))),
        a.point(lambda x: 255 if x > 200 else 0))
    shell = shell.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))  # fill speckle
    white = v.point(lambda x: 255 if x > 236 else 0).filter(ImageFilter.MinFilter(9))
    paper = white.filter(ImageFilter.MaxFilter(13))  # flat white patches survive the erosion
    return ImageChops.subtract(shell, paper).filter(ImageFilter.GaussianBlur(0.8))


def tinted(im: Image.Image, mask: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """The runtime recipe: colour times shell lightness (canvas 'multiply' with the colour
    lifted by 1/0.8, the shell's typical value), blended in through the mask."""
    rgb = im.convert('RGB')
    lift = [min(1.6, k / 255 / 0.8) for k in colour]
    mult = Image.merge('RGB', [c.point(lambda x, f=f: min(255, int(x * f))) for c, f in zip(rgb.split(), lift)])
    out = Image.composite(mult, rgb, mask)
    out.putalpha(im.getchannel('A'))
    return out


def scaled(raw: str, axis: str, px: float) -> Image.Image:
    im = trim(Image.open(RAW / f'{raw}.png').convert('RGBA'))
    k = px if axis == 'k' else px * SCALE / (im.width if axis == 'w' else im.height)
    return im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)


def far_edge_middle(im: Image.Image) -> tuple[float, float]:
    """Where the middle of the desk top's far edge is: the topmost light-oak pixel of each column
    gives the edge (a median line, so props standing on it don't bend it), and its middle lies half
    the desk depth (0.4 m) behind the oak's centroid, which on screen is to the right."""
    h, s, v = im.convert('RGB').convert('HSV').split()
    w = im.width
    top: dict[int, int] = {}
    xs = n = 0
    for i, (hh, ss, vv, a) in enumerate(zip(h.getdata(), s.getdata(), v.getdata(), im.getchannel('A').getdata())):
        if a > 200 and 14 <= hh <= 32 and 60 <= ss <= 150 and vv > 170:
            x = i % w
            top.setdefault(x, i // w)
            xs += x
            n += 1
    cx = xs / n + 0.4 * 0.3624 * PX_PER_M  # sin(yaw) of the 0.4 m towards the far edge
    near = sorted(x for x in top if abs(x - cx) < w * 0.3)
    offsets = sorted(top[x] - DESK_SLOPE * x for x in near)  # each column's intercept for the desk slope
    return cx, offsets[len(offsets) // 2] + DESK_SLOPE * cx


def anchor(name: str, im: Image.Image) -> dict:
    """Where the object's world reference point lands in the sprite (the page supplies the point):
    bench, the middle of the desk top's far edge; plant, the planter's footprint centre; pilaster, its footprint
    centre; lantern, the diamond's centre; robot, the chair seat, plus its desk-top cut line."""
    base = name.split('/')[-1]
    w, h = im.size
    if base == 'bench':
        return {'ref_px': [round(c, 1) for c in far_edge_middle(im)], 'ref': 'desk_top_far_edge_middle'}
    if base in ('plant', 'pilaster'):
        return {'ref_px': [round(w / 2, 1), round(h - 0.2 * PX_PER_M * 0.9, 1)], 'ref': 'footprint_centre'}
    if base == 'lantern':
        return {'ref_px': [round(w / 2, 1), round(h * 0.7, 1)], 'ref': 'diamond_centre'}
    pose = base.removesuffix('-teal')
    sx, sy = SEATS.get(name) or (SEAT_RULE[0] * w, SEAT_RULE[1] * h)
    cut = CUTS.get(pose, CUT_DEFAULT)
    return {'ref_px': [round(sx, 1), round(sy, 1)], 'ref': 'seat',
            'cut': {'x': round(sx, 1), 'y': round(sy + cut, 1), 'slope': DESK_SLOPE}}


def anim_frames(name: str, still: Image.Image, seat: tuple[float, float]) -> tuple[list[Image.Image], dict]:
    """Slice a 4-cell sheet, trim each cell, scale it to the still's height and pin its
    bottom centre (the castors) to the still's; report how far the cells drift from each other."""
    sheet = Image.open(RAW / f'{ANIMS[name]}.png').convert('RGBA')
    cw = sheet.width // 4
    frames = []
    for i in range(4):
        cell = trim(sheet.crop((i * cw, 0, (i + 1) * cw, sheet.height)))
        k = still.height / cell.height
        cell = cell.resize((round(cell.width * k), still.height), Image.LANCZOS)
        frame = Image.new('RGBA', still.size)
        frame.alpha_composite(cell, (round((still.width - cell.width) / 2), 0)) if cell.width <= still.width \
            else frame.alpha_composite(cell.crop(((cell.width - still.width) // 2, 0,
                                                  (cell.width - still.width) // 2 + still.width, cell.height)))
        frames.append(frame)
    alphas = [f.getchannel('A').point(lambda a: 255 if a > 128 else 0) for f in frames]
    ious = []
    for a in alphas[1:]:
        inter = sum(ImageChops.darker(alphas[0], a).getdata())
        union = sum(ImageChops.lighter(alphas[0], a).getdata())
        ious.append(round(inter / union, 3))
    widths = [trim(f).width for f in frames]
    return frames, {'frames': 4, 'fps': 6, 'silhouette_iou_vs_first': ious, 'widths_px': widths}


def strip(frames: list[Image.Image]) -> Image.Image:
    w, h = frames[0].size
    out = Image.new(frames[0].mode, (w * len(frames), h))
    for i, f in enumerate(frames):
        out.paste(f, (i * w, 0))
    return out


def textures() -> dict:
    """Floor: one quadrant of the 2x2 floor texture (grout on its right and bottom edges, so
    repeating it gives a full grid), a 0.6 m tile. Wall: the plaster texture, 2 m square."""
    out = {}
    floor = Image.open(RAW / 'floor-v2.png').convert('RGB')
    q = floor.crop((0, 0, floor.width // 2, floor.height // 2)).resize((256, 256), Image.LANCZOS)
    q.save(B2 / 'floor-tile.webp', 'WEBP', quality=88, method=6)
    wall = Image.open(RAW / 'wall-v2.png').convert('RGB').resize((512, 512), Image.LANCZOS)
    wall.save(B2 / 'wall-tile.webp', 'WEBP', quality=88, method=6)
    for name, metres, im in (('floor-tile', 0.6, q), ('wall-tile', 2.0, wall)):
        f = B2 / f'{name}.webp'
        out[name] = {'file': f.name, 'size': list(im.size), 'metres': metres, 'bytes': f.stat().st_size}
    out['floor-tile']['seam'] = 'on the grout line, by construction'
    out['wall-tile']['seam'] = seam(wall)
    return out


def seam(im: Image.Image) -> dict:
    """Mean absolute step across the wrap (left edge against right, top against bottom) next to
    the mean step between neighbouring columns inside: near 1x means the repeat hides its seam."""
    g = im.convert('L')
    w, h = g.size
    col = lambda x: g.crop((x, 0, x + 1, h))
    row = lambda y: g.crop((0, y, w, y + 1))
    step = lambda a, b: sum(ImageChops.difference(a, b).getdata()) / max(a.width, a.height)
    inner = sum(step(col(x), col(x + 1)) for x in range(w // 4, w // 4 + 20)) / 20
    return {'wrap_x': round(step(col(w - 1), col(0)) / inner, 2),
            'wrap_y': round(step(row(h - 1), row(0)) / inner, 2)}


def finish() -> dict:
    manifest = {'px_per_m': PX_PER_M, 'sprites': {}}
    for name, (raw, (axis, px)) in KEEP.items():
        im = scaled(raw, axis, px)
        out = B2 / f'{name}.webp'
        out.parent.mkdir(exist_ok=True)
        im.save(out, 'WEBP', quality=90, method=6)
        entry = {'file': f'{name}.webp', 'from': f'raw/{raw}.png', 'size': list(im.size),
                 'bytes': out.stat().st_size, **anchor(name, im)}
        base = name.split('/')[-1]
        if base in TINTABLE:
            mask = B2 / f'{name}.mask.png'
            tint_mask(im).save(mask, optimize=True)
            entry.update(mask=f'{name}.mask.png', mask_bytes=mask.stat().st_size)
        if name in ANIMS:
            frames, stats = anim_frames(name, im, tuple(entry['ref_px']))
            sheet, msheet = B2 / f'{name}.anim.webp', B2 / f'{name}.anim.mask.png'
            strip(frames).save(sheet, 'WEBP', quality=90, method=6)
            strip([tint_mask(f) for f in frames]).save(msheet, optimize=True)
            entry['anim'] = {'file': sheet.name, 'mask': msheet.name, 'bytes': sheet.stat().st_size,
                             'mask_bytes': msheet.stat().st_size, 'from': f'raw/{ANIMS[name]}.png', **stats}
        manifest['sprites'][name] = entry
    manifest['textures'] = textures()
    (B2 / 'sprites.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest['sprites']


def tint_sheet(manifest: dict) -> None:
    """Per pose: grey, grey tinted teal at runtime, and teal generated directly."""
    rows = []
    for pose in TINTABLE:
        grey = Image.open(B2 / manifest[pose]['file']).convert('RGBA')
        mask = Image.open(B2 / manifest[pose]['mask'])
        teal = Image.open(B2 / manifest[f'{pose}-teal']['file']).convert('RGBA')
        rows.append([grey, tinted(grey, mask, TEAL), teal])
    cw = max(im.width for r in rows for im in r) + 20
    ch = max(im.height for r in rows for im in r) + 20
    sheet = Image.new('RGB', (cw * 3, ch * len(rows)), (236, 238, 242))
    for y, r in enumerate(rows):
        for x, im in enumerate(r):
            sheet.paste(im, (x * cw + 10, y * ch + ch - im.height - 10), im)
    sheet.save(B2 / 'tint.jpg', quality=88)


def contact(cell: int = 300) -> None:
    raws = sorted(RAW.glob('*.png'))
    cols = 5
    rows = (len(raws) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cell, rows * (cell + 20)), (236, 238, 242))
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(raws):
        im = trim(Image.open(p).convert('RGBA'))
        im.thumbnail((cell - 10, cell - 10))
        x, y = (i % cols) * cell, (i // cols) * (cell + 20)
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2), im)
        draw.text((x + 5, y + cell + 3), p.stem, fill=(20, 20, 20))
    sheet.save(B2 / 'contact.jpg', quality=88)


if __name__ == '__main__':
    contact()
    manifest = finish()
    tint_sheet(manifest)
    for k, v in manifest.items():
        print(k, v['size'], v['bytes'], v.get('mask_bytes', ''), v['ref_px'], v.get('anim', {}).get('silhouette_iou_vs_first', ''))
    print(json.loads((B2 / 'sprites.json').read_text())['textures'])
