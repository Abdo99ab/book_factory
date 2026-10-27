"""Regenerate the fixed PNGs the mocked OpenAI returns (fictional art, no real photo).

    python tests/fixtures/make_fixtures.py

Spread frames are 3408 x 1920 px landscape (16:9). ``fit_to_spread`` crops them to 3408 x 1728 (the
426 x 216 mm aspect), so each page half is exactly 1728 x 1728 px: ~203 ppi in the 216 mm page box,
above the 150 ppi fail line and below the 300 ppi target (regressions also see the ppi *warning* path).
``cover_art.png`` stays a 1600 px square (covers are square).

Faces are not drawn: the fold check is exercised with mocked face boxes, and the centre of every
spread frame is left clear.
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
FRAME_W, FRAME_H = 3408, 1920
COVER_SIZE = 1600


def make(name: str, ground, blob, band=None, size=(FRAME_W, FRAME_H)) -> None:
    width, height = size
    image = Image.new("RGB", size, ground)
    draw = ImageDraw.Draw(image)
    # Blobs sit well away from the centre fold (x = 50 %) and from the top / bottom 12 % bands.
    draw.ellipse((width * 0.60, height * 0.18, width * 0.80, height * 0.42), fill=blob)
    draw.rectangle((width * 0.06, height * 0.18, width * 0.14, height * 0.30), fill=(143, 165, 139))
    if band:
        draw.rectangle((0, height * 0.55, width, height), fill=band)
    image.save(HERE / name, format="PNG", optimize=True)


if __name__ == "__main__":
    make("page_light.png", (240, 232, 214), (150, 120, 90))
    make("page_warm.png", (236, 226, 204), (169, 80, 47))
    make("page_dark_lower.png", (238, 230, 210), (143, 165, 139), band=(34, 50, 74))
    make("cover_art.png", (243, 235, 221), (15, 82, 87), size=(COVER_SIZE, COVER_SIZE))
