"""Robot colours from robot-sheet.png. Defaults are the sheet's mid-tones (median of the 40-75th value
percentile per colour class over the teal pose images); palette.json, written by calibrate_colours.py, holds
the albedos that make a B1-studio render reproduce those mid-tones."""

import json
from pathlib import Path

SHEET = {'teal': '#1e9ca8', 'cream': '#d6c9be', 'black': '#131717', 'glow': '#9beff6'}
FILE = Path(__file__).with_name('palette.json')
PALETTE = SHEET | (json.loads(FILE.read_text()) if FILE.exists() else {})


def lin(name_or_hex: str) -> tuple:
    """Palette entry (or #rrggbb) as linear RGBA."""
    h = PALETTE.get(name_or_hex, name_or_hex)
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)
