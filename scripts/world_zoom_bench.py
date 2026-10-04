"""Measure the world view's zoom: frame times during a continuous wheel zoom from the far floor to the close bench
and back, time to sharp after it stops, how much sprites and ground are upscaled, and the largest jump of drawn
content at a tier swap, with and without a GPU. Run on home, not carbon.

  uv run python scripts/world_zoom_bench.py [--out DIR] [--scale 2] [--video]

Prints a JSON summary; with --out, also a frame strip (PNG crops around a robot during the zoom) and, with
--video, a webm of each zoom."""

import argparse
import base64
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fleet.container import configured_container
from fleet.web.fixture import FixtureLibrary, FixtureState  # noqa: E402
from fleet.web.server import make_handler  # noqa: E402
from world_zoom import PROBE, TIMER, blur, jumps, percentile, rest, time_to_sharp, zoom  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "restoke.json"
MODES = {
    "no-gpu": ["--disable-gpu", "--disable-gpu-compositing"],
    "gpu": ["--enable-gpu", "--ignore-gpu-blocklist", "--use-angle=vulkan", "--enable-features=Vulkan"],
}

# a crop of the canvas around the first bench's robots, grabbed inside the frame loop
STRIP = """(() => {
  const w = fleetWorld.engine, shots = w.zoomShots = [], frame = w.frame;
  let n = 0;
  w.frame = function () {
    frame.call(this);
    if (n++ % 4) return;
    const c = document.createElement('canvas'), W = 260, H = 200, el = this.el;
    c.width = W; c.height = H;
    c.getContext('2d').drawImage(el, el.width / 2 - W / 2, el.height / 2 - H / 2, W, H, 0, 0, W, H);
    shots.push({ t: performance.now(), ppm: this.camera.view.ppm, png: c.toDataURL('image/png') });
  };
})()"""


def serve() -> str:
    state = FixtureState.load(FIXTURE, container=configured_container())
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state, FixtureLibrary(state.fixture, container=configured_container())))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def open_world(browser: Any, url: str, scale: float, video: Path | None = None) -> Any:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=scale,
                                  **({"record_video_dir": str(video), "record_video_size": {"width": 1440, "height": 900}} if video else {}))
    page = context.new_page()
    page.goto(url + "/?view=world")
    page.wait_for_function("window.fleetWorld && (fleetWorld.ready || fleetWorld.error)", timeout=60_000)
    assert page.evaluate("fleetWorld.error") is None
    rest(page)
    return context, page


def frame_times(browser: Any, url: str, scale: float) -> dict[str, Any]:
    context, page = open_world(browser, url, scale)
    page.evaluate(TIMER)
    out = {}
    for name, direction in (("in", 1), ("out", -1)):
        page.evaluate("fleetWorld.engine.zoomTimer.frames = []")
        zoom(page, direction)
        rest(page)
        page.wait_for_timeout(1500)   # (anything still to come: a late tier, a rebuild)
        frames = page.evaluate("fleetWorld.engine.zoomTimer.frames")
        moving = [f for f in frames if f["moving"]]
        gaps = [b["t"] - a["t"] for a, b in zip(moving, moving[1:])]
        work = [f["work"] for f in moving]
        out[name] = {"frames": len(moving), "work_p50": percentile(work, 0.5), "work_p95": percentile(work, 0.95),
                     "work_max": max(work or [0]), "gap_p50": percentile(gaps, 0.5), "gap_p95": percentile(gaps, 0.95),
                     "gap_max": max(gaps or [0]), "low_motion": any(f["lowMotion"] for f in frames),
                     "time_to_sharp_s": time_to_sharp(frames)}
    context.close()
    return out


def quality(browser: Any, url: str, scale: float, low_motion: bool) -> dict[str, Any]:
    context, page = open_world(browser, url, scale)
    # (the probe's pixel readbacks are slow: motion is drawn as the unprobed run found, not switched by them)
    page.evaluate(f"fleetWorld.engine.motionDpr = 'fixed'; fleetWorld.engine.lowMotion = {'true' if low_motion else 'false'}")
    page.evaluate(PROBE)
    out = {}
    for name, direction in (("in", 1), ("out", -1)):
        page.evaluate("fleetWorld.engine.zoomProbe.frames = []")
        zoom(page, direction)
        rest(page)
        page.wait_for_timeout(1500)   # (anything still to come: a late tier, a rebuild)
        frames = page.evaluate("fleetWorld.engine.zoomProbe.frames")
        out[name] = {"frames": len(frames), **blur([f for f in frames if f["moving"]]),
                     "at_rest": blur([f for f in frames if not f["moving"]][-1:]),
                     **jumps(frames)}
    context.close()
    return out


def strip(browser: Any, url: str, scale: float, out: Path, video: bool) -> None:
    context, page = open_world(browser, url, scale, out / "video" if video else None)
    page.evaluate(STRIP)
    zoom(page, 1)
    rest(page)
    page.wait_for_timeout(1500)
    shots = page.evaluate("fleetWorld.engine.zoomShots")
    for i, s in enumerate(shots):
        (out / f"strip-{i:03d}-ppm{s['ppm']:.0f}.png").write_bytes(base64.b64decode(s["png"].split(",", 1)[1]))
    context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--modes", default="no-gpu,gpu")
    args = parser.parse_args()
    url = serve()
    report: dict[str, Any] = {"scale": args.scale}
    with sync_playwright() as p:
        for mode in args.modes.split(","):
            # (the full Chromium in new headless mode, which can use the GPU; the headless shell never does)
            browser = p.chromium.launch(channel="chromium", args=MODES[mode])
            page = browser.new_page()
            renderer = page.evaluate("""(() => { const g = document.createElement('canvas').getContext('webgl');
              const e = g && g.getExtension('WEBGL_debug_renderer_info'); return g ? g.getParameter(e ? e.UNMASKED_RENDERER_WEBGL : g.RENDERER) : null; })()""")
            page.close()
            times = frame_times(browser, url, args.scale)
            report[mode] = {"gl_renderer": renderer, "frame_times": times,
                            "quality": quality(browser, url, args.scale, times["in"]["low_motion"])}
            if args.out and mode == "no-gpu":
                args.out.mkdir(parents=True, exist_ok=True)
                strip(browser, url, args.scale, args.out, args.video)
            browser.close()
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        (args.out / "zoom-bench.json").write_text(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
