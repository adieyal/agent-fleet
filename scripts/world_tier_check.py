"""Check that every tier of every kit piece and robot frame lands where its neighbours do: each pair of neighbouring
tiers is drawn through the engine at the zoom where they swap, and their alpha centroids and quantile edges compared.
Prints the pairs off by more than 0.5 screen px and exits 1 if there are any. Run on home, not carbon.

  uv run python scripts/world_tier_check.py [--frames N] [--clips Walking,Idle]"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "scripts")]

from world_zoom import AGREEMENT  # noqa: E402
from world_zoom_bench import open_world, serve  # noqa: E402

LOOK = "{ host: '#6b8cff', kit: 'antenna', agent: 'claude' }"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--frames", type=int, default=0, help="frames per robot sprite (0: all)")
    parser.add_argument("--clips", default="", help="robot clips to check (default: all)")
    args = parser.parse_args()
    url = serve()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium")
        context, page = open_world(browser, url, 1)
        kit = page.evaluate("[...fleetWorld.engine.sprites.values()].filter(s => !s.compose && s.tiers.length > 1).map(s => s.id)")
        robots = page.evaluate(f"""(clips => {{ const R = fleetWorld.robots, look = {LOOK};
          return (clips.length ? clips : Object.keys(R.man.clips)).flatMap(c => Object.keys(R.man.clips[c].dirs)
            .flatMap(d => ['all', 'low', 'high'].map(part => R.sprite(look, c, d, part)))); }})""",
                               [c for c in args.clips.split(",") if c])
        pairs = page.evaluate(AGREEMENT, {"ids": kit, "frames": 0})
        for i in range(0, len(robots), 12):   # (a batch at a time: composed frames are kept only up to a budget)
            pairs += page.evaluate(AGREEMENT, {"ids": robots[i:i + 12], "frames": args.frames})
        context.close()
        browser.close()
    bad = sorted((q for q in pairs if q["px"] > 0.5), key=lambda q: -q["px"])
    for q in bad:
        print(f"{q['id']:60} tiers {q['tiers']} frame {q.get('frame', 0):3}  {q['px']:6.2f} px ({q['which']})  centroid {q['centroid']:.2f} px")
    print(f"{len(pairs)} tier pairs, {len(bad)} off by more than 0.5 px")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
