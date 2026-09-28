"""Views of /prototype/floor for a review, one per point, at the concept images' size (1672 x 941).

    uv run --group dev python art/scripts/shoot_review.py OUT_DIR [LABEL]

Writes <view>-<label>.png for: l1 (the whole floor), l2 (the workarea bench), seat (the seated robots, closest zoom),
lift (the lift and its floor indicator), surfaces (bare wall and floor, no furniture), store (the crate corner).
Robots are seated (?seated) so every view is still. Views are world framings (target and height), so before and
after compare the same place even when the layout moves things.
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from conftest import FIXTURE, serve_fixture  # noqa: E402

W, H = 1672, 941
# name: JS returning a framing from the page's layout
VIEWS = {
    'l1': 'floor.layout.frames.far',
    'l2': 'floor.layout.frames.near',
    'seat': "({ target: floor.layout.frames.near.target, height: 3.4 })",
    'lift': "({ target: [floor.layout.lift.at[0] + 0.6, floor.layout.lift.at[1] - 1.2, 1.4], height: 4.2 })",
    'surfaces': "({ target: [2.0, 1.2, 0.6], height: 4.2 })",
    'store': "(floor.layout.store ? { target: floor.layout.store.frame.target, height: floor.layout.store.frame.height }"
             " : { target: [1.6, floor.layout.size.d - 1.2, 1.2], height: 4.6 })",
    # review 2: the library against the back wall, a long run of bare back wall, and the seated robots up close
    'shelves': "({ target: [4.6, floor.layout.size.d - 0.9, 1.1], height: 3.6 })",
    'wall': "({ target: [7.5, floor.layout.size.d - 1.5, 1.5], height: 5.2 })",
    'fringe': "({ target: floor.layout.frames.near.target, height: 2.2 })",
}


def main() -> None:
    out = Path(sys.argv[1])
    label = sys.argv[2] if len(sys.argv) > 2 else 'now'
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
    with serve_fixture(FIXTURE) as url, sync_playwright() as p:
        browser = p.chromium.launch(env=env)
        page = browser.new_page(viewport={'width': W, 'height': H})
        page.goto(f'{url}/prototype/floor?shot&seated')
        page.wait_for_function('window.floor && (window.floor.ready || window.floor.error)', timeout=90_000)
        if page.evaluate('window.floor.error'):
            sys.exit(page.evaluate('window.floor.error'))
        for name, frame in VIEWS.items():
            # a framing closer than the camera's near limit is allowed here: widen the limit for the shot
            page.evaluate(f"""import('/js/world/projection.js').then(p => {{
              const c = floor.engine.camera, f = {frame}, v = p.fit(f, c.W, c.H);
              c.max = {{ ...c.max, ppm: Math.max(c.max.ppm, v.ppm) }};
              c.jump(v); floor.engine.request(); }})""")
            page.wait_for_function('!floor.engine.camera.moving && !floor.engine.ground.building && !floor.engine.raf', timeout=30_000)
            page.wait_for_timeout(400)
            page.screenshot(path=str(out / f'{name}-{label}.png'))
        browser.close()
    print('shot', out, label)


if __name__ == '__main__':
    main()
