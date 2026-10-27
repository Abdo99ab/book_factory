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
TOP_ZONE = (0.0, 0.0, 1.0, 0.35)  # plain wall for band + title (top 35 %)
LOGO_ZONE = (0.30, 0.82, 0.70, 1.0)  # bottom 18 %, centre 40 % — plain floor
FACE_Y_RANGE = (0.38, 0.72)
FACE_X_RANGE = (0.25, 0.75)
# Back cover: figures only in the lower third; keep 20 mm clear of the trim bottom on a 216 mm panel.
BACK_FIGURE_Y_MIN = 1.0 / 3.0
BACK_BOTTOM_CLEAR_FRAC = 20.0 / 216.0  # ≈ 0.093 of the square art height


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

# Calm-zone text sits in the CENTRE of the calm half: ~60 % of half width, ~45 % of height.
# (x0, y0, x1, y1) as fractions of the whole SPREAD art.
def calm_zone_box(calm_zone: str) -> tuple[float, float, float, float]:
    half_x0 = 0.0 if calm_zone == "left" else 0.5
    # centre of the half: half is 0.5 wide; box is 0.6 * 0.5 = 0.3 wide, centred in the half
    pad_x = (0.5 - 0.5 * 0.60) / 2  # 0.1
    pad_y = (1.0 - 0.45) / 2  # 0.275
    return (
        half_x0 + pad_x,
        pad_y,
        half_x0 + 0.5 - pad_x,
        1.0 - pad_y,
    )


CALM_ZONE_BOXES = {
    "left": calm_zone_box("left"),
    "right": calm_zone_box("right"),
}

# Top band of the frame used by the cheap head-cut heuristic (fraction of height).
HEAD_CUT_TOP_FRACTION = 0.03


def calm_zone_stats(art_path: Path | str, calm_zone: str) -> dict:
    """Luminance p10 / p90 / std of the centred calm-zone text box on the calm half."""
    return region_stats(art_path, calm_zone_box(calm_zone))


def head_cut_heuristic(source) -> bool:
    """True when skin-tone / figure mass appears to touch the top ``HEAD_CUT_TOP_FRACTION`` of the frame.

    Compares luminance variance of the top rows against the band just below; a busy top with a
    calmer band underneath is treated as a cut-off head / figure. Also trips on skin-tone density
    alone in the top strip (a cut-off head is often a solid skin band with little local variance).
    """
    image = _open_rgb(source)
    image.thumbnail((320, 320))
    w, h = image.size
    top_h = max(2, int(h * HEAD_CUT_TOP_FRACTION + 0.999))
    below_h = max(top_h * 3, int(h * 0.12))
    top = image.crop((0, 0, w, top_h))
    below = image.crop((0, top_h, w, min(h, top_h + below_h)))

    def _stats(region: Image.Image) -> tuple[float, float]:
        pixels = list(region.getdata())
        n = len(pixels) or 1
        lums = [luminance(p) for p in pixels]
        mean = sum(lums) / n
        var = sum((v - mean) ** 2 for v in lums) / n
        skin = sum(
            1
            for r, g, b in pixels
            if r > 90 and g > 50 and b > 30 and r >= g and (r - b) > 20
        ) / n
        return var, skin

    top_var, top_skin = _stats(top)
    below_var, below_skin = _stats(below)
    if top_skin >= 0.08 and top_skin > below_skin * 1.5:
        return True
    top_act = top_var + top_skin * 0.1
    below_act = below_var + below_skin * 0.1
    return top_act > 0.008 and top_act > below_act * 1.25


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
    face is the largest box. Returns ``{"passed", "failures": [zone, ...], "face_checked", "main_face"}``.
    """
    failures: list[str] = []
    face_checked = faces is not None
    main = None
    if faces is not None:
        if not faces:
            failures.append("face: no child face found (head must sit at 50-62% of the height)")
        else:
            main = max(faces, key=lambda f: float(f.get("w", 0)) * float(f.get("h", 0)))
            if not _face_in_range(main):
                failures.append(
                    "face: the child's face must sit inside 38-72% of the height and 25-75% of the "
                    "width (head top at 38-42%, centred)"
                )
    top = region_stats(art_path, TOP_ZONE)
    if is_busy(top):
        failures.append("top band/title zone (top 35%): must be plain, soft and empty")
    logo = region_stats(art_path, LOGO_ZONE)
    if is_busy(logo):
        failures.append("logo zone (bottom 18%, centre 40%): must be plain floor, soft and empty")
    return {
        "passed": not failures,
        "failures": failures,
        "face_checked": face_checked,
        "main_face": main,
        "top_zone": top,
        "logo_zone": logo,
    }


def check_back_cover_layout(art_path: Path | str, faces: list[dict] | None) -> dict:
    """Back art: faces / figures only in the lower third, clear of the bottom wrap."""
    failures: list[str] = []
    face_checked = faces is not None
    if faces:
        for face in faces:
            y0 = float(face.get("y", 0))
            y1 = y0 + float(face.get("h", 0))
            if y0 < BACK_FIGURE_Y_MIN:
                failures.append("back face: figures must stay in the lower third (upper two thirds plain wall)")
                break
            if y1 > 1.0 - BACK_BOTTOM_CLEAR_FRAC:
                failures.append(
                    "back bottom: figures and the anchor stay at least 20 mm above the trim bottom"
                )
                break
    upper = region_stats(art_path, (0.0, 0.0, 1.0, BACK_FIGURE_Y_MIN))
    if is_busy(upper) and (faces is None or faces):
        # Only flag a busy upper third when we also saw a face too high, or couldn't check faces.
        if faces is None or any(float(f.get("y", 0)) < BACK_FIGURE_Y_MIN for f in faces or []):
            if "back face" not in " ".join(failures):
                failures.append("back upper two thirds: must be plain wall with no figures")
    return {
        "passed": not failures,
        "failures": failures,
        "face_checked": face_checked,
        "upper_zone": upper,
    }


def rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def face_box_norm(face: dict) -> tuple[float, float, float, float]:
    x, y = float(face.get("x", 0)), float(face.get("y", 0))
    w, h = float(face.get("w", 0)), float(face.get("h", 0))
    return (x, y, x + w, y + h)
