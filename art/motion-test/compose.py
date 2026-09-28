"""Contact sheets and side-by-side webms from render_motion.py output (run with any Python that has Pillow).

    python compose.py sheet <clip_dir> <out.png> <title> <label>[:<caption>] ...
    python compose.py webm  <clip_dir> <out.webm> <ffmpeg> <label>[:<caption>] ...
    python compose.py review3 <render_motion review dir> <out dir> <job context motion-test dir>
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


if __name__ == '__main__' and sys.argv[1] not in ('review', 'review3'):
    a = sys.argv[1:]
    if a[0] == 'sheet':
        sheet(Path(a[1]), Path(a[2]), a[3], a[4:])
    else:
        webm(Path(a[1]), Path(a[2]), a[3], a[4:])


def board(items, h, out) -> None:
    """Images side by side at height h, each captioned, transparent ones on the floor colour."""
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


def native(items, out, bg=(255, 255, 255, 255)) -> None:
    """Images side by side at their native pixels (no resampling), bottoms aligned, each captioned."""
    ims = [(cap, flat(im.convert('RGBA')) if im.mode == 'RGBA' else im.convert('RGBA')) for cap, im in items]
    h = max(i.height for _, i in ims)
    W = sum(i.width for _, i in ims) + 12 * (len(ims) - 1)
    b = Image.new('RGBA', (W, h + 34), bg)
    d = ImageDraw.Draw(b)
    x = 0
    for cap, im in ims:
        b.paste(im, (x, 34 + h - im.height))
        d.text((x + 4, 6), cap, fill='black', font=ImageFont.load_default(size=16))
        x += im.width + 12
    b.convert('RGB').save(out, optimize=True)


L2 = Path(__file__).resolve().parents[2] / 'docs/images/concept/l2.png'
L2_CROP = (520, 380, 1210, 700)  # l2's three seated robots at its bench, 171.5 px/m


def review(src: Path, dest: Path, context: Path) -> None:
    """Round-2 review boards from render_motion.py's review set. context: the job's motion-test context dir."""
    dest.mkdir(parents=True, exist_ok=True)
    rebuild = Path.home() / '.local/state/fleet/renovation/robot-rebuild'
    sheet = Image.open(context / 'robot-sheet.png')
    pose = Image.open(context / 'robot-poses' / 'teal robot standing.png')
    apose = Image.open(rebuild / 'robot-apose-front.png')
    o = lambda n: Image.open(src / f'{n}.png')  # noqa: E731
    board([('robot-sheet.png', sheet), ('robot-apose-front.png', apose), ('rebuilt robot, its A-pose, front',
           o('apose_front')), ('robot-sheet teal standing', pose), ('rebuilt robot, standing idle, similar angle',
           o('sheet_angle'))], 900, dest / '01_vs_references.png')
    board([('target', pose)] + [(f'floor camera (28/33), turned {a} deg', o(f'turn_{a:03d}'))
                                for a in range(0, 360, 45)], 900, dest / '02_turnaround.png')
    board([('head, floor camera', o('close_head_floor')), ('head, front', o('close_head_front')),
           ('head, side', o('close_head_side')), ('head, back, floor camera', o('close_head_back'))], 800,
          dest / '03_close_head.png')
    board([('back, floor camera (8x)', o('close_back')), ('turned 135 deg', o('turn_135')),
           ('turned 225 deg', o('turn_225'))], 900, dest / '04_close_back.png')
    poses = ('fist', 'open', 'thumbs_up', 'cupped', 'pinch', 'book', 'sheet')
    clip = {'fist': 'walking, idle, typing', 'open': 'waving', 'thumbs_up': 'thumbs up', 'cupped': 'box',
            'pinch': 'writing', 'book': 'reading (seated)', 'sheet': 'walking, reading'}
    board([(f'{p.replace("_", " ")}: {clip[p]}', o(f'hand_{p}_close')) for p in poses], 600,
          dest / '05_hands_close.png')
    board([(f'{p.replace("_", " ")}: {clip[p]}', o(f'hand_{p}_full')) for p in poses], 600,
          dest / '06_hands_in_clip.png')
    l2 = Image.open(L2).crop(L2_CROP)
    for mult in (1, 2):
        ref = l2 if mult == 1 else l2.resize((l2.width * 2, l2.height * 2), Image.LANCZOS)
        native([(f'l2 (concept), {mult}x', ref)] + [(f'{n}, {mult}x', o(f'bench_{n}_{mult}x'))
                                                     for n in ('typing', 'reading', 'walking')],
               dest / f'07_bench_{mult}x_vs_l2.png')


def review3(src: Path, dest: Path, context: Path) -> None:
    """Round-3 boards from render_motion.py's review set. context: the job's motion-test context dir."""
    dest.mkdir(parents=True, exist_ok=True)
    rebuild = Path.home() / '.local/state/fleet/renovation/robot-rebuild'
    o = lambda n: Image.open(src / f'{n}.png')  # noqa: E731
    face = Image.open(rebuild / 'robot-apose-front.png').crop((250, 60, 780, 580))
    pose = Image.open(context / 'robot-poses' / 'teal robot standing.png')
    board([('robot-apose-front.png (face)', face), ('rebuilt head, front', o('head_front')),
           ('robot-sheet teal standing', pose), ('rebuilt head, similar angle', o('head_sheet_angle')),
           ('rebuilt head, floor camera', o('head_floor'))], 700, dest / '01_head_vs_sheet.png')
    l2 = Image.open(L2).crop(L2_CROP)
    clips = (('typing', 'typing'), ('writing-seated', 'writing'), ('reading-seated', 'reading'))
    for mult in (1, 2):
        ref = l2 if mult == 1 else l2.resize((l2.width * 2, l2.height * 2), Image.LANCZOS)
        native([(f'l2 (concept), {mult}x', ref)] + [(f'{cap}, {mult}x', o(f'bench_{c}_{mult}x')) for c, cap in clips],
               dest / f'02_bench_{mult}x_vs_l2.png')
    board([(cap, o(f'close_{c}')) for c, cap in clips], 700, dest / '03_seated_close.png')
    board([('thumbs up standing, floor camera', o('thumbs_standing')), ('standing, front', o('thumbs_standing_front')),
           ('thumbs up sitting, floor camera', o('thumbs_sitting')), ('sitting, front', o('thumbs_sitting_front'))],
          700, dest / '04_thumbs_up.png')
    board([(f'walking with the box, frame {k + 1}/4', o(f'box_walk_{k}')) for k in range(4)], 700,
          dest / '05_box_walk.png')
    native([('box walk, 1x', o('box_walk_1x')), ('box walk, 2x', o('box_walk_2x'))], dest / '05_box_walk_1x_2x.png')


if __name__ == '__main__' and sys.argv[1] == 'review3':
    review3(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))


if __name__ == '__main__' and sys.argv[1] == 'review':
    review(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
