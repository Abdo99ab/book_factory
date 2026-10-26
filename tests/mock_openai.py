"""Fixed-PNG stand-ins for the OpenAI image API (no network, fictional data only)."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from PIL import Image


def png_bytes(size: int = 2048, colour=(120, 150, 170)) -> bytes:
    """A small-on-disk but real square PNG of ``size`` x ``size`` px (flat colour + soft band)."""
    image = Image.new("RGB", (size, size), colour)
    band = Image.new("RGB", (size, size // 8), (colour[0] // 2, colour[1] // 2, colour[2] // 2))
    image.paste(band, (0, size // 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def image_result(size: int = 2048, colour=(120, 150, 170)):
    data = base64.b64encode(png_bytes(size, colour)).decode("ascii")
    return SimpleNamespace(data=[SimpleNamespace(b64_json=data)])


class FakeImageEdit:
    """Drop-in for ``image_edit`` that records every call and returns a fixed PNG."""

    def __init__(self, size: int = 2048):
        self.size = size
        self.calls: list[dict] = []
        self._cache: dict[int, object] = {}

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
        # Slightly different colour per call so spreads are distinguishable
        px = self.size if size in (None, "auto") or not str(size)[0].isdigit() else min(self.size, int(str(size).split("x")[0]))
        key = len(self.calls) % 5
        if (px, key) not in self._cache:
            self._cache[(px, key)] = image_result(px, (110 + key * 20, 150, 170 - key * 10))
        return self._cache[(px, key)]
