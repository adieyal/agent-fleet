"""Turn the building pieces rendered at tier 4 (art/scripts/building_pieces.py --mult 4) into the shipped WebP tiers.

    python3 art/scripts/building_finish.py BUILD_DIR

Writes fleet/web/assets/world/building/<piece>@<ppm>.webp at 1x, 2x and 4x (25, 51 and 102 px/m) and manifest.json:
the camera, the floor step in pixels, and per piece and tier its file, size, anchor pixel and DOM slots. Every size and
anchor is a multiple of 4 at tier 4, so the smaller tiers are exact halvings and anchors stay whole pixels.
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
    info = json.loads((build / 'pieces.json').read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    ppm1 = info['camera']['ppm_1x']
    manifest = {k: v for k, v in info.items() if k != 'pieces'}
    manifest['pieces'] = {}
    total = 0
    for name, p in info['pieces'].items():
        src = p['mult']
        if src != 4:
            sys.exit(f'building_finish: {name} was rendered at x{src}; render at --mult 4')
        im = Image.open(build / p['file']).convert('RGBA')
        tiers = []
        for mult in MULTS:
            k = mult / src
            size = [round(p['size'][0] * k), round(p['size'][1] * k)]
            out = im if mult == src else im.resize(size, Image.LANCZOS)
            f = f'{name}@{round(ppm1 * mult)}.webp'
            out.save(OUT / f, 'WEBP', quality=QUALITY, method=6)
            total += (OUT / f).stat().st_size
            tiers.append({'ppm': round(ppm1 * mult, 3), 'file': f, 'size': size,
                          'anchor_px': [p['anchor_px'][0] * k, p['anchor_px'][1] * k],
                          'slots': {s: [round(v * k, 2) for v in xy] for s, xy in p['slots'].items()}})
        manifest['pieces'][name] = {'tiers': tiers}
        print(name, [t['size'] for t in tiers])
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(f'building_finish: {len(manifest["pieces"])} pieces, {total / 1e6:.2f} MB of WebP')


if __name__ == '__main__':
    main()
