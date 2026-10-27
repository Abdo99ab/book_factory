"""Fixed-PNG stand-ins for the OpenAI image API (no network, fictional data only)."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from PIL import Image


def png_bytes(size: int = 2048, colour=(120, 150, 170), height: int | None = None) -> bytes:
    """A small-on-disk but real PNG, ``size`` px wide and ``height`` px tall (square by default)."""
    height = height or size
    image = Image.new("RGB", (size, height), colour)
    band = Image.new("RGB", (size, max(1, height // 8)), (colour[0] // 2, colour[1] // 2, colour[2] // 2))
    image.paste(band, (0, height // 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def image_result(size: int = 2048, colour=(120, 150, 170), height: int | None = None):
    data = base64.b64encode(png_bytes(size, colour, height)).decode("ascii")
    return SimpleNamespace(data=[SimpleNamespace(b64_json=data)])


class FakeImageEdit:
    """Drop-in for ``image_edit`` that records every call and returns a fixed PNG.

    ``size`` is the PIXEL WIDTH of the frame it returns. A landscape request ("auto" / None / a
    non-square ``WxH``) gets a 16:9 frame (taller than the 1.97:1 spread, so ``fit_to_spread`` crops
    the top and bottom); a square request (``"square"`` or ``WxW``) gets a square one.
    """

    def __init__(self, size: int = 3408):
        self.size = size
        self.calls: list[dict] = []
        self._cache: dict[tuple, object] = {}

    @staticmethod
    def _is_square(size) -> bool:
        text = str(size or "auto").lower()
        if text == "square":
            return True
        if "x" in text and text.replace("x", "").isdigit():
            w, h = text.split("x")
            return w == h
        return False

    def __call__(self, client, image_path, prompt, size=None, quality=None, description=""):
        paths = [image_path] if not isinstance(image_path, (list, tuple)) else list(image_path)
        self.calls.append(
            {
                "paths": [str(p) for p in paths],
                "names": [getattr(p, "name", str(p)).split("\\")[-1].split("/")[-1] for p in paths],
                "prompt": prompt,
                "size": size,
                "description": description,
            }
        )
        width = self.size
        text = str(size or "auto")
        if "x" in text and text.replace("x", "").isdigit():
            width = min(width, int(text.split("x")[0]))
        square = self._is_square(size)
        height = width if square else round(width * 9 / 16)
        # Slightly different colour per call so spreads are distinguishable
        key = len(self.calls) % 5
        if (width, height, key) not in self._cache:
            self._cache[(width, height, key)] = image_result(width, (110 + key * 20, 150, 170 - key * 10), height)
        return self._cache[(width, height, key)]
