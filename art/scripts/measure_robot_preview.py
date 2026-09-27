"""Measure the robot sprite preview (/prototype/robot): frame time in headless Chromium with the GPU disabled
(SwiftShader, software raster) and enabled, far (1x sprites), mid (2x) and close (4x), with the frame rate
uncapped so the interval is the real cost. Also screenshots the walk beside B2's robot.

    uv run --group dev python art/scripts/measure_robot_preview.py [--seconds 4] [--repeat 3]

Writes art/build/robot_sprites/perf.json, perf.md and scene.png.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'bakeoff'))
sys.path.insert(0, str(HERE))
from measure import MODES, serve  # noqa: E402  (the bake-off's modes and fixture server)
from shoot_bench import headless_env  # noqa: E402

OUT = HERE.parent / 'build' / 'robot_sprites'
VIEWPORT = {'width': 1672, 'height': 941}
ZOOMS = {'far': 0.0, 'mid': 0.45, 'close': 1.0}


def run(browser, url: str, zoom: float, seconds: float) -> dict:
    page = browser.new_page(viewport=VIEWPORT)
    errors: list[str] = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(f'{url}/prototype/robot?zoom={zoom}&shot')
    page.wait_for_function('window.robotPreview && (robotPreview.ready || robotPreview.error)', timeout=120_000)
    page.wait_for_function('robotPreview.stats().loaded', timeout=120_000)  # close up, the 4x pages load first
    page.wait_for_timeout(2000)  # settle: decode, and tint what the first laps show
    page.evaluate('robotPreview.resetStats()')
    page.wait_for_timeout(seconds * 1000)
    stats = page.evaluate('robotPreview.stats()')
    page.close()
    f = stats['frame']
    return {'errors': errors, 'res': stats['res'], 'frame_ms': round(f['interval_mean'], 2), 'frame_p95_ms': round(f['interval_p95'], 2),
            'draw_ms': round(f['work_mean'], 3), 'draw_p95_ms': round(f['work_p95'], 3), 'frames': f['frames'],
            'sprite_bytes': stats['bytes']['sprites']}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--seconds', type=float, default=4)
    ap.add_argument('--repeat', type=int, default=3, help='runs per cell; the median run is reported')
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server, url = serve()
    rows = []
    try:
        with sync_playwright() as p:
            for mode, flags in MODES.items():
                for name, zoom in ZOOMS.items():
                    runs = []
                    for _ in range(args.repeat):  # a fresh browser each time
                        browser = p.chromium.launch(args=flags, env=headless_env())
                        runs.append(run(browser, url, zoom, args.seconds))
                        browser.close()
                    runs.sort(key=lambda r: r['frame_ms'])
                    rows.append({'mode': mode, 'zoom': name, **runs[len(runs) // 2],
                                 'frame_ms_runs': [r['frame_ms'] for r in runs]})
                    print(mode, name, rows[-1]['frame_ms'], 'ms', rows[-1]['res'], flush=True)
            browser = p.chromium.launch(args=MODES['no GPU (SwiftShader)'][:1], env=headless_env())
            page = browser.new_page(viewport=VIEWPORT)
            page.goto(f'{url}/prototype/robot?zoom=0.62&t=9&shot')
            page.wait_for_function('robotPreview.ready')
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / 'scene.png'))
            browser.close()
    finally:
        server.shutdown()
    (OUT / 'perf.json').write_text(json.dumps(rows, indent=2) + '\n')
    lines = ['| Mode | Zoom | Sprites | Frame ms (mean) | p95 | fps | Draw ms | Runs (ms) |', '|---|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['mode']} | {r['zoom']} | {r['res']} | {r['frame_ms']} | {r['frame_p95_ms']} | "
                     f"{round(1000 / r['frame_ms'], 1)} | {r['draw_ms']} | {', '.join(map(str, r['frame_ms_runs']))} |")
    (OUT / 'perf.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print('median draw ms, SwiftShader:', statistics.median(r['draw_ms'] for r in rows if 'SwiftShader' in r['mode']))
    return 1 if any(r['errors'] for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
