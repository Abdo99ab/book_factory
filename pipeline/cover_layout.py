"""Front/back cover layout zones, face detection, and deterministic reframe.

Zones are derived from the same millimetre layout as ``pdf_build._draw_front`` /
``_draw_back`` so prompts and checks share one source of truth.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from reportlab.pdfbase import pdfmetrics

from .brand import LOGO_FILES, SIZE_SCALE
from .constants import BLEED_MM, PAGE_WITH_BLEED_MM, TRIM_MM
from .fonts import register_brand_fonts, reportlab_name
from .job_log import append_event, read_json, write_json

BAND_DEPTH_MM = float(SIZE_SCALE["band_depth_mm"])
TITLE_GAP_BELOW_BAND_MM = 11.0
TITLE_SIDE_INSET_MM = 30.0  # trim_w - 30 in _draw_front
TITLE_PLATE_PAD_MM = 5.0
LOGO_W_MM = 62.0
LOGO_BOTTOM_CLEAR_MM = 10.0
LOGO_PLATE_PAD_MM = 8.0
BACK_TEXT_BAND_FRAC = 0.40  # bottom 40 % reserved for the white plate

# Deterministic reframe limits (fractions of art height).
# Allow up to ~32% so a face centred near 28% can still reach ~58% without a model call;
# beyond that we regenerate once.
MAX_REFRAME_SHIFT = 0.32
MAX_REFRAME_SCALE = 0.15  # scale down at most 15 %
CENTRE_TOL = 0.04  # how close face-union centre must sit to the target


def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def split_title(title: str, name: str) -> tuple[str | None, str]:
    for prefix in (f"{name} and the ", f"The Night {name}'s ", f"{name}'s ", f"{name}\u2019s "):
        if title.startswith(prefix):
            return prefix.strip(), title[len(prefix) :].strip()
    return None, title


def _wrap(text: str, font: str, size: float, width_pt: float) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if pdfmetrics.stringWidth(trial, font, size) <= width_pt:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def estimate_front_title_height_mm(book_title: str, child_name: str) -> float:
    """Height of the front title block (and the + rest), matching ``_draw_title_block`` sizing."""
    register_brand_fonts()
    font = reportlab_name("title")
    trim_w = TRIM_MM
    width_mm = trim_w - TITLE_SIDE_INSET_MM
    width_pt = mm_to_pt(width_mm)
    line1, rest = split_title(book_title, child_name)
    lo, hi = SIZE_SCALE["front_title_rest"]
    size = int(hi)
    lines = _wrap(rest, font, size, width_pt)
    for candidate in range(int(hi), int(lo) - 1, -2):
        trial = _wrap(rest, font, candidate, width_pt)
        if len(trial) <= 3:
            size, lines = candidate, trial
            break
    else:
        size = int(lo)
        lines = _wrap(rest, font, size, width_pt)
        while len(lines) > 3 and size > 30:
            size -= 2
            lines = _wrap(rest, font, size, width_pt)
    l1_size = float(SIZE_SCALE["front_title_and_the"][0])
    l1_pt = l1_size * 1.25
    lead = size * 1.12
    height_pt = (l1_pt if line1 else 0) + lead * max(1, len(lines))
    return height_pt * 25.4 / 72 + TITLE_PLATE_PAD_MM


def logo_height_mm(logo_w_mm: float = LOGO_W_MM) -> float:
    path = LOGO_FILES.get("main")
    if path and Path(path).is_file():
        with Image.open(path) as im:
            return logo_w_mm * im.size[1] / max(1, im.size[0])
    return logo_w_mm * 0.35


def front_cover_zones(book_title: str, child_name: str) -> dict:
    """Fractional zones on the square cover art (y=0 at top), from the real PDF layout.

    Returns percentages useful for prompts plus float fractions for checks/reframe.
    """
    h_mm = PAGE_WITH_BLEED_MM
    title_top_mm = BLEED_MM + BAND_DEPTH_MM + TITLE_GAP_BELOW_BAND_MM
    title_h = estimate_front_title_height_mm(book_title, child_name)
    title_bottom_mm = title_top_mm + title_h
    logo_h = logo_height_mm()
    logo_top_mm = h_mm - BLEED_MM - LOGO_BOTTOM_CLEAR_MM - logo_h - LOGO_PLATE_PAD_MM

    title_bottom = min(0.45, title_bottom_mm / h_mm)
    logo_top = max(0.70, logo_top_mm / h_mm)
    if logo_top <= title_bottom + 0.15:
        # Pathological title — keep a usable clear band.
        title_bottom = 0.35
        logo_top = 0.85
    clear_top = title_bottom
    clear_bottom = logo_top
    face_centre = (clear_top + clear_bottom) / 2

    return {
        "title_bottom": title_bottom,
        "logo_top": logo_top,
        "clear_top": clear_top,
        "clear_bottom": clear_bottom,
        "face_centre_y": face_centre,
        "face_centre_x": 0.50,
        "title_pct": round(clear_top * 100),
        "logo_pct_from_bottom": round((1.0 - clear_bottom) * 100),
        "centre_pct": round(face_centre * 100),
        "band_bottom": (BLEED_MM + BAND_DEPTH_MM) / h_mm,
        "title_top": title_top_mm / h_mm,
        "title_height_mm": title_h,
        "logo_height_mm": logo_h,
    }


def back_cover_zones() -> dict:
    return {
        "text_band_top": 1.0 - BACK_TEXT_BAND_FRAC,
        "clear_bottom": 1.0 - BACK_TEXT_BAND_FRAC,
        "face_centre_y": (1.0 - BACK_TEXT_BAND_FRAC) * 0.55,
        "face_centre_x": 0.50,
        "title_pct": 0,
        "logo_pct_from_bottom": round(BACK_TEXT_BAND_FRAC * 100),
        "centre_pct": round((1.0 - BACK_TEXT_BAND_FRAC) * 0.55 * 100),
    }


def detect_faces_local(image_path: Path) -> list[dict]:
    """OpenCV Haar face boxes (frontal + profile), normalised 0–1, y=0 at top."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return []
    path = str(image_path)
    image = cv2.imread(path)
    if image is None:
        import numpy as np

        with Image.open(image_path) as pil:
            rgb = np.array(pil.convert("RGB"))
        image = rgb[:, :, ::-1].copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    if w < 1 or h < 1:
        return []
    boxes: list[tuple[int, int, int, int]] = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if cascade.empty():
            continue
        for x, y, bw, bh in cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24)
        ):
            boxes.append((int(x), int(y), int(bw), int(bh)))
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for x, y, bw, bh in boxes:
        if any(abs(x - kx) < bw * 0.4 and abs(y - ky) < bh * 0.4 for kx, ky, _, _ in kept):
            continue
        kept.append((x, y, bw, bh))
    return [
        {"x": x / w, "y": y / h, "w": bw / w, "h": bh / h}
        for x, y, bw, bh in kept
    ]


