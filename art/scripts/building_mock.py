"""Stack the shipped building pieces into a mock of the L0 view from manifest.json alone, the way the browser will:
the plinth is the canvas and its anchor the origin, every piece's anchor pixel goes on its level, drawn in the
manifest's order. Plates, rings, the focus switch and the lantern are plain placeholders on their slots (in the view
they are live DOM).

    python3 art/scripts/building_mock.py [PIECES_DIR] OUT_DIR [--tier 2]

Writes OUT_DIR/building-mock.png at l0's size (1672 x 941) and building-vs-l0.png (l0 left, the mock right).
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
L0 = REPO / 'docs' / 'images' / 'concept' / 'l0.png'
PIECES = REPO / 'packages' / 'fleet-web' / 'src' / 'fleet_web' / 'static' / 'assets' / 'world' / 'building'
# top to bottom, as the concept: (state, plate); a free floor's plate is blank
FLOORS = [('open-lit', 'North'), ('open-lit', 'Atlas'), ('lab-lit', 'Lab'), ('glass-lit', 'Harbor'),
          ('glass-unlit', 'Delta'), ('free', None)]
LANTERN_ON = 'Atlas'      # the floor with attention, as l0
CRATES = 6
# where the lobby floor's front-left corner sits in l0's frame (tier 1 px): the mock is cropped to l0's view
ORIGIN_IN_L0 = (425, 765)


def font(size):
    for f in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def compose(folder: Path, tier: str) -> Image.Image:
    m = json.loads((folder / 'manifest.json').read_text())
    t = m['tiers'][tier]
    k = int(tier)
    n = len(FLOORS)

    def piece(name):
        return m['pieces'][name]['tiers'][tier]
    plinth = piece(f'plinth-{n}')
    canvas = Image.new('RGBA', tuple(plinth['size']), m['backdrop'])
    ox, oy = plinth['anchor_px']

    def level(i):   # 0 the lobby, 1..n the floors, n + 1 the roof
        return oy if i == 0 else oy - t['lobby_step_px'] - (i - 1) * t['step_px']

    def place(name, i):
        p = piece(name)
        canvas.alpha_composite(Image.open(folder / p['file']).convert('RGBA'), (ox - p['anchor_px'][0], level(i) - p['anchor_px'][1]))
        return p

    floors = list(reversed(FLOORS))   # bottom up
    place(f'plinth-{n}', 0)
    place(f'annex-{CRATES}', 0)
    spines = [place('spine-lobby', 0)] + [place('spine', i) for i in range(1, n + 1)]
    place('spine-cap', n + 1)
    lantern_level = next(i + 1 for i, (_, label) in enumerate(floors) if label == LANTERN_ON)
    bracket = place('lantern-bracket', lantern_level)
    place('lobby', 0)
    for i, (state, _) in enumerate(floors):
        kind, _, lit = state.partition('-')
        top = i == n - 1
        place(f'floor-{kind}' + ('-top' if top else '') + (f'-{lit}' if lit else ''), i + 1)
    place('roof', n + 1)
    place('lift-lobby', 0)
    for i in range(1, n + 1):
        place('lift', i)
    place('lift-cap', n + 1)
    # placeholders for the live DOM on the manifest's slots
    d = ImageDraw.Draw(canvas)
    f = font(13 * k)
    for i, (sp, label) in enumerate(zip(spines, ['Lobby'] + [lb for _, lb in floors])):
        y = level(i)
        (px, py), (pw, ph) = sp['slots']['plate'], sp['boxes']['plate']
        (rx, ry), (rd, _) = sp['slots']['ring'], sp['boxes']['ring']
        if label:
            d.rounded_rectangle((ox + px - pw / 2, y + py - ph / 2, ox + px + pw / 2, y + py + ph / 2), 3 * k,
                                fill=(38, 38, 42, 255), outline=(150, 150, 155, 255), width=k)
            d.text((ox + px, y + py), label, font=f, fill=(240, 240, 240, 255), anchor='mm')
        r = rd / 2
        d.ellipse((ox + rx - r, y + ry - r, ox + rx + r, y + ry + r), outline=(130, 130, 136, 255), width=3 * k)
    lx, ly = bracket['slots']['lantern']
    cx, cy = ox + lx, level(lantern_level) + ly + 14 * k
    d.polygon([(cx, cy - 16 * k), (cx + 10 * k, cy), (cx, cy + 16 * k), (cx - 10 * k, cy)], fill=(214, 60, 214, 255))
    # crop to l0's view at tier 1
    s = int(tier)
    x0, y0 = ox - ORIGIN_IN_L0[0] * s, oy - ORIGIN_IN_L0[1] * s
    view = Image.new('RGBA', (1672 * s, 941 * s), m['backdrop'])
    view.alpha_composite(canvas, (-x0, -y0) if x0 < 0 or y0 < 0 else (0, 0), (max(x0, 0), max(y0, 0)))
    return view.resize((1672, 941), Image.LANCZOS) if s != 1 else view


def main() -> None:
    args = sys.argv[1:]
    tier = '2'
    if '--tier' in args:
        i = args.index('--tier'); tier = args[i + 1]; del args[i:i + 2]
    folder, out = (Path(args[0]), Path(args[1])) if len(args) > 1 else (PIECES, Path(args[0]))
    out.mkdir(parents=True, exist_ok=True)
    mock = compose(folder, tier).convert('RGB')
    mock.save(out / 'building-mock.png', optimize=True)
    l0 = Image.open(L0).convert('RGB')
    pair = Image.new('RGB', (l0.width * 2, l0.height), 'white')
    pair.paste(l0, (0, 0))
    pair.paste(mock, (l0.width, 0))
    pair.save(out / 'building-vs-l0.png', optimize=True)
    print('mock:', out / 'building-mock.png')


if __name__ == '__main__':
    main()
