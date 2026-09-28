"""Side-by-sides of /prototype/floor against the concept images, at the concept images' size (1672 x 941).

    uv run --group dev python art/scripts/shoot_floor.py OUT_DIR

Writes floor-l1.png and floor-l2.png (the render), and vs-l1.jpg, vs-l2.jpg: the render beside the concept image,
with a 50% blend of the two underneath. The floor is shown with its robots seated (?seated), so the frames compare
like with like. l1 is also compared mirrored (vs-l1-mirrored.jpg): the floor puts the lift on the left, as l2 does,
where l1 draws it on the right (docs/design/sprite-world.md, "Floor layout").
"""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from conftest import FIXTURE, serve_fixture  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
W, H = 1672, 941


def compare(render: Path, concept: Image.Image, dest: Path, label: str) -> None:
    ours = Image.open(render).convert('RGB')
    concept = concept.convert('RGB').resize((W, H))
    sheet = Image.new('RGB', (W * 2, H * 2), 'white')
    sheet.paste(ours, (0, 0))
    sheet.paste(concept, (W, 0))
    sheet.paste(Image.blend(ours, concept, 0.5), (W // 2, H))
    g = ImageDraw.Draw(sheet)
    for x, y, text in ((12, 12, 'floor prototype'), (W + 12, 12, label), (W // 2 + 12, H + 12, '50% blend')):
        g.rectangle((x - 4, y - 2, x + 8 * len(text) + 4, y + 16), fill='white')
        g.text((x, y), text, fill='black')
    sheet.save(dest, quality=85)


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
    with serve_fixture(FIXTURE) as url, sync_playwright() as p:
        browser = p.chromium.launch(env=env)
        for name, query in (('l1', 'shot&seated'), ('l2', 'shot&seated&zoom=near')):
            page = browser.new_page(viewport={'width': W, 'height': H})
            page.goto(f'{url}/prototype/floor?{query}')
            page.wait_for_function('window.floor && (window.floor.ready || window.floor.error)', timeout=60_000)
            if page.evaluate('window.floor.error'):
                sys.exit(page.evaluate('window.floor.error'))
            page.wait_for_function('!floor.engine.camera.moving && !floor.engine.ground.building && !floor.engine.raf', timeout=30_000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / f'floor-{name}.png'))
            page.close()
        browser.close()
    l1 = Image.open(REPO / 'docs/images/concept/l1.png')
    compare(out / 'floor-l1.png', l1, out / 'vs-l1.jpg', 'l1.png')
    compare(out / 'floor-l1.png', ImageOps.mirror(l1), out / 'vs-l1-mirrored.jpg', 'l1.png, mirrored')
    compare(out / 'floor-l2.png', Image.open(REPO / 'docs/images/concept/l2.png'), out / 'vs-l2.jpg', 'l2.png')
    print('shot', out)


if __name__ == '__main__':
    main()
