#!/usr/bin/env python3
"""Measure the bake-off page (/prototype/bakeoff) per variant: frame time in Chromium with the GPU
disabled (SwiftShader) and enabled (ANGLE over OpenGL), at l2 and floor framing, with the frame rate
uncapped so the interval is the real cost; the download per variant; and a screenshot of each variant
beside l2.png.

    uv run python art/bakeoff/measure.py [--out DIR] [--seconds 4] [--variants A B1 B2 B2mix]

Writes DIR/measurements.json, DIR/measurements.md and DIR/<variant>-vs-l2.png.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from fleet.container import Container
from fleet_web.fixture import FixtureLibrary, FixtureState  # noqa: E402
from fleet_web.server import make_handler  # noqa: E402

VIEWPORT = {'width': 1672, 'height': 941}  # l2.png's size
UNCAPPED = ['--disable-frame-rate-limit', '--disable-gpu-vsync']
MODES = {
    'no GPU (SwiftShader)': ['--disable-gpu', *UNCAPPED],
    'GPU (ANGLE GL)': ['--enable-gpu', '--ignore-gpu-blocklist', '--use-angle=gl', *UNCAPPED],
}
RENDERER = """() => { const g = document.createElement('canvas').getContext('webgl2'); if (!g) return 'none';
  const e = g.getExtension('WEBGL_debug_renderer_info'); return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : '?'; }"""


def serve() -> tuple[ThreadingHTTPServer, str]:
    state = FixtureState.load(REPO / 'tests' / 'fixtures' / 'restoke.json', container=Container())
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state, FixtureLibrary(state.fixture, container=state.container)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def open_page(browser, url: str, query: str):
    page = browser.new_page(viewport=VIEWPORT)
    errors: list[str] = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{url}/prototype/bakeoff?{query}')
    page.wait_for_function('window.bakeoff && (window.bakeoff.ready || window.bakeoff.error)', timeout=120_000)
    return page, errors


def measure(browser, url: str, variant: str, zoom: float, seconds: float) -> dict:
    page, errors = open_page(browser, url, f'variant={variant}&zoom={zoom}&shot')
    page.wait_for_timeout(1000)  # settle: decode, first uploads
    page.evaluate('window.bakeoff.resetStats()')
    page.wait_for_timeout(seconds * 1000)
    stats = page.evaluate('window.bakeoff.stats()')
    error = page.evaluate('window.bakeoff.error || null')
    page.close()
    f = stats['frame']
    if error or not f:
        return {'variant': variant, 'zoom': zoom, 'errors': errors + [error or 'no frames'], 'bytes': stats['bytes'],
                'missing': stats['missing'], 'frame_ms': None, 'frame_p95_ms': None, 'fps': None, 'draw_ms': None, 'frames': 0}
    return {'variant': variant, 'zoom': zoom, 'errors': errors, 'bytes': stats['bytes'], 'missing': stats['missing'],
            'frame_ms': round(f['interval_mean'], 2), 'frame_p95_ms': round(f['interval_p95'], 2),
            'fps': round(1000 / f['interval_mean'], 1), 'draw_ms': round(f['work_mean'], 3), 'frames': f['frames']}


def side_by_side(browser, url: str, variant: str, out: Path) -> Path:
    page, _ = open_page(browser, url, f'variant={variant}&zoom=1&shot&t=0.3')
    page.wait_for_timeout(800)
    shot = out / f'.{variant}.png'
    page.screenshot(path=str(shot))
    page.close()
    l2 = Image.open(REPO / 'docs' / 'images' / 'concept' / 'l2.png').convert('RGB')
    ours = Image.open(shot).convert('RGB')
    pair = Image.new('RGB', (l2.width * 2 + 16, l2.height), (255, 255, 255))
    pair.paste(ours, (0, 0))
    pair.paste(l2, (l2.width + 16, 0))
    dest = out / f'{variant}-vs-l2.png'
    pair.save(dest, optimize=True)
    shot.unlink()
    return dest


def table(rows: list[dict]) -> str:
    lines = ['| Variant | Mode | Zoom | Frame ms (mean) | p95 | fps | Draw call ms | Assets KB | Code KB |',
             '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        b = r['bytes']
        lines.append(f"| {r['variant']} | {r['mode']} | {'l2' if r['zoom'] == 1 else 'floor'} | {r['frame_ms']} | "
                     f"{r['frame_p95_ms']} | {r['fps']} | {r['draw_ms']} | {b['assets'] // 1024} | {b['code'] // 1024} |")
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--out', type=Path, default=REPO / 'art' / 'bakeoff' / 'results')
    ap.add_argument('--seconds', type=float, default=4)
    ap.add_argument('--repeat', type=int, default=3, help='runs per cell; the median run is reported')
    ap.add_argument('--variants', nargs='+', default=['A', 'B1', 'B2', 'B2mix'])
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    server, url = serve()
    rows, renderers = [], {}
    try:
        with sync_playwright() as p:
            for mode, flags in MODES.items():
                browser = p.chromium.launch(args=flags)
                probe = browser.new_page()
                renderers[mode] = probe.evaluate(RENDERER)
                browser.close()
                for v in args.variants:
                    for zoom in (1, 0):
                        runs = []
                        for _ in range(args.repeat):  # a fresh browser each time: SwiftShader work spills over
                            browser = p.chromium.launch(args=flags)
                            runs.append(measure(browser, url, v, zoom, args.seconds))
                            browser.close()
                        ok = sorted((r for r in runs if r['frame_ms'] is not None), key=lambda r: r['frame_ms'])
                        row = ok[len(ok) // 2] if ok else runs[0]
                        rows.append({'mode': mode, **row, 'runs_frame_ms': [r['frame_ms'] for r in runs]})
                        print(table(rows[-1:]).splitlines()[-1], rows[-1]['runs_frame_ms'], flush=True)
            browser = p.chromium.launch(args=MODES['GPU (ANGLE GL)'][:3])
            shots = [str(side_by_side(browser, url, v, args.out).name) for v in args.variants if v != 'B2mix']
            shots.append(side_by_side(browser, url, 'B2mix', args.out).name)
            browser.close()
    finally:
        server.shutdown()
    result = {'viewport': VIEWPORT, 'renderers': renderers, 'rows': rows, 'shots': shots}
    (args.out / 'measurements.json').write_text(json.dumps(result, indent=2) + '\n')
    md = [f'Viewport {VIEWPORT["width"]}x{VIEWPORT["height"]}, device pixel ratio 1, frame rate uncapped; '
          f'median of {args.repeat} runs of {args.seconds:g} s, each in a fresh browser.', '',
          *(f'- {m}: `{r}`' for m, r in renderers.items()), '', table(rows)]
    (args.out / 'measurements.md').write_text('\n'.join(md) + '\n')
    print('\n'.join(md))
    return 0


if __name__ == '__main__':
    sys.exit(main())
