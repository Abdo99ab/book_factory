"""Regenerate the fixed PNGs the mocked OpenAI returns (fictional art, no real photo).

    python tests/fixtures/make_fixtures.py

Committed output: 1600 px squares = ~188 ppi in the 216 mm page box (above the 150 ppi fail line,
below the 300 ppi target, so regressions also see the ppi *warning* path).
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
SIZE = 1600


def make(name: str, ground, blob, band=None) -> None:
    image = Image.new("RGB", (SIZE, SIZE), ground)
    draw = ImageDraw.Draw(image)
    draw.ellipse((SIZE * 0.55, SIZE * 0.10, SIZE * 0.92, SIZE * 0.42), fill=blob)
    draw.rectangle((SIZE * 0.08, SIZE * 0.08, SIZE * 0.20, SIZE * 0.20), fill=(143, 165, 139))
    if band:
        draw.rectangle((0, SIZE * 0.55, SIZE, SIZE), fill=band)
    image.save(HERE / name, format="PNG", optimize=True)


if __name__ == "__main__":
    make("page_light.png", (240, 232, 214), (150, 120, 90))
    make("page_warm.png", (236, 226, 204), (169, 80, 47))
    make("page_dark_lower.png", (238, 230, 210), (143, 165, 139), band=(34, 50, 74))
    make("cover_art.png", (243, 235, 221), (15, 82, 87))
