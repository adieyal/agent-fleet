"""Turn the building renders (art/scripts/building_pieces.py --mult 4) into the shipped WebP tiers and manifest.

    python3 art/scripts/building_finish.py BUILD_DIR

Crops the spine and lift columns into their bands, writes fleet/web/assets/world/building/<piece>@<ppm>.webp at 1x, 2x
and 4x (25, 51 and 102 px/m; the plinths only 1x and 2x) and manifest.json: the camera, per tier the floor and lobby
steps in pixels, the backdrop colour, how to stack (stack), and per piece and tier its file, size, anchor pixel, DOM
slots and slot boxes (both in pixels from the anchor). Every crop, size and anchor is a multiple of the render's
multiplier, so the smaller tiers are exact reductions and anchors stay whole pixels.
"""
import json
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'fleet' / 'web' / 'assets' / 'world' / 'building'
QUALITY = 88
MULTS = (1, 2, 4)


def main() -> None:
    build = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT   # (a scratch folder for previews)
    info = json.loads((build / 'pieces.json').read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob('*.webp'):
        old.unlink()
    ppm1 = info['camera']['ppm_1x']
    manifest = {k: v for k, v in info.items() if k not in ('pieces', 'step_px', 'lobby_step_px')}
    manifest['tiers'] = {str(m): {'ppm': round(ppm1 * m, 3), 'step_px': info['step_px'] * m,
                                  'lobby_step_px': info['lobby_step_px'] * m} for m in MULTS}
    manifest['pieces'] = {}
    total, renders = 0, {}
    for name, p in sorted(info['pieces'].items()):
        src = p['mult']
        if p['file'] not in renders:
            renders[p['file']] = Image.open(build / p['file']).convert('RGBA')
        im = renders[p['file']]
        if 'crop' in p:
            im = im.crop(tuple(p['crop']))
        assert list(im.size) == p['size'], name
        tiers = {}
        for mult in (m for m in MULTS if m <= src):
            k = mult / src
            size = [round(p['size'][0] * k), round(p['size'][1] * k)]
            out = im if mult == src else im.resize(size, Image.LANCZOS)
            f = f'{name}@{round(ppm1 * mult)}.webp'
            out.save(out_dir / f, 'WEBP', quality=QUALITY, method=6)
            total += (out_dir / f).stat().st_size
            tiers[str(mult)] = {'file': f, 'size': size, 'anchor_px': [int(p['anchor_px'][0] * k), int(p['anchor_px'][1] * k)],
                                'slots': {s: [round(v * k, 2) for v in xy] for s, xy in p['slots'].items()},
                                'boxes': {s: [round(v * k, 2) for v in wh] for s, wh in p.get('boxes', {}).items()}}
            if name.startswith('plinth-') and mult == 1:
                manifest['backdrop'] = '#%02x%02x%02x' % out.getpixel((2, 2))[:3]
        manifest['pieces'][name] = {'tiers': tiers}
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(f'building_finish: {len(manifest["pieces"])} pieces, {total / 1e6:.2f} MB of WebP')


if __name__ == '__main__':
    main()
