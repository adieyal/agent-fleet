"""Record a short webm of /prototype/floor: robots leave the lift and walk to their desks, a click on the active
bench zooms onto it (l2's framing), they sit, and Escape zooms back out to the whole floor (l1).

    uv run --group dev python art/scripts/record_floor.py OUT.webm
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from conftest import FIXTURE, serve_fixture  # noqa: E402

W, H = 1440, 900


def main() -> None:
    dest = Path(sys.argv[1])
    env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
    tmp = Path(tempfile.mkdtemp())
    with serve_fixture(FIXTURE) as url, sync_playwright() as p:
        browser = p.chromium.launch(args=['--enable-gpu', '--ignore-gpu-blocklist', '--use-angle=gl'], env=env)
        ctx = browser.new_context(viewport={'width': W, 'height': H}, record_video_dir=str(tmp), record_video_size={'width': W, 'height': H})
        page = ctx.new_page()
        page.goto(f'{url}/prototype/floor?shot')
        page.wait_for_function('window.floor && (window.floor.ready || window.floor.error)', timeout=90_000)
        page.wait_for_timeout(3500)   # the first robots are out of the lift
        at = page.evaluate("""import('/js/world/projection.js').then(p => {
          const b = floor.layout.benches.find(b => b.key === 'lane-0');
          return p.toScreen(floor.engine.camera.view, [b.at[0] + 1.8, b.at[1] - 0.2, 0.74]); })""")
        page.mouse.click(*at)
        page.wait_for_function('floor.seated.length === floor.walkers.length', timeout=60_000)
        page.wait_for_timeout(2500)
        page.keyboard.press('Escape')
        page.wait_for_timeout(2500)
        video = page.video.path()
        ctx.close()
        browser.close()
    shutil.move(video, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    print('recorded', dest, f'{dest.stat().st_size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
