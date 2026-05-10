"""Generate the DMG installer's background image.

Output: assets/dmg-background.png (600×400 — matches the DMG window size 1:1
so Finder doesn't clip it. A bit soft on retina, but readable, and never
clipped on non-retina).

Visual: a subtle dot grid + a small arrow centered between the two icon
slots. The icons themselves do the heavy lifting; the background just gives
the eye somewhere to rest.

Window layout (set in build.sh):
  - LocationSpoofer.app icon at (150, 230), 128px
  - Applications symlink at (450, 230), 128px
  - Arrow drawn between them at y≈230, x range ~225..375
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "dmg-background.png"

WIDTH, HEIGHT = 600, 400
BG = (247, 248, 250)            # very light slate
DOT = (220, 224, 230)           # faint slate
ARROW = (148, 163, 184)         # slate-400
LABEL = (130, 140, 160)


def find_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_dot_grid(img: Image.Image, spacing: int = 24, radius: int = 1) -> None:
    d = ImageDraw.Draw(img)
    for y in range(spacing, HEIGHT, spacing):
        for x in range(spacing, WIDTH, spacing):
            d.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=DOT,
            )


def draw_arrow(d: ImageDraw.ImageDraw) -> None:
    # Centered between the two icon positions
    y = 230
    x_start = 240
    x_end = 360
    shaft_thickness = 4
    head_size = 14

    d.line([(x_start, y), (x_end - head_size, y)], fill=ARROW, width=shaft_thickness)
    d.polygon(
        [
            (x_end, y),
            (x_end - head_size, y - head_size // 2),
            (x_end - head_size, y + head_size // 2),
        ],
        fill=ARROW,
    )


def main() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw_dot_grid(img)

    d = ImageDraw.Draw(img)

    # Tiny "Drag" label above the arrow
    label_font = find_font(11)
    label = "drag"
    bbox = d.textbbox((0, 0), label, font=label_font)
    lw = bbox[2] - bbox[0]
    d.text((300 - lw // 2, 210), label, fill=LABEL, font=label_font)

    draw_arrow(d)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"Wrote {OUT} ({WIDTH}×{HEIGHT})")


if __name__ == "__main__":
    main()
