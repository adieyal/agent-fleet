"""Screenshot each bake-off variant laid out like l2.png, beside the concept.

    uv run --group dev python art/scripts/shoot_bakeoff.py [OUT_DIR [VARIANT...]]

Serves the repository root, opens art/bakeoff/layout.html?variant=A and ?variant=B1 in headless Chromium at
1672 x 941, and writes art/bakeoff/<variant>/layout.jpg plus OUT_DIR/bakeoff-<variant>-vs-l2.png.
"""
import functools
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from shoot_bench import CONCEPT, REPO, headless_env, side_by_side

SIZE = {'width': 1672, 'height': 941}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / 'art' / 'build' / 'bakeoff'
    variants = sys.argv[2:] or ['A', 'B1']
    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(REPO)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-gpu',
                                              '--ignore-gpu-blocklist'], env=headless_env())
            for variant in variants:
                page = browser.new_page(viewport=SIZE)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: m.type == 'error' and errors.append(m.text))
                page.goto(f'http://127.0.0.1:{server.server_port}/art/bakeoff/layout.html?variant={variant}')
                page.wait_for_function('window.layoutReady === true', timeout=120_000)
                page.wait_for_timeout(500)
                shot = out / f'layout-{variant}.png'
                page.screenshot(path=str(shot))
                page.close()
                Image.open(shot).convert('RGB').save(REPO / 'art' / 'bakeoff' / variant / 'layout.jpg', quality=88)
                title = {'A': 'variant A: pre-lit 3D (unlit glTF, baked studio look)',
                         'B1': 'variant B1: Blender sprites (Cycles, transparent PNG, 1x)'}[variant]
                print(side_by_side(shot, out, title, f'bakeoff-{variant}-vs-l2.png'))
            browser.close()
    finally:
        server.shutdown()
    if errors:
        sys.exit('shoot_bakeoff: page errors:\n' + '\n'.join(errors))


if __name__ == '__main__':
    main()
