"""Small Pillow-only image checks shared by QA and cover generation (no numpy, no imagehash).

* ``grey_hash`` / ``hash_distance``  - 16x16 greyscale thumbnail + mean absolute difference (0..1)
* ``region_stats`` / ``is_busy``     - luminance spread of a zone, same rule as the cover logo zone
                                       in ``pdf_build._draw_front`` (std > 0.10 or p10 < 0.25 = busy)
* ``check_front_cover_layout``       - face position + calm top / logo zones for the front art
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HASH_SIZE = 16
# Mean absolute difference on the 16x16 greyscale thumbnails (0 = identical, 1 = black vs white).
# Two different framings of one room differ by well over 0.05; the "same picture twice" bug is ~0.
PAIR_DUPLICATE_THRESHOLD = 0.025

BUSY_STD = 0.10
BUSY_P10 = 0.25

# Front cover art zones, as fractions of the (square) art: (x0, y0, x1, y1).
TOP_ZONE = (0.0, 0.0, 1.0, 0.30)  # family band + title
LOGO_ZONE = (0.30, 0.85, 0.70, 1.0)  # bottom 15 %, centre 40 %
FACE_Y_RANGE = (0.38, 0.72)
FACE_X_RANGE = (0.25, 0.75)


def _lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _open_rgb(source) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    with Image.open(source) as image:
        return image.convert("RGB")


# --- pair duplicate -------------------------------------------------------------------------------


def grey_hash(source) -> list[int]:
    """16x16 greyscale thumbnail as a flat list of 256 ints."""
    image = _open_rgb(source).convert("L").resize((HASH_SIZE, HASH_SIZE), Image.Resampling.BOX)
    return list(image.getdata())


def hash_distance(a: list[int], b: list[int]) -> float:
    """Mean absolute difference between two grey hashes, normalised to 0..1."""
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255.0)


def pair_distance(left, right) -> float:
    return hash_distance(grey_hash(left), grey_hash(right))


def is_duplicate_pair(left, right, threshold: float = PAIR_DUPLICATE_THRESHOLD) -> tuple[bool, float]:
    distance = pair_distance(left, right)
    return distance < threshold, distance


# --- zone detail -----------------------------------------------------------------------------------


def region_stats(source, zone: tuple[float, float, float, float]) -> dict:
    """Luminance p10 / p90 / std of a fractional zone, measured on a small copy."""
    image = _open_rgb(source)
    image.thumbnail((160, 160))
    w, h = image.size
    x0, y0, x1, y1 = zone
    box = (
        max(0, min(w - 1, int(x0 * w))),
        max(0, min(h - 1, int(y0 * h))),
        max(1, min(w, int(x1 * w + 0.999))),
        max(1, min(h, int(y1 * h + 0.999))),
    )
    box = (box[0], box[1], max(box[0] + 1, box[2]), max(box[1] + 1, box[3]))
    lums = sorted(luminance(p) for p in image.crop(box).getdata())
    n = len(lums)
    mean = sum(lums) / n
    std = (sum((v - mean) ** 2 for v in lums) / n) ** 0.5
    return {"p10": lums[int(0.10 * (n - 1))], "p90": lums[int(0.90 * (n - 1))], "std": std}


def is_busy(stats: dict) -> bool:
    return stats["std"] > BUSY_STD or stats["p10"] < BUSY_P10


# --- front cover layout ------------------------------------------------------------------------------


def _face_in_range(face: dict) -> bool:
    x, y = float(face.get("x", 0)), float(face.get("y", 0))
    w, h = float(face.get("w", 0)), float(face.get("h", 0))
    return (
        FACE_Y_RANGE[0] <= y
        and y + h <= FACE_Y_RANGE[1]
        and FACE_X_RANGE[0] <= x
        and x + w <= FACE_X_RANGE[1]
    )


def check_front_cover_layout(art_path: Path | str, faces: list[dict] | None) -> dict:
    """Layout check of the front cover art.

    ``faces`` are normalised 0-1 boxes ``{x, y, w, h}`` (top-left origin). ``None`` means the face
    could not be detected, so only the detail zones are checked (``face_checked`` False). The child's
    face is the largest box. Returns ``{"passed", "failures": [zone, ...], "face_checked", ...}``.
    """
    failures: list[str] = []
    face_checked = faces is not None
    if faces is not None:
        if not faces:
            failures.append("face: no child face found (head must sit at 50-62% of the height)")
        else:
            main = max(faces, key=lambda f: float(f.get("w", 0)) * float(f.get("h", 0)))
            if not _face_in_range(main):
                failures.append(
                    "face: the child's face must sit inside 38-72% of the height and 25-75% of the "
                    "width (head at 50-62% height, centred)"
                )
    top = region_stats(art_path, TOP_ZONE)
    if is_busy(top):
        failures.append("top band/title zone (top 30%): must be plain, soft and empty")
    logo = region_stats(art_path, LOGO_ZONE)
    if is_busy(logo):
        failures.append("logo zone (bottom 15%, centre 40%): must be plain, soft and empty")
    return {
        "passed": not failures,
        "failures": failures,
        "face_checked": face_checked,
        "top_zone": top,
        "logo_zone": logo,
    }
