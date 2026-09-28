"""Measure main-thread stalls while zooming the world view: wheel out from the bench to the far floor and back in,
in bursts with pauses (each pause settles the zoom and rebuilds the ground), recording long tasks and long animation
frames with a PerformanceObserver and every animation frame's interval, with and without a GPU. Run on home.

  uv run python scripts/world_longtask_bench.py [--scale 2] [--runs 3] [--out FILE]

Prints a JSON summary per mode and run."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "scripts")]

from world_zoom import LONG_TASKS, LONG_TASKS_READ, long_task_summary, rest, zoom_bursts  # noqa: E402
from world_zoom_bench import MODES, open_world, serve  # noqa: E402


def run(browser: Any, url: str, scale: float) -> dict[str, Any]:
    context, page = open_world(browser, url, scale)
    zoom_bursts(page, 1, bursts=3, ticks=14)   # (start close in, like the profile: zooming out from a bench)
    rest(page)
    page.wait_for_timeout(1000)
    page.evaluate(LONG_TASKS)
    zoom_bursts(page, -1)
    zoom_bursts(page, 1)
    rest(page)
    page.wait_for_timeout(1000)
    out = long_task_summary(page.evaluate(LONG_TASKS_READ))
    context.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--modes", default="no-gpu,gpu")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    url = serve()
    report: dict[str, Any] = {"scale": args.scale}
    with sync_playwright() as p:
        for mode in args.modes.split(","):
            browser = p.chromium.launch(channel="chromium", args=MODES[mode])
            report[mode] = [run(browser, url, args.scale) for _ in range(args.runs)]
            browser.close()
    print(json.dumps(report, indent=1))
    if args.out:
        args.out.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
