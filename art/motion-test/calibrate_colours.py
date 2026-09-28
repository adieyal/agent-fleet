"""Match the robot's rendered colours to robot-sheet.png (run with any Python that has Pillow and numpy).

    python calibrate_colours.py <render.png> [<sheet pose png> ...]

Classifies opaque pixels into teal / cream / black (hue, saturation, value thresholds) and takes the median
of each class's 40-75th value percentile: the lit mid-tone, away from shadow and highlight. Compares the
render's mid-tones with the sheet's and scales each albedo in linear light by sheet / render, writing the
result to palette.json (read by palette.py). Prints both sets so each round's error is on record.
"""
import colorsys
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import palette  # noqa: E402

CLASSES = {
    'teal': lambda h, s, v: (h > 0.45) & (h < 0.56) & (s > 0.45) & (v > 0.25),
    'cream': lambda h, s, v: (s < 0.18) & (v > 0.6),
    'black': lambda h, s, v: v < 0.18,
}


def midtones(paths) -> dict:
    px = []
    for p in paths:
        a = np.asarray(Image.open(p).convert('RGBA')).reshape(-1, 4)
        px.append(a[a[:, 3] > 250][:, :3])
    rgb = np.concatenate(px)[::5] / 255.0
    hsv = np.array([colorsys.rgb_to_hsv(*c) for c in rgb])
    out = {}
    for name, test in CLASSES.items():
        m = test(hsv[:, 0], hsv[:, 1], hsv[:, 2])
        v = hsv[m, 2]
        lo, hi = np.percentile(v, [40, 75])
        out[name] = np.median(rgb[m][(v >= lo) & (v <= hi)], 0)
    return out


def to_lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def hexs(c) -> str:
    return '#' + ''.join(f'{int(round(x * 255)):02x}' for x in np.clip(c, 0, 1))


def main() -> None:
    render = Path(sys.argv[1])
    sheets = sys.argv[2:] or [str(Path.home() / '.fleet/jobs/5c6cb7/context/motion-test/robot-poses' / f)
                              for f in ('teal robot standing.png', 'teal robot sitting.png', 'teal robot walking.png')]
    want, got = midtones(sheets), midtones([render])
    new = dict(json.loads(palette.FILE.read_text()) if palette.FILE.exists() else {})
    for name in CLASSES:
        cur = to_lin(np.array([int(palette.PALETTE[name][i:i + 2], 16) / 255 for i in (1, 3, 5)]))
        ratio = to_lin(want[name]) / np.maximum(to_lin(got[name]), 1e-4)
        new[name] = hexs(to_srgb(np.clip(cur * ratio, 0, 1)))
        print(f'{name:6s} sheet {hexs(want[name])} render {hexs(got[name])} albedo {palette.PALETTE[name]} -> {new[name]}')
    palette.FILE.write_text(json.dumps(new, indent=1) + '\n')


if __name__ == '__main__':
    main()
