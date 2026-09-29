"""Stack the building pieces (art/scripts/building_pieces.py) into a mock of the L0 view, the way the browser will:
every piece's anchor pixel on its level's point, levels a whole number of pixels apart, drawn back to front. Plates and
rings are plain placeholders at the spine's slots (in the view they are live DOM).

    python3 art/scripts/building_mock.py BUILD_DIR OUT_DIR

Writes OUT_DIR/building-mock.png (the concept's size, 1672 x 941, at tier 1) and building-vs-l0.png (l0 left, the
mock right).
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
L0 = REPO / 'docs' / 'images' / 'concept' / 'l0.png'
# top to bottom, as the concept: (piece, plate); None: a free floor's plate is blank
FLOORS = [('floor-open-lit', 'North'), ('floor-open-unlit', 'Atlas'), ('floor-glass-lit', 'Harbor'),
          ('floor-glass-unlit', 'Delta'), ('floor-glass-unlit', None)]
ORIGIN_1X = (433, 752)    # where the lobby floor's front-left corner sits in l0's frame


def font(size):
    for f in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def compose(build: Path) -> Image.Image:
    info = json.loads((build / 'pieces.json').read_text())
    pieces = info['pieces']
    mult = pieces['lobby']['mult']
    step, lobby_step = info['step_px'] * mult, info['lobby_step_px'] * mult
    w, h = 1672 * mult, 941 * mult
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ox, oy = ORIGIN_1X[0] * mult, ORIGIN_1X[1] * mult

    def level(i):   # screen y of level i's anchor: 0 the lobby, 1.. the floors, len+1 the roof
        return oy if i == 0 else oy - lobby_step - (i - 1) * step

    def place(name, y):
        p = pieces[name]
        im = Image.open(build / p['file']).convert('RGBA')
        canvas.alpha_composite(im, (ox - p['anchor_px'][0], y - p['anchor_px'][1]))
        return p

    n = len(FLOORS)
    top = level(n + 1)
    plinth = pieces['plinth']
    bg = Image.open(build / plinth['file']).convert('RGBA')
    canvas.paste(bg.getpixel((4, 4)), (0, 0, w, h))   # the backdrop: the plinth's own far ground
    place('plinth', oy)
    place('annex', oy)
    spines = [place('spine-lobby', level(0))] + [place('spine', level(i)) for i in range(1, n + 1)]
    place('spine-cap', top)
    place('lobby', level(0))
    for i, (name, _) in enumerate(reversed(FLOORS)):
        place(name, level(i + 1))
    place('roof', top)
    place('lift-lobby', level(0))
    for i in range(1, n + 1):
        place('lift', level(i))
    place('lift-cap', top)
    # placeholder plates and rings on the spine's slots
    d = ImageDraw.Draw(canvas)
    f = font(15 * mult)
    names = ['Lobby'] + [p for _, p in reversed(FLOORS)]
    for i, (sp, label) in enumerate(zip(spines, names)):
        y = level(i)
        px, py = ox + sp['slots']['plate'][0], y + sp['slots']['plate'][1]
        rx, ry = ox + sp['slots']['ring'][0], y + sp['slots']['ring'][1]
        if label:
            d.rounded_rectangle((px - 36 * mult, py - 14 * mult, px + 36 * mult, py + 14 * mult), 3 * mult,
                                fill=(38, 38, 42, 255), outline=(150, 150, 155, 255), width=mult)
            d.text((px, py), label, font=f, fill=(240, 240, 240, 255), anchor='mm')
        r = 15 * mult
        d.ellipse((rx - r, ry - r, rx + r, ry + r), outline=(120, 120, 126, 255), width=4 * mult)
        d.ellipse((rx - r + 4 * mult, ry - r + 4 * mult, rx + r - 4 * mult, ry + r - 4 * mult), fill=(236, 236, 240, 255))
    return canvas.resize((1672, 941), Image.LANCZOS) if mult != 1 else canvas


def main() -> None:
    build, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    mock = compose(build).convert('RGB')
    mock.save(out / 'building-mock.png', optimize=True)
    l0 = Image.open(L0).convert('RGB')
    pair = Image.new('RGB', (l0.width * 2, l0.height), 'white')
    pair.paste(l0, (0, 0))
    pair.paste(mock.resize(l0.size), (l0.width, 0))
    pair.save(out / 'building-vs-l0.png', optimize=True)
    print('mock:', out / 'building-mock.png')


if __name__ == '__main__':
    main()
