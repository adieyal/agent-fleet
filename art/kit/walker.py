#!/usr/bin/env python3
"""Pack the placeholder walker (art/scripts/build_walker.py, rendered on host home into art/build/walker/) into
fleet/web/assets/world/robot-placeholder/: a sprite per heading (walk-0 .. walk-7, 8-frame sheets with a tint mask)
in the sprite engine's manifest format. It stands in for the robot job's Walking clip until that lands
(docs/design/robot-sprites.md); swapping is a change of manifest.

    uv run --group dev python art/kit/walker.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / 'art' / 'build' / 'walker'
OUT = REPO / 'fleet' / 'web' / 'assets' / 'world' / 'robot-placeholder'
SHELL = 0.72      # the shell's brightness relative to its render, so it tints like the seated (B2) robots


def main() -> None:
    info = json.loads((SRC / 'walker.json').read_text())
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    fps = round(info['frames'] / info['clip_s'] * 1.25, 2)   # the deck plays Walking at timescale 1.25
    sprites = {}
    for h, entry in info['headings'].items():
        tiers = []
        for t, m in zip(entry['tiers'], entry['mask']):
            name = f'walk-{h}@{round(t["ppm"])}'
            im, raw = Image.open(SRC / t['file']).convert('RGBA'), Image.open(SRC / m['file']).convert('L')
            # the mask pass was framed on its own (from another pose), so move each of its cells onto the colour
            # frame by the two anchors
            mask = Image.new('L', im.size, 0)
            dx, dy = round(t['anchor_px'][0] - m['anchor_px'][0]), round(t['anchor_px'][1] - m['anchor_px'][1])
            fw, mw = t['size'][0], m['size'][0]
            for i in range(t['frames']):
                cell = Image.new('L', (fw, t['size'][1]), 0)
                cell.paste(raw.crop((i * mw, 0, (i + 1) * mw, m['size'][1])), (dx, dy))
                mask.paste(cell, (i * fw, 0))
            # the shell renders near white; the runtime multiplies it by the host colour lifted by 1/0.8, which
            # assumes a mid-grey shell like the seated robots': darken it to that under the mask
            rgb = im.convert('RGB')
            grey = rgb.point(lambda v: int(v * SHELL))
            im = Image.merge('RGBA', (*Image.composite(grey, rgb, mask).split(), im.getchannel('A')))
            im.save(OUT / f'{name}.webp', 'WEBP', quality=88, method=4)
            mask.save(OUT / f'{name}.mask.png', optimize=True)
            tiers.append({'ppm': t['ppm'], 'file': f'{name}.webp', 'mask': f'{name}.mask.png', 'size': t['size'],
                          'anchor_px': t['anchor_px'], 'frames': t['frames'], 'fps': fps})
        sprites[f'walk-{h}'] = {'source': 'blender', 'from': 'art/scripts/build_walker.py (robot.glb of renovate/robot)',
                                'footprint': [-0.25, -0.25, 0, 0.25, 0.25, info['height_m']], 'hit': 'alpha', 'tiers': tiers,
                                'doc': f'placeholder: Walking, heading {45 * int(h)}° anticlockwise from +x; anchor between the feet'}
    manifest = {'version': 1, 'about': 'Placeholder robot: Walking in 8 headings, until the robot job\'s sprites land.',
                'camera': {'pitch': 44.5, 'yaw': 21.25}, 'sprites': sprites}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(f'walker: {len(sprites)} headings, {sum(f.stat().st_size for f in OUT.iterdir()) / 1e6:.2f} MB')


if __name__ == '__main__':
    main()
