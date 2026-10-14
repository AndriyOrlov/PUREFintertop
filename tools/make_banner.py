"""Draws docs/banner.png and docs/social-preview.png (1280x640).

Same layout as the HDRDisplayCalibrate banner: title on the left, app
screenshots on the right, everything inside GitHub's 80 px safe area so the
social card is never cropped.

    python tools/make_banner.py
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
WIDTH, HEIGHT, SAFE = 1280, 640, 80
WHITE, BLUE, YELLOW = (255, 255, 255), (96, 205, 255), (255, 200, 61)


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def glow(picture, centre, radius, color):
    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    x, y = centre
    ImageDraw.Draw(layer).ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    return Image.alpha_composite(picture, layer.filter(ImageFilter.GaussianBlur(radius * 0.55)))


def rounded(picture, radius):
    mask = Image.new("L", picture.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, picture.size[0] - 1, picture.size[1] - 1),
                                           radius, fill=255)
    result = picture.convert("RGBA")
    result.putalpha(mask)
    return result


def with_shadow(base, picture, position, radius=14):
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    x, y = position
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x + 6, y + 12, x + picture.size[0] + 6, y + picture.size[1] + 12), radius,
        fill=(0, 0, 0, 150))
    base = Image.alpha_composite(base, shadow.filter(ImageFilter.GaussianBlur(14)))
    base.alpha_composite(rounded(picture, radius), position)
    return base


def scaled(path, width, crop=None):
    picture = Image.open(path).convert("RGBA")
    if crop:
        picture = picture.crop(crop)
    height = round(picture.size[1] * width / picture.size[0])
    return picture.resize((width, height), Image.Resampling.LANCZOS)


def main():
    banner = Image.new("RGBA", (WIDTH, HEIGHT), (22, 22, 26, 255))
    # Light in the two app colours: blue (PureRef -> FrameRef), yellow (back).
    banner = glow(banner, (230, 170), 320, (96, 205, 255, 55))
    banner = glow(banner, (980, 240), 300, (255, 200, 61, 45))
    banner = glow(banner, (1120, 520), 240, (120, 90, 255, 45))

    # Right side: the board report behind, the converter window in front.
    report = scaled(DOCS / "screenshots" / "board-report.png", 500, crop=(30, 20, 1370, 1060))
    banner = with_shadow(banner, report, (WIDTH - SAFE - report.size[0], SAFE))
    converter = scaled(DOCS / "screenshots" / "converter.png", 360)
    banner = with_shadow(banner, converter, (600, HEIGHT - SAFE - converter.size[1]), 16)

    draw = ImageDraw.Draw(banner)
    icon = Image.open(ROOT / "packaging" / "converter.png").convert("RGBA")
    banner.alpha_composite(icon.resize((76, 76), Image.Resampling.LANCZOS), (SAFE, SAFE + 6))
    draw.text((SAFE, 168), "PureRef", font=font("seguibl.ttf", 64), fill=WHITE)
    draw.text((SAFE, 236), "Interop", font=font("seguibl.ttf", 64), fill=BLUE)
    body = font("segoeui.ttf", 26)
    draw.text((SAFE + 2, 340), "Open, edit, convert and repair", font=body, fill=(215, 215, 220))
    draw.text((SAFE + 2, 377), ".pur boards without PureRef.", font=body, fill=(215, 215, 220))
    # Segoe UI has no ⇄, so the arrows come from Segoe UI Symbol.
    x = SAFE + 2
    for text, face in (("PureRef ", body), ("⇄", font("seguisym.ttf", 26)),
                       (" FrameRef · API · reports", body)):
        draw.text((x, 424), text, font=face, fill=YELLOW)
        x += draw.textlength(text, font=face)
    draw.text((SAFE + 2, 520), "github.com/AndriyOrlov/PUREFintertop",
              font=font("segoeui.ttf", 19), fill=(150, 150, 155))

    result = banner.convert("RGB")
    for name in ("banner.png", "social-preview.png"):
        result.save(DOCS / name, optimize=True)
    print("docs/banner.png, docs/social-preview.png")


if __name__ == "__main__":
    main()