def union_face_box(faces: list[dict]) -> dict | None:
    if not faces:
        return None
    x0 = min(float(f["x"]) for f in faces)
    y0 = min(float(f["y"]) for f in faces)
    x1 = max(float(f["x"]) + float(f["w"]) for f in faces)
    y1 = max(float(f["y"]) + float(f["h"]) for f in faces)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _mean_rgb(pixels) -> tuple[int, int, int]:
    n = len(pixels) or 1
    return (
        sum(p[0] for p in pixels) // n,
        sum(p[1] for p in pixels) // n,
        sum(p[2] for p in pixels) // n,
    )


def check_front_faces(faces: list[dict] | None, zones: dict) -> dict:
    """Pass when all faces sit in the clear middle band, centred."""
    failures: list[str] = []
    if faces is None:
        return {"passed": True, "failures": [], "face_checked": False, "union": None}
    if not faces:
        failures.append("face: no face found on the cover")
        return {"passed": False, "failures": failures, "face_checked": True, "union": None}
    union = union_face_box(faces)
    assert union is not None
    cx = float(union["x"]) + float(union["w"]) / 2
    cy = float(union["y"]) + float(union["h"]) / 2
    top = float(union["y"])
    bottom = float(union["y"]) + float(union["h"])
    if top < zones["clear_top"] - 0.01:
        failures.append(
            f"face: enters title zone (union top {top:.0%} < clear {zones['clear_top']:.0%})"
        )
    if bottom > zones["clear_bottom"] + 0.01:
        failures.append(
            f"face: enters logo zone (union bottom {bottom:.0%} > clear {zones['clear_bottom']:.0%})"
        )
    if abs(cx - zones["face_centre_x"]) > CENTRE_TOL + 0.06:
        failures.append(f"face: not centred horizontally (cx={cx:.0%})")
    if abs(cy - zones["face_centre_y"]) > CENTRE_TOL + 0.06:
        failures.append(
            f"face: centre at {cy:.0%}, target ~{zones['face_centre_y']:.0%} of height"
        )
    return {
        "passed": not failures,
        "failures": failures,
        "face_checked": True,
        "union": union,
        "centre": {"x": cx, "y": cy},
    }


