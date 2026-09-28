#!/usr/bin/env python3
"""Regenerate the PWA icons.

    python tools/make_icons.py

Writes icon-192.png, icon-512.png, icon-512-maskable.png and
apple-touch-icon.png into the repository root, then bump CACHE_NAME in sw.js
and commit if you want the change to reach an installed device.

Requires Pillow.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BG = (43, 76, 126, 255)      # deep slate blue, matches --accent in app.css
CARD = (250, 250, 250, 255)
LINE = (43, 76, 126, 255)

# Fraction of the canvas left as margin on each side. The maskable variant needs
# more, because Android may crop the icon to a circle: everything meaningful has
# to sit inside the central 80% safe zone.
LAYOUT = [
    ("icon-192.png", 192, 0.20),
    ("icon-512.png", 512, 0.20),
    ("icon-512-maskable.png", 512, 0.28),
    ("apple-touch-icon.png", 180, 0.20),
]


def draw_icon(size: int, pad_ratio: float, path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # A note card, slightly taller than wide.
    card_w = int(size * (1 - 2 * pad_ratio))
    card_h = int(card_w * 1.22)
    x0 = (size - card_w) // 2
    y0 = (size - card_h) // 2
    draw.rounded_rectangle(
        [x0, y0, x0 + card_w, y0 + card_h],
        radius=max(2, int(card_w * 0.10)), fill=CARD,
    )

    # Three ruled lines; the last is short, like a paragraph tail.
    line_w = max(2, int(card_w * 0.075))
    inset = int(card_w * 0.18)
    gap = card_h * 0.20
    top = y0 + card_h * 0.26
    for index in range(3):
        width = (card_w - 2 * inset) * (1.0 if index < 2 else 0.6)
        draw.rounded_rectangle(
            [x0 + inset, top + index * gap, x0 + inset + width, top + index * gap + line_w],
            radius=line_w // 2, fill=LINE,
        )

    img.save(path, "PNG", optimize=True)
    print(f"  {path.name:<26} {size}x{size}")


def main() -> None:
    print(f"writing icons into {ROOT}")
    for name, size, pad in LAYOUT:
        draw_icon(size, pad, ROOT / name)
    print("\ndone. Bump CACHE_NAME in sw.js so installed devices pick up new icons.")


if __name__ == "__main__":
    main()
