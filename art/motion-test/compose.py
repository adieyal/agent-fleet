"""Contact sheets and side-by-side webms from render_motion.py output (run with any Python that has Pillow).

    python compose.py sheet <clip_dir> <out.png> <title> <label>[:<caption>] ...
    python compose.py webm  <clip_dir> <out.webm> <ffmpeg> <label>[:<caption>] ...
    python compose.py review <render_motion review dir> <out dir> <robot-sheet pose png>
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (198, 203, 230, 255)  # the floor tile colour (build_workbench floor_tile #c6cbe6)
FONT = ImageFont.load_default(size=22)


def flat(im: Image.Image) -> Image.Image:
    bg = Image.new('RGBA', im.size, BG)
    bg.alpha_composite(im.convert('RGBA'))
    return bg


def rows(clip: Path, specs, prefix: str):
    out = []
    for s in specs:
        label, _, cap = s.partition(':')
        out.append((cap or label, sorted((clip / label).glob(f'{prefix}_*.png'))))
    return out


def sheet(clip: Path, dest: Path, title: str, specs) -> None:
    rs = rows(clip, specs, 'sheet')
    w, h = Image.open(rs[0][1][0]).size
    n = max(len(f) for _, f in rs)
    head, lab = 40, 34
    im = Image.new('RGBA', (w * n, head + len(rs) * (h + lab)), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((10, 8), title, fill='black', font=FONT)
    for r, (cap, files) in enumerate(rs):
        y = head + r * (h + lab)
        d.text((10, y + 6), cap, fill='black', font=FONT)
        for c, f in enumerate(files):
            im.paste(flat(Image.open(f)), (c * w, y + lab))
    im.convert('RGB').save(dest, optimize=True)
    small = im.resize((im.width // 2, im.height // 2), Image.LANCZOS)  # 1x: the floor's sprite density
    small.convert('RGB').save(dest.with_name(dest.stem + '_1x.png'), optimize=True)


def webm(clip: Path, dest: Path, ffmpeg: str, specs) -> None:
    rs = rows(clip, specs, 'all')
    n = max(len(f) for _, f in rs)
    w, h = Image.open(rs[0][1][0]).size
    w, h = w * 2, h * 2  # frames are 1x sprites; shown 2x with nearest-neighbour so pixels stay honest
    lab = 34
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            im = Image.new('RGBA', (w * len(rs), h + lab), (255, 255, 255, 255))
            d = ImageDraw.Draw(im)
            for c, (cap, files) in enumerate(rs):
                d.text((c * w + 8, 6), cap, fill='black', font=FONT)
                fr = flat(Image.open(files[i % len(files)]))  # shorter clips loop
                im.paste(fr.resize((w, h), Image.NEAREST), (c * w, lab))
            im.convert('RGB').save(f'{tmp}/{i:05d}.png')
        subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-framerate', '30', '-i', f'{tmp}/%05d.png',
                        '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '34',
                        '-pix_fmt', 'yuv420p', str(dest)], check=True)


if __name__ == '__main__' and sys.argv[1] != 'review':
    a = sys.argv[1:]
    if a[0] == 'sheet':
        sheet(Path(a[1]), Path(a[2]), a[3], a[4:])
    else:
        webm(Path(a[1]), Path(a[2]), a[3], a[4:])


def review(src: Path, dest: Path, sheet_png: Path) -> None:
    """Assembly review boards: turnaround next to the sheet, close-ups, bench frames (on the floor colour)."""
    dest.mkdir(parents=True, exist_ok=True)

    def board(items, h, out):
        ims = []
        for cap, im in items:
            im = im.convert('RGBA')
            im = im.resize((round(im.width * h / im.height), h), Image.LANCZOS)
            ims.append((cap, flat(im)))
        W = sum(i.width for _, i in ims) + 10 * (len(ims) - 1)
        b = Image.new('RGBA', (W, h + 40), (255, 255, 255, 255))
        d = ImageDraw.Draw(b)
        x = 0
        for cap, im in ims:
            b.paste(im, (x, 40))
            d.text((x + 6, 8), cap, fill='black', font=FONT)
            x += im.width + 10
        b.convert('RGB').save(out, optimize=True)

    target = Image.open(sheet_png)
    turns = [(f'B1 camera, turned {a} deg', Image.open(src / f'turn_{a:03d}.png')) for a in range(0, 360, 45)]
    board([('target: robot-sheet teal standing', target), ('parts robot, sheet-like angle',
           Image.open(src / 'sheet_angle.png')), ('parts robot, front', Image.open(src / 'front.png'))],
          900, dest / '01_vs_sheet.png')
    board([('target', target)] + turns, 900, dest / '02_turnaround.png')
    board([(n.replace('close_', '').replace('_', ' '), Image.open(src / f'{n}.png')) for n in
           ('close_head_b1', 'close_head_front', 'close_head_back')], 800, dest / '03_close_head.png')
    board([(n.replace('close_', '').replace('_', ' '), Image.open(src / f'{n}.png')) for n in
           ('close_hand_thumbs_up_b1', 'close_hand_thumbs_up_side', 'thumbs_up_full')], 800,
          dest / '04_close_hands.png')
    board([('back, B1 camera', Image.open(src / 'close_back.png')), ('135 deg', Image.open(src / 'turn_135.png')),
           ('225 deg', Image.open(src / 'turn_225.png'))], 900, dest / '05_close_back.png')
    for name in ('bench_typing', 'bench_walking'):
        for mult in (1, 2):  # native pixels: these are the sprite densities
            flat(Image.open(src / f'{name}_{mult}x.png')).convert('RGB').save(dest / f'06_{name}_{mult}x.png')


if __name__ == '__main__' and sys.argv[1] == 'review':
    review(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
