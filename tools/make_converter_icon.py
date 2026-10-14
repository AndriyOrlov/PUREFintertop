"""Draw packaging/converter.ico: two opposite arrows on a dark rounded tile.

    python tools/make_converter_icon.py

Colours follow HDRDisplayCalibrate: accent blue for PureRef -> FrameRef and
warm yellow for the way back.
"""

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BLUE = (96, 205, 255, 255)
YELLOW = (255, 200, 61, 255)
TILE = (32, 32, 32, 255)
EDGE = (255, 255, 255, 40)


def arrow(draw, y, left, right, color, pointing_right, size):
    shaft = size * 0.075
    head = size * 0.17
    if pointing_right:
        draw.rectangle((left, y - shaft, right - head * 0.9, y + shaft), fill=color)
        draw.polygon([(right, y), (right - head, y - head), (right - head, y + head)], fill=color)
    else:
        draw.rectangle((left + head * 0.9, y - shaft, right, y + shaft), fill=color)
        draw.polygon([(left, y), (left + head, y - head), (left + head, y + head)], fill=color)


def draw_icon(size=256):
    scale = 4  # draw large, then shrink for smooth edges
    big = size * scale
    picture = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    margin = big * 0.06
    draw.rounded_rectangle((margin, margin, big - margin, big - margin), radius=big * 0.22,
                           fill=TILE, outline=EDGE, width=max(1, big // 128))
    left, right = big * 0.2, big * 0.8
    arrow(draw, big * 0.38, left, right, BLUE, True, big)
    arrow(draw, big * 0.62, left, right, YELLOW, False, big)
    return picture.resize((size, size), Image.Resampling.LANCZOS)


def main():
    target = ROOT / "packaging" / "converter.ico"
    icon = draw_icon(256)
    icon.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                           (64, 64), (128, 128), (256, 256)])
    icon.save(ROOT / "packaging" / "converter.png")
    print(target)


if __name__ == "__main__":
    main()
