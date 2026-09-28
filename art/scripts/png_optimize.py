"""Losslessly re-compress the B1 sprite PNGs (PIL's optimiser beats Blender's writer by about a third) and
update the byte counts in art/bakeoff/B1/manifest.json.

    python3 art/scripts/png_optimize.py
"""
import json
from pathlib import Path

from PIL import Image

B1 = Path(__file__).resolve().parents[1] / 'bakeoff' / 'B1'


def sizes(node) -> None:
    """Refresh every {'file': ..., 'bytes': ...} entry in the manifest tree."""
    if isinstance(node, dict):
        if 'file' in node and 'bytes' in node:
            node['bytes'] = (B1 / node['file']).stat().st_size
        for v in node.values():
            sizes(v)


def main() -> None:
    before = after = 0
    for png in sorted(B1.glob('*.png')):
        before += png.stat().st_size
        im = Image.open(png)
        im.load()
        im.save(png, 'PNG', optimize=True)
        after += png.stat().st_size
    manifest = json.loads((B1 / 'manifest.json').read_text())
    sizes(manifest)
    (B1 / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'png_optimize: {before / 1e6:.2f} MB -> {after / 1e6:.2f} MB')


if __name__ == '__main__':
    main()
