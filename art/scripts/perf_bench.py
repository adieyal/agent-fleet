"""Measure the workbench prototype: load size, time to first frame, and frame times while animating.

    uv run --group dev python art/scripts/perf_bench.py [WIDTHxHEIGHT] [SECONDS]

Runs headless Chromium on the GPU (ANGLE over Vulkan), reports the WebGL renderer so a software
fallback is visible, and prints median / p95 / worst frame times over the window.
"""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.server import make_handler
from shoot_bench import headless_env

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / 'tests' / 'fixtures' / 'restoke.json'

SAMPLE = """async seconds => {
  const gl = document.querySelector('canvas').getContext('webgl2');
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const times = [];
  let last = performance.now();
  await new Promise(done => {
    const end = last + seconds * 1000;
    const tick = now => { times.push(now - last); last = now; now < end ? requestAnimationFrame(tick) : done(); };
    requestAnimationFrame(tick);
  });
  times.sort((a, b) => a - b);
  const q = p => times[Math.min(times.length - 1, Math.floor(p * times.length))];
  return { renderer, frames: times.length, median_ms: q(0.5), p95_ms: q(0.95), worst_ms: times[times.length - 1],
           fps: 1000 / q(0.5), ready_ms: window.bench.readyAt };
}"""


def main() -> None:
    w, h = (int(v) for v in (sys.argv[1] if len(sys.argv) > 1 else '1440x900').split('x'))
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 6
    state = FixtureState.load(FIXTURE)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state, FixtureLibrary(state.fixture)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sizes: dict[str, int] = {}
    try:
        with sync_playwright() as p:
            # ANGLE on Vulkan reaches the NVIDIA GPU headless; the GL paths fall back to llvmpipe.
            # vsync and the frame cap are off so the numbers are real frame times, not 16.7 ms.
            browser = p.chromium.launch(args=['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-gpu',
                                              '--ignore-gpu-blocklist', '--disable-gpu-vsync',
                                              '--disable-frame-rate-limit'], env=headless_env())
            page = browser.new_page(viewport={'width': w, 'height': h})
            page.on('response', lambda r: sizes.__setitem__(r.url, len(r.body())) if r.ok else None)
            page.goto(f'http://127.0.0.1:{server.server_port}/prototype/bench')
            page.wait_for_function('window.bench && window.bench.ready', timeout=120_000)
            page.wait_for_timeout(1000)
            result = page.evaluate(SAMPLE, seconds)
            browser.close()
    finally:
        server.shutdown()
    world = sum(v for k, v in sizes.items() if '/assets/world/' in k)
    code = sum(v for k, v in sizes.items() if k.endswith('.js'))
    result.update(viewport=f'{w}x{h}', load_mb=round(sum(sizes.values()) / 1e6, 2),
                  world_mb=round(world / 1e6, 2), code_mb=round(code / 1e6, 2))
    print(json.dumps({k: round(v, 2) if isinstance(v, float) else v for k, v in result.items()}, indent=2))


if __name__ == '__main__':
    main()
