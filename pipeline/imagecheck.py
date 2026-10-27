"""Small Pillow-only image checks shared by QA and cover generation (no numpy, no imagehash).

* ``region_stats`` / ``is_busy``     - luminance spread of a zone, same rule as the cover logo zone
                                       in ``pdf_build._draw_front`` (std > 0.10 or p10 < 0.25 = busy)
* ``check_front_cover_layout``       - face position + calm top / logo zones for the front art
* ``spread_layout_failures``         - faces vs the fold, the outer edges and the crop bands of a spread
* ``calm_zone_stats``                - luminance spread of the calm half's lower two thirds
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .constants import CROP_BAND_FRACTION, EDGE_BAND_MM, GUTTER_BAND_MM, SPREAD_WIDTH_MM

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


# --- spread layout (fold, edges, crop bands, calm zone) ------------------------------------------------

# The calm half's text sits in its lower two thirds: (x0, y0, x1, y1) as fractions of the SPREAD art.
CALM_ZONE_BOXES = {
    "left": (0.0, 1 / 3, 0.5, 1.0),
    "right": (0.5, 1 / 3, 1.0, 1.0),
}


def calm_zone_stats(art_path: Path | str, calm_zone: str) -> dict:
    """Luminance p10 / p90 / std of the lower two thirds of the calm half (``left`` or ``right``)."""
    return region_stats(art_path, CALM_ZONE_BOXES["left" if calm_zone == "left" else "right"])


def spread_layout_failures(
    faces: list[dict] | None,
    frame_size: tuple[int, int] | None = None,
    crop_box: tuple[int, int, int, int] | None = None,
) -> list[dict]:
    """Fold / edge / crop-band failures for faces on a spread.

    ``faces`` are normalised 0-1 boxes ``{x, y, w, h}`` measured on the model's FULL frame
    (``frame_size`` px). ``crop_box`` is the (left, top, right, bottom) the frame is centre-cropped
    to; without it the frame is the spread. Returns ``[{"zone", "message", "face"}]``:

    * ``fold``       - the face overlaps the centre band, fold +/- GUTTER_BAND_MM
    * ``outer edge`` - the face sits within EDGE_BAND_MM of the left / right edge (or is cut off)
    * ``top/bottom`` - the face sits in the top / bottom band that can be cropped away: the larger of
                       CROP_BAND_FRACTION and the share the fit crop really removes
    """
    if not faces:
        return []
    if frame_size and crop_box:
        fw, fh = frame_size
        cl, ct, cr, cb = crop_box
    else:
        fw = fh = 1.0
        cl, ct, cr, cb = 0.0, 0.0, 1.0, 1.0
    cw, ch = cr - cl, cb - ct
    fold_half = GUTTER_BAND_MM / SPREAD_WIDTH_MM
    edge = EDGE_BAND_MM / SPREAD_WIDTH_MM
    band = max(CROP_BAND_FRACTION, ct / fh, (fh - cb) / fh)
    found: dict[str, dict] = {}
    for face in faces:
        x0 = (float(face.get("x", 0)) * fw - cl) / cw
        x1 = x0 + float(face.get("w", 0)) * fw / cw
        y0 = float(face.get("y", 0))
        y1 = y0 + float(face.get("h", 0))
        if x0 < 0.5 + fold_half and x1 > 0.5 - fold_half:
            found.setdefault(
                "fold",
                {
                    "zone": "fold",
                    "message": (
                        f"fold: a face crosses the centre fold; keep every face and hand at least "
                        f"{GUTTER_BAND_MM:g} mm away from the centre line of the picture"
                    ),
                    "face": face,
                },
            )
        if x0 < edge or x1 > 1 - edge:
            found.setdefault(
                "outer edge",
                {
                    "zone": "outer edge",
                    "message": (
                        f"outer edge: a face is too close to the left or right edge; keep faces at "
                        f"least {EDGE_BAND_MM:g} mm inside the outer edges"
                    ),
                    "face": face,
                },
            )
        if y0 < band or y1 > 1 - band:
            found.setdefault(
                "top/bottom",
                {
                    "zone": "top/bottom",
                    "message": (
                        f"top/bottom: a face sits in the top or bottom {round(band * 100)}% of the "
                        "frame, which is cropped away; keep heads and faces in the middle band"
                    ),
                    "face": face,
                },
            )
    return list(found.values())


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
