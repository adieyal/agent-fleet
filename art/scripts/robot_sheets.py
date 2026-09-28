"""Lay out shoot_robot.py's renders as review sheets: turnaround.png, looks.png, clips.png and compare.png.

    uv run --group dev python art/scripts/robot_sheets.py [DIR]

compare.png puts each B2 sprite beside the concept robot in the same pose, scaled so both helmets are
the same width: grey against B2's grey robots, teal against its teal ones.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ART = Path(__file__).resolve().parent.parent
B2 = ART / 'bakeoff' / 'B2'
POSES = ['robot-typing', 'robot-pencil', 'robot-tube']
BG = (236, 240, 245, 255)


def trim(im: Image.Image, pad: int = 6) -> Image.Image:
    box = im.getchannel('A').point(lambda a: 255 if a > 8 else 0).getbbox()
    return im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)))


def row(images: list[Image.Image], labels: list[str], height: int) -> Image.Image:
    scaled = [im.resize((round(im.width * height / im.height), height), Image.LANCZOS) for im in images]
    sheet = Image.new('RGBA', (sum(s.width for s in scaled) + 16 * (len(scaled) + 1), height + 40), BG)
    d = ImageDraw.Draw(sheet)
    x = 16
    for s, label in zip(scaled, labels):
        sheet.alpha_composite(s, (x, 10))
        d.text((x, height + 20), label, fill=(40, 40, 40, 255))
        x += s.width + 16
    return sheet


def stack(rows: list[Image.Image]) -> Image.Image:
    out = Image.new('RGBA', (max(r.width for r in rows), sum(r.height for r in rows)), BG)
    y = 0
    for r in rows:
        out.alpha_composite(r, (0, y))
        y += r.height
    return out


def main() -> None:
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else ART / 'build' / 'robot' / 'review'
    turns = [Image.open(d / f'turn{k}.png') for k in range(8)]
    row(turns, [f'{k * 45}°' for k in range(8)], 420).convert('RGB').save(d / 'turnaround.png')
    looks = [trim(Image.open(d / f'look{i}.png')) for i in range(6)]
    row(looks, ['codex: eyes', 'claude: band', 'backpack', 'antenna', 'halo', 'crest'], 360).convert('RGB').save(d / 'looks.png')
    clips = ['Idle', 'Walking', 'Running', 'Wave', 'ThumbsUp', 'Yes', 'No', 'Death', 'Dance', 'Jump', 'WalkJump', 'Punch',
             'Sitting', 'Standing']
    stack([row([Image.open(d / f'clip_{c}.png') for c in clips[:7]], clips[:7], 300),
           row([Image.open(d / f'clip_{c}.png') for c in clips[7:]], clips[7:], 300)]).convert('RGB').save(d / 'clips.png')
    rows = []
    for tag, suffix in (('grey', ''), ('teal', '-teal')):
        ims, labels = [], []
        for pose in POSES:
            b2 = trim(Image.open(B2 / f'{pose}{suffix}.webp').convert('RGBA'))
            k = float((d / f'cmp_{pose}_{tag}.txt').read_text())  # our helmet px per B2 helmet px
            ours = trim(Image.open(d / f'cmp_{pose}_{tag}.png'))
            ours = ours.resize((round(ours.width / k), round(ours.height / k)), Image.LANCZOS)
            ims += [b2, ours]
            labels += [f'B2 {pose}{suffix}', 'concept robot']
        h = max(i.height for i in ims)
        pad = [Image.new('RGBA', (i.width, h), (0, 0, 0, 0)) for i in ims]
        for p, i in zip(pad, ims):
            p.alpha_composite(i, (0, h - i.height))
        rows.append(row(pad, labels, h * 2))
    stack(rows).convert('RGB').save(d / 'compare.png')
    print('sheets in', d)


if __name__ == '__main__':
    main()
