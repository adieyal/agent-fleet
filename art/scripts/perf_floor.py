"""Frame time of /prototype/floor with its robots walking (?loop), with the GPU disabled (SwiftShader) and enabled
(ANGLE GL), at 1440 x 900 and device pixel ratio 1 and 2, at the whole-floor (l1) and bench (l2) framings and
while zooming from one to the other and back.

    uv run --group dev python art/scripts/perf_floor.py OUT_DIR [--seconds 4] [--repeat 3]

The frame rate is uncapped, so the interval between frames is their true cost; each run is a fresh browser. Reports
the median over runs of the mean frame interval, its p95, fps and the engine's own work per frame. Writes
perf.json and perf.md.
"""
import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from conftest import FIXTURE, serve_fixture  # noqa: E402

UNCAPPED = ['--disable-frame-rate-limit', '--disable-gpu-vsync']
MODES = {
    'no GPU': ['--disable-gpu', *UNCAPPED],
    'GPU': ['--enable-gpu', '--ignore-gpu-blocklist', '--use-angle=gl', *UNCAPPED],
}
RECORD = """seconds => new Promise(done => {
  const e = floor.engine, frame = e.frame.bind(e), log = [];
  e.frame = () => { const s = performance.now(); frame(); log.push([s, performance.now() - s]); };
  setTimeout(() => {
    e.frame = frame;
    const iv = log.slice(1).map((x, i) => x[0] - log[i][0]), work = log.map(x => x[1]);
    const q = (a, p) => a.slice().sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(p * a.length))];
    const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
    done({ frames: log.length, interval: mean(iv), p95: q(iv, 0.95), work: mean(work), throttle: e.throttle,
           walking: floor.walkers.filter(w => w.state === 'walking').length });
  }, seconds * 1000);
})"""


def run(p, url: str, flags: list[str], dpr: int, zoom: str, seconds: float) -> dict:
    # headless Chromium reaches the GPU only through the display; without one, "GPU" silently means SwiftShader
    gpu = '--disable-gpu' not in flags
    env = dict(os.environ) if gpu else {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
    browser = p.chromium.launch(args=flags, env=env)
    page = browser.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=dpr)
    page.goto(f'{url}/prototype/floor?shot&loop' + ('&zoom=near' if zoom == 'l2' else ''))
    page.wait_for_function('window.floor && (window.floor.ready || window.floor.error)', timeout=90_000)
    page.wait_for_function('floor.walkers.filter(w => w.state === "walking").length === floor.walkers.length', timeout=60_000)
    page.wait_for_timeout(1000)   # the ground snapshot settles
    if zoom == 'zooming':   # the click's zoom onto the active bench and back out, while they walk
        page.evaluate("""setTimeout(() => floor.zoomTo('bench-0'), 50);
          setTimeout(() => { floor.engine.camera.frame(floor.layout.frames.far, 3.2); floor.engine.request(); }, 1850)""")
        seconds = 3.6
    out = page.evaluate(RECORD, seconds)
    out['renderer'] = page.evaluate("""(() => { const g = document.createElement('canvas').getContext('webgl');
      const d = g && g.getExtension('WEBGL_debug_renderer_info'); return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'none'; })()""")
    browser.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('out', type=Path)
    ap.add_argument('--seconds', type=float, default=4)
    ap.add_argument('--repeat', type=int, default=3)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    with serve_fixture(FIXTURE) as url, sync_playwright() as p:
        for mode, flags in MODES.items():
            for dpr in (1, 2):
                for zoom in ('l1', 'l2', 'zooming'):
                    runs = [run(p, url, flags, dpr, zoom, args.seconds) for _ in range(args.repeat)]
                    med = {k: statistics.median(r[k] for r in runs) for k in ('interval', 'p95', 'work', 'frames')}
                    row = {'mode': mode, 'dpr': dpr, 'framing': zoom, **med, 'fps': 1000 / med['interval'],
                           'throttle': max(r['throttle'] for r in runs), 'walking': runs[0]['walking'], 'renderer': runs[0]['renderer']}
                    rows.append(row)
                    print(f"{mode:7s} dpr {dpr} {zoom}: {row['interval']:.1f} ms (p95 {row['p95']:.1f}), {row['fps']:.0f} fps, "
                          f"work {row['work']:.2f} ms, {row['walking']} walking, throttle x{row['throttle']}", flush=True)
    (args.out / 'perf.json').write_text(json.dumps(rows, indent=1) + '\n')
    md = ['| Mode | DPR | Framing | Frame ms (mean) | p95 | fps | Engine work ms | Ambient throttle |', '|---|---|---|---|---|---|---|---|']
    md += [f"| {r['mode']} | {r['dpr']} | {r['framing']} | {r['interval']:.1f} | {r['p95']:.1f} | {r['fps']:.0f} | {r['work']:.2f} | x{r['throttle']} |" for r in rows]
    md += ['', f'1440 x 900, {rows[0]["walking"]} robots walking, frame rate uncapped; median of {args.repeat} runs of {args.seconds:g} s, '
           f'each in a fresh browser. GPU renderer: {next(r["renderer"] for r in rows if r["mode"] == "GPU")}.']
    (args.out / 'perf.md').write_text('\n'.join(md) + '\n')


if __name__ == '__main__':
    main()
