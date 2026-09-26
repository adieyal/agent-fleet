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
}
TINTABLE = ('robot-typing', 'robot-pencil', 'robot-tube')
TEAL = (38, 178, 170)  # the l2 teal robot's lit shell


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


def finish() -> dict:
    manifest = {}
    for name, (raw, (axis, px)) in KEEP.items():
        im = trim(Image.open(RAW / f'{raw}.png').convert('RGBA'))
        k = px * SCALE / (im.width if axis == 'w' else im.height)
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        out = B2 / f'{name}.webp'
        im.save(out, 'WEBP', quality=90, method=6)
        manifest[name] = {'file': out.name, 'from': f'raw/{raw}.png', 'size': list(im.size),
                          'l2_scale': round(1 / SCALE, 4), 'bytes': out.stat().st_size}
        if name in TINTABLE:
            mask = B2 / f'{name}.mask.png'
            tint_mask(im).save(mask, optimize=True)
            manifest[name].update(mask=mask.name, mask_bytes=mask.stat().st_size)
    (B2 / 'sprites.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


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
        print(k, v['size'], v['bytes'], v.get('mask_bytes', ''))
