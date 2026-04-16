from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

BG = "#c41e3a"
FG = "#ffffff"
OUT = Path("app/static/icons")

def find_font(size):
    for path in [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()

def make_icon(size, filename):
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    font = find_font(int(size * 0.55))
    text = "M"
    bbox = d.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1]
    d.text((x, y), text, fill=FG, font=font)
    img.save(OUT / filename, "PNG")
    print(f"wrote {filename} ({size}x{size})")

make_icon(192, "icon-192.png")
make_icon(512, "icon-512.png")
make_icon(180, "apple-touch-icon-180.png")
make_icon(32, "favicon-32.png")