def check_back_faces(faces: list[dict] | None, zones: dict | None = None) -> dict:
    zones = zones or back_cover_zones()
    failures: list[str] = []
    if not faces:
        return {"passed": True, "failures": [], "face_checked": faces is not None, "union": None}
    for face in faces:
        y1 = float(face["y"]) + float(face["h"])
        if y1 > zones["text_band_top"] + 0.01:
            failures.append("back face: enters bottom text plate (bottom 40%)")
            break
    return {
        "passed": not failures,
        "failures": failures,
        "face_checked": True,
        "union": union_face_box(faces),
    }


def reframe_cover_art(
    art_path: Path,
    faces: list[dict],
    zones: dict,
    *,
    max_shift: float = MAX_REFRAME_SHIFT,
    max_scale: float = MAX_REFRAME_SCALE,
) -> dict:
    """Shift/scale art so the face-union centre lands on the clear-zone target.

    Fills exposed edges by extending edge colours with a soft blend. Returns
    ``{shifted, too_far, shift_x, shift_y, scale, faces}``.
    """
    union = union_face_box(faces)
    if not union:
        return {"shifted": False, "too_far": False, "shift_x": 0.0, "shift_y": 0.0, "scale": 1.0, "faces": faces}

    cx = float(union["x"]) + float(union["w"]) / 2
    cy = float(union["y"]) + float(union["h"]) / 2
    tx, ty = zones["face_centre_x"], zones["face_centre_y"]
    # Positive shift_y moves content down (face moves down in the frame).
    shift_x = tx - cx
    shift_y = ty - cy

    # Optional scale-down so the union fits inside the clear band.
    scale = 1.0
    clear_h = zones["clear_bottom"] - zones["clear_top"]
    if float(union["h"]) > clear_h * 0.85:
        scale = max(1.0 - max_scale, clear_h * 0.80 / max(float(union["h"]), 1e-6))

    if abs(shift_y) <= 0.01 and abs(shift_x) <= 0.01 and scale >= 0.99:
        return {"shifted": False, "too_far": False, "shift_x": 0.0, "shift_y": 0.0, "scale": 1.0, "faces": faces}
    if abs(shift_y) > max_shift or abs(shift_x) > max_shift:
        return {
            "shifted": False,
            "too_far": True,
            "shift_x": shift_x,
            "shift_y": shift_y,
            "scale": scale,
            "faces": faces,
        }

    path = Path(art_path)
    with Image.open(path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        if scale < 0.999:
            new_w = max(1, int(round(width * scale)))
            new_h = max(1, int(round(height * scale)))
            scaled = rgb.resize((new_w, new_h), Image.Resampling.LANCZOS)
            fill = _mean_rgb(list(rgb.crop((0, 0, width, max(1, height // 40))).getdata()))
            canvas = Image.new("RGB", (width, height), fill)
            ox = (width - new_w) // 2
            oy = (height - new_h) // 2
            canvas.paste(scaled, (ox, oy))
            rgb = canvas
            cx = 0.5 + (cx - 0.5) * scale
            cy = 0.5 + (cy - 0.5) * scale
            shift_x = tx - cx
            shift_y = ty - cy

        dx = int(round(shift_x * width))
        dy = int(round(shift_y * height))
        top_fill = _mean_rgb(list(rgb.crop((0, 0, width, max(1, min(8, height // 40)))).getdata()))
        bot_fill = _mean_rgb(
            list(rgb.crop((0, max(0, height - max(1, min(8, height // 40))), width, height)).getdata())
        )
        base_fill = top_fill if dy >= 0 else bot_fill
        out = Image.new("RGB", (width, height), base_fill)
        # Paste with clipping for negative offsets.
        src_x0 = max(0, -dx)
        src_y0 = max(0, -dy)
        dst_x0 = max(0, dx)
        dst_y0 = max(0, dy)
        src_x1 = min(width, width - dx)
        src_y1 = min(height, height - dy)
        if src_x1 > src_x0 and src_y1 > src_y0:
            cropped = rgb.crop((src_x0, src_y0, src_x1, src_y1))
            out.paste(cropped, (dst_x0, dst_y0))
        if dy > 0:
            blend = max(4, min(dy, height // 40))
            for row in range(blend):
                t = (row + 1) / (blend + 1)
                y_out = dy + row
                if y_out >= height:
                    break
                for x in range(width):
                    src_x = x - dx
                    if 0 <= src_x < width and row < height:
                        pixel = rgb.getpixel((src_x, row))
                        out.putpixel(
                            (x, y_out),
                            (
                                int(top_fill[0] * (1 - t) + pixel[0] * t),
                                int(top_fill[1] * (1 - t) + pixel[1] * t),
                                int(top_fill[2] * (1 - t) + pixel[2] * t),
                            ),
                        )
        out.save(path, format="PNG")

    new_faces = []
    for f in faces:
        nx = float(f["x"])
        ny = float(f["y"])
        nw = float(f["w"])
        nh = float(f["h"])
        if scale < 0.999:
            nx = 0.5 + (nx - 0.5) * scale
            ny = 0.5 + (ny - 0.5) * scale
            nw *= scale
            nh *= scale
        new_faces.append({"x": nx + shift_x, "y": ny + shift_y, "w": nw, "h": nh})
    return {
        "shifted": True,
        "too_far": False,
        "shift_x": shift_x,
        "shift_y": shift_y,
        "scale": scale,
        "faces": new_faces,
    }


def soften_zone(art_path: Path, zone: tuple[float, float, float, float]) -> None:
    """Flatten a fractional zone toward a sampled background colour with a soft inner edge."""
    path = Path(art_path)
    with Image.open(path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        x0 = max(0, int(zone[0] * width))
        y0 = max(0, int(zone[1] * height))
        x1 = min(width, max(x0 + 1, int(zone[2] * width + 0.999)))
        y1 = min(height, max(y0 + 1, int(zone[3] * height + 0.999)))
        if zone[1] <= 0.01:
            sample = rgb.crop((x0, y0, x1, min(y1, y0 + max(2, (y1 - y0) // 8))))
        else:
            sample = rgb.crop((x0, max(y0, y1 - max(2, (y1 - y0) // 8)), x1, y1))
        fill = _mean_rgb(list(sample.getdata()))
        zone_h = max(1, y1 - y0)
        feather = max(4, zone_h // 5)
        pixels = rgb.load()
        for y in range(y0, y1):
            dist_from_inner = (y1 - 1 - y) if zone[1] <= 0.01 else (y - y0)
            strength = 0.92 if dist_from_inner >= feather else 0.92 * (dist_from_inner / feather)
            for x in range(x0, x1):
                r, g, b = pixels[x, y][:3]
                pixels[x, y] = (
                    int(r * (1 - strength) + fill[0] * strength),
                    int(g * (1 - strength) + fill[1] * strength),
                    int(b * (1 - strength) + fill[2] * strength),
                )
        rgb.save(path, format="PNG")


def soften_front_keepclear(art_path: Path, zones: dict) -> None:
    soften_zone(art_path, (0.0, 0.0, 1.0, zones["clear_top"]))
    soften_zone(art_path, (0.25, zones["clear_bottom"], 0.75, 1.0))


def soften_back_text_band(art_path: Path, zones: dict | None = None) -> None:
    zones = zones or back_cover_zones()
    soften_zone(art_path, (0.0, zones["text_band_top"], 1.0, 1.0))


def layout_prompt_rules(kind: str, zones: dict) -> str:
    """Binding layout sentences appended to every cover prompt."""
    if kind == "front":
        top = zones["title_pct"]
        bottom = 100 - zones["logo_pct_from_bottom"]
        centre = zones["centre_pct"]
        return (
            f"LAYOUT (binding): All faces (the child and any companion such as Pip) centred "
            f"left-to-right, inside the middle band from {top}% to {bottom}% of the height; "
            f"face centre near {centre}% of the height. "
            f"The top {top}% is plain sky / foliage / wall for the title — no head, face, paw "
            f"or companion above {top}%. "
            f"The bottom {zones['logo_pct_from_bottom']}% is plain ground for the logo. "
            f"Companions sit beside or in front of the child at face height — never on the "
            f"shoulders, back, or above the child's head."
        )
    return (
        f"LAYOUT (binding): All faces stay above the bottom {zones['logo_pct_from_bottom']}% "
        f"(white text plate). Figures centred; no face in the bottom text band."
    )


def enforce_cover_layout(
    client,
    job_dir: Path,
    path: Path,
    kind: str,
    inputs: dict,
    story: dict,
    character_path: Path,
    *,
    regenerate_fn,
) -> dict:
    """Detect faces → reframe → optional one regen → force plate + warn. Never fails the job."""
    del client  # face detect is local OpenCV; regen uses regenerate_fn
    book_title = story.get("title") or story.get("locked", {}).get("title") or ""
    name = inputs.get("name") or ""
    zones = front_cover_zones(book_title, name) if kind == "front" else back_cover_zones()
    faces = detect_faces_local(path)
    append_event(
        job_dir,
        "cover_faces",
        f"{kind}: detected {len(faces)} face box(es).",
        kind=kind,
        faces=faces,
        zones={k: zones[k] for k in ("clear_top", "clear_bottom", "face_centre_y", "title_pct", "centre_pct") if k in zones},
    )
    check = check_front_faces(faces, zones) if kind == "front" else check_back_faces(faces, zones)
    layout: dict = {
        "force_soft_plate": False,
        "title_shrunk": False,
        "warning": False,
        "zones": zones,
        "attempts": [],
    }

    def _record(stage: str, faces_now: list[dict], check_now: dict, extra: dict | None = None):
        layout["attempts"].append(
            {"stage": stage, "faces": faces_now, "check": check_now, **(extra or {})}
        )

    _record("initial", faces, check)
    if check["passed"]:
        if kind == "front":
            soften_front_keepclear(path, zones)
        else:
            soften_back_text_band(path, zones)
        layout["passed"] = True
        return layout

    if kind == "front" and faces:
        reframe = reframe_cover_art(path, faces, zones)
        if reframe.get("shifted"):
            faces = reframe["faces"]
            check = check_front_faces(faces, zones)
            soften_front_keepclear(path, zones)
            _record("reframe", faces, check, {"reframe": {k: reframe[k] for k in ("shift_x", "shift_y", "scale", "too_far")}})
            append_event(
                job_dir,
                "cover_reframe",
                f"front: shifted dx={reframe['shift_x']:.2f} dy={reframe['shift_y']:.2f} scale={reframe['scale']:.2f}",
                kind=kind,
            )
            if check["passed"]:
                layout["passed"] = True
                layout["reframed"] = True
                return layout
        elif reframe.get("too_far"):
            append_event(
                job_dir,
                "cover_reframe_too_far",
                f"front: need dy={reframe['shift_y']:.2f} (> {MAX_REFRAME_SHIFT}); regenerating once.",
                kind=kind,
            )
            _record("reframe_too_far", faces, check, {"reframe": reframe})
            # One model regen with stronger layout emphasis.
            regenerate_fn(path)
            faces = detect_faces_local(path)
            check = check_front_faces(faces, zones)
            _record("regen", faces, check)
            append_event(job_dir, "cover_faces", f"front after regen: {len(faces)} face(s).", faces=faces)
            if not check["passed"] and faces:
                reframe2 = reframe_cover_art(path, faces, zones)
                if reframe2.get("shifted"):
                    faces = reframe2["faces"]
                    check = check_front_faces(faces, zones)
                    _record("reframe_after_regen", faces, check)
            soften_front_keepclear(path, zones)
            if check["passed"]:
                layout["passed"] = True
                layout["regenerated"] = True
                return layout
        else:
            soften_front_keepclear(path, zones)
    elif kind == "back":
        soften_back_text_band(path, zones)
        if not check["passed"]:
            regenerate_fn(path)
            faces = detect_faces_local(path)
            check = check_back_faces(faces, zones)
            soften_back_text_band(path, zones)
            _record("regen", faces, check)
            if check["passed"]:
                layout["passed"] = True
                layout["regenerated"] = True
                return layout

    # Keep best effort; force title plate + human warning.
    layout["passed"] = False
    layout["warning"] = True
    layout["force_soft_plate"] = kind == "front"
    layout["title_shrunk"] = kind == "front"
    append_event(
        job_dir,
        "cover_layout_warning",
        f"{kind}: layout still soft after reframe/regen — forcing title plate; human check needed. "
        f"Failures: {'; '.join(check.get('failures') or [])}",
        kind=kind,
        failures=check.get("failures"),
    )
    return layout


def write_cover_layout(job_dir: Path, layout: dict) -> None:
    cover_dir = Path(job_dir) / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    existing = read_json(cover_dir / "cover.json") or {}
    existing["layout"] = {
        "force_soft_plate": bool(layout.get("force_soft_plate")),
        "title_shrunk": bool(layout.get("title_shrunk")),
        "warning": bool(layout.get("warning")),
        "passed": bool(layout.get("passed")),
        "zones": layout.get("zones"),
    }
    write_json(cover_dir / "cover.json", existing)
