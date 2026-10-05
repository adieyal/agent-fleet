"""Screenshot the workbench prototype at l2.png's size and set it beside the concept.

    uv run --group dev python art/scripts/shoot_bench.py [OUT_DIR]

Serves the deck over the test fixture, opens /prototype/bench?still&shot in headless Chromium at
1672 x 941, and writes bench.png and bench-vs-l2.png to OUT_DIR (default art/build/prototype).
"""
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from fleet.container import Container
from fleet_web.fixture import FixtureLibrary, FixtureState
from fleet_web.server import make_handler

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / 'tests' / 'fixtures' / 'restoke.json'
CONCEPT = REPO / 'docs' / 'images' / 'concept' / 'l2.png'
SIZE = {'width': 1672, 'height': 941}


def headless_env() -> dict[str, str]:
    """The environment without a display: with DISPLAY set (say a dead SSH X forward) ANGLE's Vulkan backend
    tries to reach X and WebGL fails; without it, it runs headless on the GPU."""
    return {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}


def shoot(out: Path) -> Path:
    state = FixtureState.load(FIXTURE, container=Container())
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state, FixtureLibrary(state.fixture, container=state.container)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            # ANGLE on Vulkan reaches the NVIDIA GPU headless; the GL paths fall back to llvmpipe
            browser = p.chromium.launch(args=['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-gpu',
                                              '--ignore-gpu-blocklist'], env=headless_env())
            page = browser.new_page(viewport=SIZE, device_scale_factor=1)
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: m.type == 'error' and errors.append(m.text))
            page.goto(f'http://127.0.0.1:{server.server_port}/prototype/bench?still&shot')
            try:
                page.wait_for_function('window.bench && window.bench.ready', timeout=120_000)
            except Exception:
                page.screenshot(path=str(out / 'bench-failed.png'))
                sys.exit('shoot_bench: page never became ready:\n' + '\n'.join(errors))
            shot = out / 'bench.png'
            page.screenshot(path=str(shot))
            browser.close()
    finally:
        server.shutdown()
    if errors:
        sys.exit('shoot_bench: page errors:\n' + '\n'.join(errors))
    return shot


def side_by_side(shot: Path, out: Path, title: str = 'prototype: /prototype/bench (three.js, baked)',
                 name: str = 'bench-vs-l2.png') -> Path:
    a, b = Image.open(shot).convert('RGB'), Image.open(CONCEPT).convert('RGB')
    pad, label = 16, 36
    img = Image.new('RGB', (a.width + b.width + pad * 3, max(a.height, b.height) + pad * 2 + label), 'white')
    img.paste(a, (pad, pad + label))
    img.paste(b, (a.width + pad * 2, pad + label))
    d = ImageDraw.Draw(img)
    d.text((pad, pad), title, fill='black')
    d.text((a.width + pad * 2, pad), 'concept: docs/images/concept/l2.png', fill='black')
    dest = out / name
    img.save(dest, optimize=True)
    return dest


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / 'art' / 'build' / 'prototype'
    out.mkdir(parents=True, exist_ok=True)
    print(side_by_side(shoot(out), out))


if __name__ == '__main__':
    main()
