"""Measure how close the prototype camera can zoom before the baked lighting goes soft.

    uv run --group dev python art/scripts/zoom_sharpness.py [OUT_DIR]

At each zoom the same spot on the bench (a pedestal's contact shadow on the floor, a desk edge, chair
casters) is rendered at 1672 x 941 and cropped to the same world area, and two numbers are recorded:

- screen pixels per lightmap texel: the lightmap's texel size (from the workbench manifest) over the
  view's metres per pixel. Bilinear filtering hides texels up to a few pixels; past ~4 px a soft,
  stepped look appears along baked contact shadows.
- contact-shadow edge width: the 10-90% ramp of the shadow edge beside the pedestal, in world mm.
  It stays near the baked blur while the lightmap is sharp enough, then grows with zoom.

The crops go side by side in OUT_DIR/zoom-sharpness.png for eyeballing.
"""
import io
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.server import make_handler
from shoot_bench import FIXTURE, REPO, headless_env

SIZE = {'width': 1672, 'height': 941}
ZOOMS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
SPOT = [7.1, 0.3, -3.9]   # three coordinates: the floor in front of desk 2's pedestal
CROP_M = 1.2              # world width of the compared crop


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / 'art' / 'build' / 'prototype'
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((REPO / 'fleet/web/assets/world/workbench/manifest.json').read_text())
    texel = manifest['lightmap']['texel_m']
    state = FixtureState.load(FIXTURE)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state, FixtureLibrary(state.fixture)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows, crops = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-gpu',
                                              '--ignore-gpu-blocklist'], env=headless_env())
            page = browser.new_page(viewport=SIZE)
            page.goto(f'http://127.0.0.1:{server.server_port}/prototype/bench?still&shot&maxzoom={max(ZOOMS)}')
            page.wait_for_function('window.bench && window.bench.ready', timeout=120_000)
            home = page.evaluate('window.bench.view()')
            for z in ZOOMS:
                page.evaluate('v => window.bench.setView(v)', {'zoom': z, 'target': SPOT})
                page.wait_for_timeout(300)
                view = page.evaluate('window.bench.view()')
                img = Image.open(io.BytesIO(page.screenshot())).convert('L')
                m_per_px = 5.486 / view['zoom'] / SIZE['height']
                half = int(CROP_M / m_per_px / 2)
                cy, cx = SIZE['height'] // 2, SIZE['width'] // 2
                crop = img.crop((cx - half, cy - half // 2, cx + half, cy + half // 2))
                # the shadow edge: how many pixels the brightness takes to ramp 10-90% down the centre column
                w, h = crop.size
                col = [sum(crop.getpixel((x, y)) for x in range(w // 2 - 3, w // 2 + 3)) / 6 for y in range(h)]
                ranked = sorted(col)
                lo, hi = ranked[len(ranked) // 10], ranked[len(ranked) * 9 // 10]
                ramp = sum(lo + 0.1 * (hi - lo) < v < lo + 0.9 * (hi - lo) for v in col)
                rows.append({'zoom': round(view['zoom'], 2), 'px_per_texel': round(texel / m_per_px, 2),
                             'mm_per_px': round(m_per_px * 1000, 2), 'edge_ramp_mm': round(ramp * m_per_px * 1000)})
                crops.append(crop.resize((360, 180)))
            browser.close()
    finally:
        server.shutdown()
    sheet = Image.new('L', (360 * len(crops), 200), 255)
    d = ImageDraw.Draw(sheet)
    for i, (c, r) in enumerate(zip(crops, rows)):
        sheet.paste(c, (360 * i, 20))
        d.text((360 * i + 4, 4), f"zoom {r['zoom']}  {r['px_per_texel']} px/texel", fill=0)
    sheet.save(out / 'zoom-sharpness.png')
    print(json.dumps({'texel_m': texel, 'home': home['zoom'], 'rows': rows}, indent=1))


if __name__ == '__main__':
    main()
