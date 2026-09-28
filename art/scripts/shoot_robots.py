"""Views of the v2 robots on /prototype/floor at the concept images' size (1672 x 941).

    uv run --group dev python art/scripts/shoot_robots.py OUT_DIR

Writes far.png (the whole floor), mid.png (halfway in), close.png (the workarea bench at its l2 framing), desk-row.png
(the seated robots up close: desk occlusion and their shadows under their chairs) and box-walk.png (a robot carrying a
parcel to the storage corner, ?as=0:ship, followed up close). The first four have the robots seated (?seated).
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from conftest import FIXTURE, serve_fixture  # noqa: E402

W, H = 1672, 941
FIT = """f => import('/js/world/projection.js').then(p => {
  const c = floor.engine.camera, v = p.fit(f, c.W, c.H);
  c.max = { ...c.max, ppm: Math.max(c.max.ppm, v.ppm) }; c.jump(v); floor.engine.request(); })"""
REST = '!floor.engine.camera.moving && !floor.engine.ground.building'


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
    with serve_fixture(FIXTURE) as url, sync_playwright() as p:
        browser = p.chromium.launch(env=env)
        page = browser.new_page(viewport={'width': W, 'height': H})
        page.goto(f'{url}/prototype/floor?shot&seated')
        page.wait_for_function('window.floor && (window.floor.ready || window.floor.error)', timeout=90_000)
        views = {
            'far': 'floor.layout.frames.far',
            'mid': "({ target: floor.layout.frames.near.target, height: 9.0 })",
            'close': 'floor.layout.frames.near',
            'desk-row': "({ target: [floor.layout.runs[1].seat[0], floor.layout.runs[1].seat[1] - 0.4, 0.55], height: 2.4 })",
        }
        for name, frame in views.items():
            page.evaluate(f'(() => {{ const f = {frame}; return ({FIT})(f); }})()')
            page.wait_for_function(REST, timeout=30_000)
            page.wait_for_timeout(900)
            page.screenshot(path=str(out / f'{name}.png'))
        page.goto(f'{url}/prototype/floor?shot&as=0:ship')
        page.wait_for_function('window.floor && window.floor.ready', timeout=90_000)
        # (out on the open floor between the benches, clear of the lectern and the desks)
        page.wait_for_function("(m => m.clip === 'BoxWalk' && m.at[0] < 12.5 && m.at[0] > 8)(floor.crew.members[0])", timeout=60_000)
        # (the robots stop where they are for the shot, walking on the spot, while the camera settles on this one)
        page.evaluate('floor.crew.step = () => false')
        at = page.evaluate('floor.crew.members[0].at')
        page.evaluate(f'({FIT})({{ target: [{at[0]}, {at[1]}, 0.6], height: 2.6 }})')
        page.wait_for_function(REST, timeout=30_000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(out / 'box-walk.png'))
        browser.close()
    print('shot', out)


if __name__ == '__main__':
    main()
