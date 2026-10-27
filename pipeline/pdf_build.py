"""Raised Ready print PDF: native-resolution art + live vector text (ReportLab).

Text is never drawn into an image. Each spread is ONE wide image (``spreads/spread-NN/art.png``)
that is opened once, split into a left and a right half (``pages.split_spread``) and placed at the
half's native pixels in the 216 mm (trim + bleed) box; nothing is resampled up. Fonts are the four brand TTFs only.

Packaging comes from the printer profile (constants.PRINTER_PROFILES):
  * ``single_pdf_cover_flat_first``      — TJ Books: cover flat, [endpaper], p1-32, [endpaper]
  * ``single_pdf_cover_first_back_last`` — Prodigi: front cover, p1-32, back cover
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .brand import (
    BAND_TEXT_COLOUR,
    COLOURS,
    FAMILY_COLOUR,
    HONESTY_LINE,
    LOGO_FILES,
    READ_IT_AGAIN_LINE,
    ROUTE_PRESETS,
    SAFE_MM,
    SIZE_SCALE,
)
from .catalogue import get_title
from .constants import (
    BACK_QR_SAFE_CORNER,
    BACK_QR_SAFE_MM,
    BLEED_MM,
    CONTENT_PAGE_COUNT,
    COVER_HEIGHT_MM,
    COVER_WIDTH_MM,
    PAGE_WITH_BLEED_MM,
    PRINTED_IN_UK_LINE,
    PRINTER,
    PRINTER_PROFILE_NAME,
    SPINE_MM,
    SPREAD_COUNT,
    SPREAD_WIDTH_MM,
    TEXT_PLATE,
    WRAP_MM,
    mm_to_pt,
)
from .fonts import normalize_font_id, register_brand_fonts, reportlab_name
from .illustrate import assert_print_resolution
from .job_log import append_event, read_json, write_json
from .pages import split_spread, spread_art_path, spread_art_rel

SITE_URL = "raisedready.co.uk"
CONTRAST_TARGET = 7.0  # story text on art: p10-based navy vs paper must clear 7:1
PLATE_ALPHA = 0.88
PLATE_LAYERS = 8
BAND_DEPTH_MM = float(SIZE_SCALE["band_depth_mm"])
TITLE_SIZE_FLOOR = float(SIZE_SCALE["front_title_rest"][0])  # 44 pt

INK = COLOURS["ink_navy"]["rgb"]
PAPER = COLOURS["paper"]["rgb"]
OAT = COLOURS["oat"]["rgb"]


# --- small helpers -------------------------------------------------------------------------------


def _c01(rgb) -> tuple[float, float, float]:
    return tuple(v / 255.0 for v in rgb)  # type: ignore[return-value]


def _lum(rgb) -> float:
    def lin(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb[:3]
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def safe_order_id(inputs: dict, job_dir: Path) -> str:
    raw = str(inputs.get("order_id") or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9-]", "", raw)
    return cleaned or re.sub(r"[^A-Za-z0-9-]", "", job_dir.name) or "order"


def pdf_path_for(job_dir: Path, inputs: dict) -> Path:
    return job_dir / "print" / f"{safe_order_id(inputs, job_dir)}-final.pdf"


def apply_post_grade(image: Image.Image, grade: str | None) -> Image.Image:
    """Lighting grade applied AFTER face QA (spec §8.3.6). Colour only; never resizes."""
    if not grade:
        return image
    from PIL import ImageEnhance

    rgb = image.convert("RGB")
    if grade == "night":
        tint, strength, bright = (20, 35, 70), 0.20, 0.74
    elif grade == "underwater":
        tint, strength, bright = (15, 82, 87), 0.24, 0.92
    else:
        return rgb
    rgb = ImageEnhance.Brightness(rgb).enhance(bright)
    return Image.blend(rgb, Image.new("RGB", rgb.size, tint), strength)


class _Art:
    """One page/cover image: embedded at native pixels, sampled (small copy) for contrast."""

    def __init__(self, path, work_dir: Path, grade: str | None = None, tag: str = ""):
        # ``path`` is a file, or an already-open PIL image (a half cut from a spread).
        if isinstance(path, Image.Image):
            rgb = path.convert("RGB")
            stem = "image"
        else:
            with Image.open(path) as source:
                rgb = source.convert("RGB")
            stem = Path(path).stem
        self.width_px, self.height_px = rgb.size
        graded = apply_post_grade(rgb, grade)
        work_dir.mkdir(parents=True, exist_ok=True)
        self.embed_path = work_dir / f"{tag or stem}.jpg"
        # Same pixel dimensions as the model output; JPEG only keeps the PDF a sane size.
        graded.save(self.embed_path, format="JPEG", quality=95, subsampling=0)
        small = graded.copy()
        small.thumbnail((160, 160))
        self.small = small
        self.rect_mm: tuple[float, float, float, float] | None = None

    def stats(self, box_mm: tuple[float, float, float, float]) -> dict:
        """Luminance stats for a box given in the same mm space the art was placed in."""
        assert self.rect_mm is not None
        ax, ay, aw, ah = self.rect_mm
        x0, y0, x1, y1 = box_mm
        sw, sh = self.small.size
        px0 = max(0, min(sw - 1, int((x0 - ax) / aw * sw)))
        px1 = max(px0 + 1, min(sw, int((x1 - ax) / aw * sw + 0.999)))
        py0 = max(0, min(sh - 1, int((y0 - ay) / ah * sh)))
        py1 = max(py0 + 1, min(sh, int((y1 - ay) / ah * sh + 0.999)))
        region = self.small.crop((px0, py0, px1, py1))
        pixels = list(region.getdata())
        lums = sorted(_lum(p) for p in pixels)
        n = len(lums)
        mean = sum(lums) / n
        std = (sum((v - mean) ** 2 for v in lums) / n) ** 0.5
        return {
            "p10": lums[int(0.10 * (n - 1))],
            "p90": lums[int(0.90 * (n - 1))],
            "std": std,
        }


@dataclass
class _Ctx:
    canvas: canvas.Canvas
    job_dir: Path
    work_dir: Path
    inputs: dict
    story: dict
    title: dict
    name: str
    family: str
    route: dict
    grade: str | None
    layout_log: list
    # Front cover art failed the layout check twice: soft plate under the TITLE BOX only (never a face).
    force_cover_plate: bool = False
    # Shrink the front title to the 44 pt floor (title would otherwise overlap the face).
    title_floor_only: bool = False
    # The spread being drawn: {"n", "source", "left", "right"}; one open + one split per spread.
    spread_cache: dict = field(default_factory=dict)


def _y(c: canvas.Canvas, y_mm: float) -> float:
    """Top-left mm -> PDF y (points, origin bottom-left)."""
    return c._pagesize[1] - mm_to_pt(y_mm)


def _x(x_mm: float) -> float:
    return mm_to_pt(x_mm)


def _place(c: canvas.Canvas, art: _Art, x: float, y: float, w: float, h: float, clip=None) -> None:
    art.rect_mm = (x, y, w, h)
    c.saveState()
    if clip is not None:
        cx, cy, cw, ch = clip
        path = c.beginPath()
        path.rect(_x(cx), _y(c, cy + ch), mm_to_pt(cw), mm_to_pt(ch))
        c.clipPath(path, stroke=0, fill=0)
    c.drawImage(
        ImageReader(str(art.embed_path)),
        _x(x),
        _y(c, y + h),
        width=mm_to_pt(w),
        height=mm_to_pt(h),
    )
    c.restoreState()


def _fill_rect(c, x, y, w, h, rgb) -> None:
    c.saveState()
    c.setFillColorRGB(*_c01(rgb))
    c.rect(_x(x), _y(c, y + h), mm_to_pt(w), mm_to_pt(h), stroke=0, fill=1)
    c.restoreState()


def _soft_plate(c, x, y, w, h) -> None:
    """Soft-edged paper plate (#FAF6EF, ~88 % opacity): feathered layers, no hard white box."""
    per_layer = 1 - (1 - PLATE_ALPHA) ** (1 / PLATE_LAYERS)
    c.saveState()
    c.setFillColorRGB(*_c01(PAPER))
    for i in range(PLATE_LAYERS):
        grow = (PLATE_LAYERS - 1 - i) * 0.8
        c.setFillAlpha(per_layer)
        c.roundRect(
            _x(x - grow),
            _y(c, y + h + grow),
            mm_to_pt(w + 2 * grow),
            mm_to_pt(h + 2 * grow),
            mm_to_pt(5 + grow),
            stroke=0,
            fill=1,
        )
    c.restoreState()


def _decide_ink(ctx: _Ctx, art: _Art | None, box_mm, allow_light_ink: bool = False, label: str = ""):
    """(ink_rgb, plate_bool). Navy on a light calm zone; plate only when contrast fails."""
    if art is None:
        return INK, False
    s = art.stats(box_mm)
    navy_contrast = (s["p10"] + 0.05) / (_lum(INK) + 0.05)
    if navy_contrast >= CONTRAST_TARGET and TEXT_PLATE != "always":
        return INK, False
    if TEXT_PLATE == "always":
        return INK, True
    if allow_light_ink:
        light_contrast = (_lum(PAPER) + 0.05) / (s["p90"] + 0.05)
        if light_contrast >= CONTRAST_TARGET:
            return PAPER, False
    if TEXT_PLATE == "none":
        append_event(
            ctx.job_dir,
            "text_contrast_warning",
            f"{label}: ink navy contrast {navy_contrast:.1f}:1 < {CONTRAST_TARGET} and TEXT_PLATE=none.",
        )
        return INK, False
    return INK, True


_SOFT_BREAK = frozenset({",", ";", ":", "—", "–", "and", "or", "but"})


def _rebalance_orphan_pair(first: list[str], second: list[str]) -> tuple[list[str], list[str]]:
    """Never leave a line with fewer than three words when the pair can be rebalanced."""
    if len(second) >= 3 or len(first) + len(second) < 6:
        return first, second
    # Aim for two roughly even lines, each ≥ 3 words.
    total = first + second
    best = len(first)
    for cut in range(3, len(total) - 2):
        if abs(cut - len(total) / 2) < abs(best - len(total) / 2):
            best = cut
    return total[:best], total[best:]


def _wrap(text: str, font: str, size: float, width_pt: float) -> list[str]:
    """Word-wrap; prefer breaks after punctuation / conjunctions; rebalance orphans (<3 words)."""
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current: list[str] = []
        for word in words:
            trial = current + [word]
            if current and pdfmetrics.stringWidth(" ".join(trial), font, size) > width_pt:
                # Prefer the latest soft-break point inside current.
                break_at = len(current)
                for index in range(len(current) - 1, 2, -1):
                    token = current[index - 1].rstrip(".,;:!?…\"'").lower()
                    punct = current[index - 1][-1:] in {",", ";", ":", "—", "–"}
                    if punct or token in _SOFT_BREAK:
                        if pdfmetrics.stringWidth(" ".join(current[:index]), font, size) <= width_pt:
                            break_at = index
                            break
                first, rest = current[:break_at], current[break_at:]
                rest = rest + [word]
                first, rest = _rebalance_orphan_pair(first, rest)
                # If rest still too wide, flush first and keep rest cooking.
                if pdfmetrics.stringWidth(" ".join(rest), font, size) <= width_pt or len(first) < 1:
                    lines.append(" ".join(first))
                    current = rest
                else:
                    lines.append(" ".join(first))
                    current = rest
            else:
                current = trial
        if current:
            if len(current) < 3 and lines:
                prev = lines[-1].split()
                first, rest = _rebalance_orphan_pair(prev, current)
                if len(rest) >= 3 or len(first) + len(rest) < 6:
                    lines[-1] = " ".join(first)
                    current = rest
            lines.append(" ".join(current))
    return lines


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    return [p for p in parts if p]


def fit_lines_for_story(text: str, font: str, size: float, width_pt: float) -> list[str]:
    """One sentence per line when it fits; otherwise wrap with orphan-aware rebalance."""
    lines: list[str] = []
    for sentence in _sentences(text) or [text]:
        if pdfmetrics.stringWidth(sentence, font, size) <= width_pt:
            lines.append(sentence)
        else:
            lines.extend(_wrap(sentence, font, size, width_pt))
    return lines


def _draw_lines(c, lines, font, size, x_mm, top_mm, leading_pt, rgb, align="left", width_mm=None):
    c.saveState()
    c.setFillColorRGB(*_c01(rgb))
    c.setFont(font, size)
    y = _y(c, top_mm) - size  # first baseline
    for line in lines:
        if align == "center":
            c.drawCentredString(_x(x_mm + (width_mm or 0) / 2), y, line)
        else:
            c.drawString(_x(x_mm), y, line)
        y -= leading_pt
    c.restoreState()


def _tracked_centered(c, text, font, size, centre_x_mm, baseline_mm, rgb, tracking_pct) -> None:
    char_space = size * tracking_pct / 100.0
    width = pdfmetrics.stringWidth(text, font, size) + char_space * max(0, len(text) - 1)
    t = c.beginText()
    t.setTextOrigin(_x(centre_x_mm) - width / 2, _y(c, baseline_mm))
    t.setFont(font, size)
    t.setCharSpace(char_space)
    t.setFillColorRGB(*_c01(rgb))
    t.textOut(text)
    c.drawText(t)


def _draw_logo(c, key: str, x_mm: float, y_mm: float, w_mm: float, rotate: bool = False) -> tuple[float, float]:
    path = LOGO_FILES[key]
    if not Path(path).is_file():
        raise FileNotFoundError(f"Brand logo master missing: {path}")
    with Image.open(path) as im:
        aspect = im.size[1] / im.size[0]
    h_mm = w_mm * aspect
    c.drawImage(
        ImageReader(str(path)),
        _x(x_mm),
        _y(c, y_mm + h_mm),
        width=mm_to_pt(w_mm),
        height=mm_to_pt(h_mm),
        mask="auto",
    )
    return w_mm, h_mm


# --- title string split (cover / title page) -------------------------------------------------------


def split_title(title: str, name: str) -> tuple[str | None, str]:
    """('[Name] and the', rest) per spec §4; no split when the pattern is not recognised."""
    for prefix in (f"{name} and the ", f"The Night {name}'s ", f"{name}'s ", f"{name}\u2019s "):
        if title.startswith(prefix):
            return prefix.strip(), title[len(prefix) :].strip()
    return None, title


def _title_layout(ctx: _Ctx, title: str, width_mm: float, *, floor_only: bool = False):
    """Noto Serif title lines: (line1, rest_lines, rest_size) with rest 44-52 pt, max 3 lines.

    ``floor_only`` forces the 44 pt floor (used when the title would otherwise overlap the face).
    """
    font = reportlab_name("title")
    line1, rest = split_title(title, ctx.name)
    lo, hi = SIZE_SCALE["front_title_rest"]
    width_pt = mm_to_pt(width_mm)
    if floor_only:
        size = int(lo)
        return line1, _wrap(rest, font, size, width_pt), size
    for size in range(hi, lo - 1, -2):
        lines = _wrap(rest, font, size, width_pt)
        if len(lines) <= 3:
            return line1, lines, size
    size = lo
    lines = _wrap(rest, font, size, width_pt)
    while len(lines) > 3 and size > 30:
        size -= 2
        lines = _wrap(rest, font, size, width_pt)
    append_event(
        ctx.job_dir,
        "title_size_warning",
        f"Title needs {size} pt (< {lo}) to fit 3 lines.",
        size=size,
    )
    return line1, lines, size


def _draw_title_block(
    ctx, c, art, title, centre_x_mm, top_mm, width_mm, allow_light_ink, label, force_plate=False
):
    font = reportlab_name("title")
    floor_only = bool(getattr(ctx, "title_floor_only", False))
    line1, lines, size = _title_layout(ctx, title, width_mm, floor_only=floor_only)
    l1_size = SIZE_SCALE["front_title_and_the"][0]
    l1_pt = l1_size * 1.25
    lead = size * 1.12
    height_pt = (l1_pt if line1 else 0) + lead * len(lines)
    height_mm = height_pt * 25.4 / 72
    box = (centre_x_mm - width_mm / 2, top_mm - 2, centre_x_mm + width_mm / 2, top_mm + height_mm + 2)
    ink, plate = _decide_ink(ctx, art, box, allow_light_ink=allow_light_ink, label=label)
    if force_plate and TEXT_PLATE != "none":
        ink, plate = INK, True
    if plate:
        _soft_plate(c, box[0] - 4, box[1] - 3, width_mm + 8, height_mm + 8)
    cursor = top_mm
    if line1:
        _draw_lines(c, [line1], font, l1_size, centre_x_mm - width_mm / 2, cursor, l1_pt, ink, "center", width_mm)
        cursor += l1_pt * 25.4 / 72
    _draw_lines(c, lines, font, size, centre_x_mm - width_mm / 2, cursor, lead, ink, "center", width_mm)
    return height_mm, ink, plate


def _draw_band(ctx, c, fill_x_mm, fill_w_mm, top_mm, trim_top_mm, text_cx_mm) -> None:
    """Family band: 13 mm deep below the trim line; DM Mono 10 pt caps, +12 % tracking."""
    colour = COLOURS[FAMILY_COLOUR[ctx.family]]["rgb"]
    ink = COLOURS[BAND_TEXT_COLOUR[ctx.family]]["rgb"]
    bottom = trim_top_mm + BAND_DEPTH_MM
    _fill_rect(c, fill_x_mm, top_mm, fill_w_mm, bottom - top_mm, colour)
    _tracked_centered(
        c,
        ctx.story["locked"]["front_band"].upper(),
        reportlab_name("label"),
        SIZE_SCALE["front_band"],
        text_cx_mm,
        trim_top_mm + BAND_DEPTH_MM / 2 + 1.3,
        ink,
        SIZE_SCALE["band_tracking_pct"],
    )


# --- interior pages ---------------------------------------------------------------------------------


def _blank_page(ctx: _Ctx, rgb=(255, 255, 255)) -> None:
    c = ctx.canvas
    c.setPageSize((mm_to_pt(PAGE_WITH_BLEED_MM),) * 2)
    _fill_rect(c, 0, 0, PAGE_WITH_BLEED_MM, PAGE_WITH_BLEED_MM, rgb)
    c.showPage()


def _paper_page(ctx: _Ctx) -> None:
    _fill_rect(ctx.canvas, 0, 0, PAGE_WITH_BLEED_MM, PAGE_WITH_BLEED_MM, PAPER)


def _page_text_box(side: str) -> tuple[float, float]:
    """(left_mm, width_mm) of the text column: >=10 mm from trim, >=15 mm from the gutter."""
    outer = BLEED_MM + SAFE_MM["trim"]
    gutter = BLEED_MM + SAFE_MM["gutter"]
    if side == "left":  # gutter is the right edge
        return outer, PAGE_WITH_BLEED_MM - outer - gutter
    return gutter, PAGE_WITH_BLEED_MM - outer - gutter


def _fit_story_text(ctx: _Ctx, text: str, width_mm: float, max_height_mm: float):
    route = ctx.route
    font = reportlab_name("body")
    start = float(route["story_pt"])
    leading = float(route["leading"])
    floor = 14.0
    size = start
    sentence_per_line = ctx.inputs.get("route") != "field_guide"
    while True:
        if sentence_per_line:
            lines = fit_lines_for_story(text, font, size, mm_to_pt(width_mm))
        else:
            lines = _wrap(text, font, size, mm_to_pt(width_mm))
        height_mm = len(lines) * size * leading * 25.4 / 72
        if height_mm <= max_height_mm or size <= floor:
            return lines, size, leading, height_mm
        size -= 1


def story_text_box_mm(side: str, height_mm: float) -> tuple[float, float, float, float]:
    """Centred text block on the calm page: (left, top, width, height) in page mm.

    Horizontal centre of ``_page_text_box``; vertical centre between the 10 mm top and bottom
    safe margins. The block never moves because of the art.
    """
    left, width = _page_text_box(side)
    top_safe = BLEED_MM + SAFE_MM["trim"]
    bottom_safe = PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"]
    available = bottom_safe - top_safe
    top = top_safe + max(0.0, (available - height_mm) / 2)
    return left, top, width, height_mm


def _spread_half(ctx: _Ctx, art_path: Path, n: int, side: str) -> Image.Image:
    """The ``side`` half of spread ``n``. The spread is opened and split ONCE; both halves come from it."""
    cache = ctx.spread_cache
    source = str(art_path)
    if cache.get("n") != n:
        left, right = split_spread(art_path)
        cache.clear()
        cache.update({"n": n, "source": source, "left": left, "right": right})
    if cache["source"] != source:
        raise AssertionError(
            f"Spread {n}: the two halves must come from the same art file "
            f"({cache['source']} vs {source})."
        )
    return cache[side]


def _draw_spread_page(ctx: _Ctx, page: dict, art_path: Path, side: str, index: int) -> None:
    c = ctx.canvas
    c.setPageSize((mm_to_pt(PAGE_WITH_BLEED_MM),) * 2)
    n = int(page["spread"])
    if page.get("art_path") not in (None, spread_art_rel(n)):
        raise AssertionError(f"Page {page['number']}: art_path {page.get('art_path')!r} is not spread {n}'s art.")
    half = _spread_half(ctx, art_path, n, side)
    art = _Art(half, ctx.work_dir, ctx.grade, tag=f"page-{page['number']:02d}")
    _place(c, art, 0, 0, PAGE_WITH_BLEED_MM, PAGE_WITH_BLEED_MM)
    record = {
        "page": page["number"],
        "kind": "spread_half",
        "spread": n,
        "side": side,
        "source": spread_art_rel(n),
        "text": False,
        "plate": False,
    }
    if page.get("calm_zone") == side and (page.get("text") or "").strip():
        left, width = _page_text_box(side)
        top_safe = BLEED_MM + SAFE_MM["trim"]
        bottom_safe = PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"]
        max_h = bottom_safe - top_safe
        lines, size, leading, height_mm = _fit_story_text(ctx, page["text"], width, max_h)
        if size < ctx.route["story_pt"] - 1:
            append_event(
                ctx.job_dir,
                "text_fit_warning",
                f"Page {page['number']}: story text set at {size:g} pt (brand {ctx.route['story_pt']} pt).",
                page=page["number"],
            )
        left, top, width, height_mm = story_text_box_mm(side, height_mm)
        box = (left, top, left + width, top + height_mm)
        ink, plate = _decide_ink(ctx, art, box, label=f"Page {page['number']}")
        if plate:
            # Soft plate under the text box only (+ 6 mm padding), never a whole panel.
            _soft_plate(c, left - 6, top - 6, width + 12, height_mm + 12)
        _draw_lines(
            c,
            lines,
            reportlab_name("body"),
            size,
            left,
            top,
            size * leading,
            ink,
            align="center",
            width_mm=width,
        )
        record.update(
            {
                "text": True,
                "plate": plate,
                "size_pt": size,
                "lines": len(lines),
                "box_mm": [left, top, width, height_mm],
            }
        )
    ctx.layout_log.append(record)
    c.showPage()


def _draw_title_page(ctx: _Ctx, page: dict) -> None:
    c = ctx.canvas
    c.setPageSize((mm_to_pt(PAGE_WITH_BLEED_MM),) * 2)
    _paper_page(ctx)
    page_w = PAGE_WITH_BLEED_MM
    _draw_band(ctx, c, 0, page_w, 0, BLEED_MM, page_w / 2)
    title = ctx.story["locked"]["title"]
    # Paper ground: ink navy always, no plate needed.
    _draw_title_block(ctx, c, None, title, page_w / 2, BLEED_MM + BAND_DEPTH_MM + 22, 170, False, "Title page")
    c.saveState()
    c.setFillColorRGB(*_c01(INK))
    c.setFont(reportlab_name("body"), SIZE_SCALE["illustrated_from"])
    c.drawCentredString(
        _x(page_w / 2),
        _y(c, page_w - BLEED_MM - SAFE_MM["trim"] - 38),
        f"Illustrated from {ctx.name}'s photo",
    )
    c.restoreState()
    spot = ctx.job_dir / "title_page" / "spot.png"
    if spot.is_file():
        art = _Art(spot, ctx.work_dir, None, tag="title-spot")
        side = 70.0
        _place(c, art, (page_w - side) / 2, BLEED_MM + BAND_DEPTH_MM + 62, side, side)
    else:
        append_event(ctx.job_dir, "title_spot_missing", "No title-page spot illustration; page 1 has none.")
    logo_w = 34.0
    _draw_logo(c, "one_ink", (page_w - logo_w) / 2, page_w - BLEED_MM - SAFE_MM["trim"] - 14, logo_w)
    ctx.layout_log.append({"page": 1, "kind": "title", "text": True, "plate": False})
    c.showPage()


def _parent_blocks(ctx: _Ctx) -> list[tuple[str, str]]:
    parent = ctx.story["locked"]["parent_spread"]
    blocks = [
        ("YOU COULD SAY", parent.get("you_could_say")),
        ("TRY TOGETHER", parent.get("try_together")),
        ("IN REAL LIFE", parent.get("in_real_life")),
        ("READ IT AGAIN", parent.get("read_it_again")),
    ]
    return [(label, body) for label, body in blocks if (body or "").strip()]


def _draw_parent_page(ctx: _Ctx, page: dict, side: str) -> None:
    c = ctx.canvas
    c.setPageSize((mm_to_pt(PAGE_WITH_BLEED_MM),) * 2)
    _paper_page(ctx)
    left, width = _page_text_box(side)
    blocks = _parent_blocks(ctx)
    half = (len(blocks) + 1) // 2
    mine = blocks[:half] if side == "left" else blocks[half:]
    top_safe = BLEED_MM + SAFE_MM["trim"] + 4
    bottom_safe = PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"]
    body_size = SIZE_SCALE["parent_body"]
    label_size = SIZE_SCALE.get("parent_label", 10)
    # Even vertical spacing of the blocks down the safe column.
    block_slots = len(mine) + (1 if side == "left" else 0)
    slot_h = (bottom_safe - top_safe) / max(1, block_slots + 1)
    cursor = top_safe
    if side == "left":
        heading = "WORDS FOR THE REAL MOMENT"
        size = 24
        lines = _wrap(heading, reportlab_name("title"), size, mm_to_pt(width))
        _draw_lines(c, lines, reportlab_name("title"), size, left, cursor, size * 1.2, INK)
        cursor += slot_h
    for label, body in mine:
        _draw_lines(c, [label], reportlab_name("label"), label_size, left, cursor, label_size * 1.3, INK)
        cursor += label_size * 1.3 * 25.4 / 72 + 2
        lines = _wrap(body, reportlab_name("body"), body_size, mm_to_pt(width))
        _draw_lines(c, lines, reportlab_name("body"), body_size, left, cursor, body_size * 1.45, INK)
        cursor += slot_h
    if side == "right":
        anchor = ctx.job_dir / "title_page" / "spot.png"
        if anchor.is_file():
            art = _Art(anchor, ctx.work_dir, None, tag="parent-spot")
            spot = float(SIZE_SCALE.get("parent_spot_mm", 90))
            _place(
                c,
                art,
                PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"] - spot,
                PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"] - spot,
                spot,
                spot,
            )
    ctx.layout_log.append({"page": page["number"], "kind": page["kind"], "text": True, "plate": False})
    c.showPage()


def _draw_dedication_page(ctx: _Ctx, page: dict) -> None:
    c = ctx.canvas
    c.setPageSize((mm_to_pt(PAGE_WITH_BLEED_MM),) * 2)
    _paper_page(ctx)
    left, width = _page_text_box("left")  # last page is a verso: gutter on the right
    # Printed verbatim as typed — no automatic "For {name}" prefix.
    dedication = (page.get("text") or "").strip()
    size = SIZE_SCALE["dedication"]
    cursor = PAGE_WITH_BLEED_MM * 0.30
    if dedication:
        lines = _wrap(dedication, reportlab_name("body"), size + 2, mm_to_pt(width))
        _draw_lines(c, lines, reportlab_name("body"), size + 2, left, cursor, (size + 2) * 1.45, INK)
    else:
        lines = []
    cursor += len(lines) * (size + 2) * 1.45 * 25.4 / 72 + 12
    note = _wrap(READ_IT_AGAIN_LINE, reportlab_name("body"), size, mm_to_pt(width))
    _draw_lines(c, note, reportlab_name("body"), size, left, cursor, size * 1.45, INK)
    bottom = PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"]
    logo_w = 34.0
    _draw_logo(c, "one_ink", left, bottom - 22, logo_w)
    _draw_lines(
        c,
        [SITE_URL],
        reportlab_name("body"),
        10,
        left + logo_w + 6,
        bottom - 17,
        13,
        INK,
    )
    code = safe_order_id(ctx.inputs, ctx.job_dir)
    _draw_lines(c, [code], reportlab_name("label"), SIZE_SCALE["order_code"], left, bottom - 4, 12, INK)
    ctx.layout_log.append({"page": 32, "kind": "dedication", "text": True, "plate": False, "order_code": code})
    c.showPage()


# --- cover ---------------------------------------------------------------------------------------


def _cover_blurb(ctx: _Ctx) -> str:
    return (ctx.title.get("blurb") or "").replace("{name}", ctx.name).strip()


def _strip_art(ctx, c, art: _Art, strip: tuple[float, float, float, float], align: str) -> None:
    sx, sy, sw, sh = strip
    side = max(sw, sh)
    ax = sx if align == "left" else sx + sw - side
    ay = sy + (sh - side) / 2
    _place(c, art, ax, ay, side, side, clip=strip)


def _draw_front(ctx: _Ctx, c, art: _Art, panel: tuple[float, float], strip, align: str) -> None:
    px, py = panel
    _strip_art(ctx, c, art, strip, align)
    trim_x, trim_y = px + BLEED_MM, py + BLEED_MM
    trim_w = PAGE_WITH_BLEED_MM - 2 * BLEED_MM
    sx, sy, sw, sh = strip
    _draw_band(ctx, c, sx, sw, sy, trim_y, trim_x + trim_w / 2)
    centre = trim_x + trim_w / 2
    height, _ink, _plate = _draw_title_block(
        ctx,
        c,
        art,
        ctx.story["locked"]["title"],
        centre,
        trim_y + BAND_DEPTH_MM + 11,
        trim_w - 30,
        True,
        "Front cover title",
        force_plate=ctx.force_cover_plate,
    )
    # Logo bottom centre; full colour only when >= 60 mm wide (else one-ink navy).
    logo_w = 62.0
    with Image.open(LOGO_FILES["full_colour"]) as im:
        logo_h = logo_w * im.size[1] / im.size[0]
    logo_y = trim_y + trim_w - 10 - logo_h
    box = (centre - logo_w / 2, logo_y, centre + logo_w / 2, logo_y + logo_h)
    s = art.stats(box)
    busy = s["std"] > 0.10 or s["p10"] < 0.25 or ctx.force_cover_plate
    if busy and TEXT_PLATE != "none":
        _soft_plate(c, box[0] - 6, box[1] - 4, logo_w + 12, logo_h + 8)
    _draw_logo(c, "full_colour", centre - logo_w / 2, logo_y, logo_w)


def _draw_back(ctx: _Ctx, c, art: _Art, panel: tuple[float, float], strip, align: str) -> None:
    px, py = panel
    _strip_art(ctx, c, art, strip, align)
    trim_x, trim_y = px + BLEED_MM, py + BLEED_MM
    trim_w = PAGE_WITH_BLEED_MM - 2 * BLEED_MM
    left = trim_x + 18
    width = trim_w - 36
    q_font, q_size = reportlab_name("title"), SIZE_SCALE["back_question"][0]
    question_lines = _wrap(ctx.story["locked"]["back_question"], q_font, q_size, mm_to_pt(width))
    blurb_size, blurb_lead = SIZE_SCALE["back_blurb"][0], 16
    blurb_lines = _wrap(_cover_blurb(ctx), reportlab_name("body"), blurb_size, mm_to_pt(width))
    honesty = HONESTY_LINE.format(name=ctx.name)
    honesty_lines = _wrap(honesty, reportlab_name("body"), 10, mm_to_pt(width))
    q_h = len(question_lines) * q_size * 1.2 * 25.4 / 72
    b_h = len(blurb_lines) * blurb_lead * 25.4 / 72
    h_h = len(honesty_lines) * 13 * 25.4 / 72
    logo_w = 62.0
    with Image.open(LOGO_FILES["full_colour"]) as im:
        logo_h = logo_w * im.size[1] / im.size[0]

    top = trim_y + 20
    gap = 8
    stack_h = q_h + gap + b_h + gap + h_h + gap + 6 + gap + logo_h
    box = (left, top, left + width, top + stack_h)
    ink, plate = _decide_ink(ctx, art, box, label="Back cover")
    if plate:
        _soft_plate(c, left - 8, top - 6, width + 16, stack_h + 12)
    cursor = top
    _draw_lines(c, question_lines, q_font, q_size, left, cursor, q_size * 1.2, ink)
    cursor += q_h + gap
    _draw_lines(c, blurb_lines, reportlab_name("body"), blurb_size, left, cursor, blurb_lead, ink)
    cursor += b_h + gap
    _draw_lines(c, honesty_lines, reportlab_name("body"), 10, left, cursor, 13, ink)
    cursor += h_h + gap
    _draw_lines(c, [SITE_URL], reportlab_name("body_bold"), 11, left, cursor, 14, ink)
    cursor += 6 + gap
    if PRINTED_IN_UK_LINE:  # OPEN flag, default off (needs a signed UK printer contract)
        _draw_lines(c, ["Printed in the UK"], reportlab_name("body"), 8, left, cursor - 4, 10, ink)
    _draw_logo(c, "full_colour", left, cursor, logo_w)

    # Keep-clear for the printer's QR / batch code: sample the mean colour of the art under the
    # box (never a flat paper rectangle). Text stays out of it; the printer prints over whatever is beneath.
    q = BACK_QR_SAFE_MM
    qx = trim_x + trim_w - q if "right" in BACK_QR_SAFE_CORNER else trim_x
    qy = trim_y + trim_w - q if "bottom" in BACK_QR_SAFE_CORNER else trim_y
    try:
        stats = art.stats((qx, qy, qx + q, qy + q))
        # Reconstruct an approximate mid-tone from the sampled luminance band.
        mid = (stats["p10"] + stats["p90"]) / 2
        tone = int(max(0, min(255, round(mid * 255))))
        _fill_rect(c, qx, qy, q, q, (tone, tone, tone))
    except Exception:  # noqa: BLE001 - leave the art if sampling fails
        pass


def _draw_spine(ctx: _Ctx, c, x_mm: float, y_mm: float, w_mm: float, h_mm: float) -> None:
    colour = COLOURS[FAMILY_COLOUR[ctx.family]]["rgb"]
    ink = COLOURS[BAND_TEXT_COLOUR[ctx.family]]["rgb"]
    _fill_rect(c, x_mm, y_mm, w_mm, h_mm, colour)
    cx = x_mm + w_mm / 2
    size = SIZE_SCALE["spine_title"]
    min_mm, max_mm = SIZE_SCALE["mini_r_mm"]
    mark = min_mm + (max_mm - min_mm) / 2
    # Title reads top to bottom (rotate -90): letter tops point toward the front cover.
    if SPINE_MM >= 8:  # spines under 8 mm: mini R only
        c.saveState()
        c.translate(_x(cx), _y(c, y_mm + WRAP_MM + BLEED_MM + 6))
        c.rotate(-90)
        c.setFillColorRGB(*_c01(ink))
        c.setFont(reportlab_name("title"), size)
        c.drawString(0, -size * 0.35, ctx.story["locked"]["title"])
        c.restoreState()
    mark_path = LOGO_FILES["mini_r"]
    with Image.open(mark_path) as im:
        aspect = im.size[1] / im.size[0]
    mw, mh = (mark / aspect, mark) if aspect >= 1 else (mark, mark * aspect)
    c.saveState()
    c.translate(_x(cx), _y(c, y_mm + h_mm - WRAP_MM - BLEED_MM - 6))
    c.rotate(-90)
    # after rotation: local +x runs down the spine; draw the mark ending at the foot
    c.drawImage(
        ImageReader(str(mark_path)),
        -mm_to_pt(mw),
        -mm_to_pt(mh) / 2,
        width=mm_to_pt(mw),
        height=mm_to_pt(mh),
        mask="auto",
    )
    c.restoreState()


def build_cover(ctx: _Ctx, front_art: Path, back_art: Path, mode: str):
    """mode='flat': one wraparound page; mode='panels': (front_page_fn, back_page_fn) drawn per page."""
    c = ctx.canvas
    front = _Art(front_art, ctx.work_dir, None, tag="cover-front")
    back = _Art(back_art, ctx.work_dir, None, tag="cover-back")
    if mode == "flat":
        c.setPageSize((mm_to_pt(COVER_WIDTH_MM), mm_to_pt(COVER_HEIGHT_MM)))
        _fill_rect(c, 0, 0, COVER_WIDTH_MM, COVER_HEIGHT_MM, OAT)
        panel = PAGE_WITH_BLEED_MM
        back_panel = (WRAP_MM, WRAP_MM)
        spine_x = WRAP_MM + panel
        front_panel = (spine_x + SPINE_MM, WRAP_MM)
        back_strip = (0.0, 0.0, WRAP_MM + panel, COVER_HEIGHT_MM)
        front_strip = (front_panel[0], 0.0, panel + WRAP_MM, COVER_HEIGHT_MM)
        _draw_back(ctx, c, back, back_panel, back_strip, "left")
        _draw_front(ctx, c, front, front_panel, front_strip, "right")
        _draw_spine(ctx, c, spine_x, 0.0, SPINE_MM, COVER_HEIGHT_MM)
        ctx.layout_log.append({"page": "cover", "kind": "cover_flat", "title": ctx.story["locked"]["title"]})
        c.showPage()
        return
    # Panels (Prodigi): front first, back last — caller sequences the pages.
    strip = (0.0, 0.0, PAGE_WITH_BLEED_MM, PAGE_WITH_BLEED_MM)
    return (
        lambda: _single_cover_page(ctx, c, front, strip, front=True),
        lambda: _single_cover_page(ctx, c, back, strip, front=False),
    )


def _single_cover_page(ctx, c, art: _Art, strip, front: bool) -> None:
    c.setPageSize((mm_to_pt(PAGE_WITH_BLEED_MM),) * 2)
    _fill_rect(c, 0, 0, PAGE_WITH_BLEED_MM, PAGE_WITH_BLEED_MM, OAT)
    (_draw_front if front else _draw_back)(ctx, c, art, (0.0, 0.0), strip, "left")
    ctx.layout_log.append({"page": "front_cover" if front else "back_cover", "kind": "cover_panel"})
    c.showPage()


# --- page map + font assertions ----------------------------------------------------------------------


def assert_page_map(pages: list) -> None:
    if len(pages) != CONTENT_PAGE_COUNT:
        raise ValueError(f"Page map has {len(pages)} pages, expected {CONTENT_PAGE_COUNT}.")
    by_number = {p["number"]: p for p in pages}
    if sorted(by_number) != list(range(1, CONTENT_PAGE_COUNT + 1)):
        raise ValueError("Pages must be numbered 1..32.")
    expected = {1: "title", 30: "parent_left", 31: "parent_right", 32: "dedication"}
    for number, kind in expected.items():
        if by_number[number].get("kind") != kind:
            raise ValueError(f"Page {number} must be '{kind}'.")
    for number in range(2, 2 * SPREAD_COUNT + 2):
        page = by_number[number]
        if page.get("kind") != "spread_half" or page.get("spread") != number // 2:
            raise ValueError(f"Page {number} must be a half of spread {number // 2}.")


def brand_font_names() -> set[str]:
    names = set()
    for role in ("title", "body", "body_bold", "label"):
        face = pdfmetrics.getFont(reportlab_name(role)).face.name
        names.add(face.decode() if isinstance(face, bytes) else str(face))
    return names


def pdf_font_names(pdf_path: Path) -> set[str]:
    """BaseFont names (subset prefix stripped) of every font used by any page."""
    from pypdf import PdfReader

    found: set[str] = set()
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        fonts = (resources.get("/Font") or {}) if hasattr(resources, "get") else {}
        for ref in (fonts.values() if hasattr(fonts, "values") else []):
            font = ref.get_object()
            base = str(font.get("/BaseFont", ""))
            found.add(re.sub(r"^/?[A-Z]{6}\+", "", base).lstrip("/"))
    return found


def assert_brand_fonts_only(pdf_path: Path) -> set[str]:
    used = pdf_font_names(pdf_path)
    allowed = brand_font_names()
    stray = {name for name in used if name and name not in allowed}
    if stray:
        raise RuntimeError(f"Non-brand fonts in PDF (system fallback?): {sorted(stray)}")
    return used


# --- previews (QA / operator UI only; never the print file) ---------------------------------------------


def render_previews(pdf_path: Path, job_dir: Path, packaging: str, endpapers: bool) -> int:
    """JPEG page previews rendered from the finished PDF (so they show the real vector text)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover
        append_event(job_dir, "preview_render_skipped", "pypdfium2 not installed; no composed previews.")
        return 0
    pages_dir = job_dir / "print" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if packaging == "single_pdf_cover_first_back_last":
            first_interior = 1
        else:
            first_interior = 1 + (1 if endpapers else 0)
        pdf[0].render(scale=0.9).to_pil().convert("RGB").save(job_dir / "print" / "cover.jpg", quality=88)
        if packaging == "single_pdf_cover_first_back_last":
            pdf[len(pdf) - 1].render(scale=0.9).to_pil().convert("RGB").save(job_dir / "print" / "cover-back.jpg", quality=88)
        count = 0
        for i in range(CONTENT_PAGE_COUNT):
            image = pdf[first_interior + i].render(scale=1.1).to_pil().convert("RGB")
            image.save(pages_dir / f"page-{i + 1:02d}.jpg", quality=86)
            count += 1
        stitch_spread_previews(job_dir)
        return count
    finally:
        pdf.close()


def stitch_spread_previews(job_dir: Path) -> int:
    """``print/spreads/spread-NN.jpg``: composed left + right page joined into one wide spread.

    The pages overlap by the 6 mm that binding and trim consume (210 -> 216 mm), so the right page's
    first 6 mm is dropped when it is butted against the left one. Operator UI only.
    """
    pages_dir = job_dir / "print" / "pages"
    out_dir = job_dir / "print" / "spreads"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for n in range(1, SPREAD_COUNT + 1):
        left_path = pages_dir / f"page-{2 * n:02d}.jpg"
        right_path = pages_dir / f"page-{2 * n + 1:02d}.jpg"
        if not (left_path.is_file() and right_path.is_file()):
            continue
        with Image.open(left_path) as left, Image.open(right_path) as right:
            overlap = round(left.width * (2 * PAGE_WITH_BLEED_MM - SPREAD_WIDTH_MM) / PAGE_WITH_BLEED_MM)
            stitched = Image.new("RGB", (left.width + right.width - overlap, left.height))
            stitched.paste(left.convert("RGB"), (0, 0))
            stitched.paste(right.convert("RGB").crop((overlap, 0, right.width, right.height)), (left.width, 0))
            stitched.save(out_dir / f"spread-{n:02d}.jpg", quality=86)
        done += 1
    return done


def _cover_forces_plate(job_dir: Path) -> bool:
    """True when cover generation asks for a soft plate under the title box (never over a face)."""
    cover = read_json(Path(job_dir) / "cover" / "cover.json") or {}
    return bool((cover.get("layout") or {}).get("force_soft_plate"))


def _cover_title_floor(job_dir: Path) -> bool:
    cover = read_json(Path(job_dir) / "cover" / "cover.json") or {}
    return bool((cover.get("layout") or {}).get("title_shrunk"))


def render_front_preview(job_dir: Path, inputs: dict, story: dict, scale: float = 1.4) -> Path | None:
    """Compose the front cover (art + band + title + logo, via ``_draw_front``) to cover/front_preview.png.

    Same drawing code as the print cover, rendered from a throw-away single-page PDF. Operator UI only.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover
        append_event(job_dir, "cover_preview_skipped", "pypdfium2 not installed; no composed front preview.")
        return None
    register_brand_fonts()
    job_dir = Path(job_dir)
    front_art = job_dir / "cover" / "front_art.png"
    if not front_art.is_file():
        return None
    title = get_title(inputs["title_id"])
    work_dir = job_dir / "cover" / "_preview_work"
    tmp_pdf = work_dir / "front_preview.pdf"
    work_dir.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(
        str(tmp_pdf),
        pagesize=(mm_to_pt(PAGE_WITH_BLEED_MM),) * 2,
        initialFontName=reportlab_name("body"),
    )
    ctx = _Ctx(
        canvas=c,
        job_dir=job_dir,
        work_dir=work_dir,
        inputs=inputs,
        story=story,
        title=title,
        name=inputs["name"],
        family=title["family"],
        route=ROUTE_PRESETS[inputs.get("route") or "morning_light"],
        grade=None,
        layout_log=[],
        force_cover_plate=_cover_forces_plate(job_dir),
        title_floor_only=_cover_title_floor(job_dir),
    )
    art = _Art(front_art, work_dir, None, tag="front-preview")
    strip = (0.0, 0.0, PAGE_WITH_BLEED_MM, PAGE_WITH_BLEED_MM)
    _single_cover_page(ctx, c, art, strip, front=True)
    c.save()
    out = job_dir / "cover" / "front_preview.png"
    pdf = pdfium.PdfDocument(str(tmp_pdf))
    try:
        pdf[0].render(scale=scale).to_pil().convert("RGB").save(out, format="PNG")
    finally:
        pdf.close()
    for leftover in work_dir.iterdir():
        leftover.unlink(missing_ok=True)
    work_dir.rmdir()
    return out


# --- entry point ----------------------------------------------------------------------------------------


def build_pdf(job_dir: Path, story: dict, pages: list, inputs: dict) -> Path:
    register_brand_fonts()  # RuntimeError (naming the TTF) if a brand font is missing
    normalize_font_id(inputs.get("font_id"))
    assert_page_map(pages)
    title = get_title(inputs["title_id"])
    route_key = inputs.get("route") or "morning_light"
    packaging = PRINTER["packaging"]
    endpapers = bool(PRINTER.get("endpapers_in_pdf"))

    missing = [
        str(spread_art_path(job_dir, n))
        for n in range(1, SPREAD_COUNT + 1)
        if not spread_art_path(job_dir, n).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"Missing spread art ({len(missing)}): {missing[0]} …")
    front_art = job_dir / "cover" / "front_art.png"
    back_art = job_dir / "cover" / "back_art.png"
    if not (front_art.is_file() and back_art.is_file()):
        raise FileNotFoundError("Cover art missing (front_art.png / back_art.png).")
    lowest_ppi = assert_print_resolution(job_dir)  # fail < 150 ppi; warn events already logged

    pdf_path = pdf_path_for(job_dir, inputs)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = job_dir / "print" / "art"
    append_event(
        job_dir,
        "pdf_start",
        f"Building {PRINTER_PROFILE_NAME} PDF: live vector text, native spread art cut into "
        f"{PAGE_WITH_BLEED_MM:g} mm pages, packaging={packaging}.",
        packaging=packaging,
        printer_profile=PRINTER_PROFILE_NAME,
    )

    c = canvas.Canvas(
        str(pdf_path),
        pagesize=(mm_to_pt(PAGE_WITH_BLEED_MM),) * 2,
        initialFontName=reportlab_name("body"),
        pageCompression=1,
    )
    c.setTitle(story["locked"]["title"])
    c.setAuthor("Raised Ready")
    ctx = _Ctx(
        canvas=c,
        job_dir=job_dir,
        work_dir=work_dir,
        inputs=inputs,
        story=story,
        title=title,
        name=inputs["name"],
        family=title["family"],
        route=ROUTE_PRESETS[route_key],
        grade=title.get("post_grade") or None,
        layout_log=[],
        force_cover_plate=_cover_forces_plate(job_dir),
        title_floor_only=_cover_title_floor(job_dir),
    )

    front_fn = back_fn = None
    if packaging == "single_pdf_cover_flat_first":
        build_cover(ctx, front_art, back_art, "flat")
        if endpapers:
            _blank_page(ctx)
    elif packaging == "single_pdf_cover_first_back_last":
        front_fn, back_fn = build_cover(ctx, front_art, back_art, "panels")
        front_fn()
    else:
        raise ValueError(f"Unknown packaging mode: {packaging}")

    for page in sorted(pages, key=lambda p: p["number"]):
        kind = page["kind"]
        if kind == "title":
            _draw_title_page(ctx, page)
        elif kind == "spread_half":
            n, side = page["spread"], page["side"]
            _draw_spread_page(ctx, page, spread_art_path(job_dir, n), side, page["number"])
        elif kind == "parent_left":
            _draw_parent_page(ctx, page, "left")
        elif kind == "parent_right":
            _draw_parent_page(ctx, page, "right")
        elif kind == "dedication":
            _draw_dedication_page(ctx, page)

    if packaging == "single_pdf_cover_flat_first":
        if endpapers:
            _blank_page(ctx)
    else:
        back_fn()
    c.save()

    fonts_used = assert_brand_fonts_only(pdf_path)
    write_json(job_dir / "print" / "layout.json", {"pages": ctx.layout_log, "fonts": sorted(fonts_used)})
    previews = render_previews(pdf_path, job_dir, packaging, endpapers)
    append_event(
        job_dir,
        "pdf_done",
        f"PDF saved: {pdf_path.name} ({CONTENT_PAGE_COUNT} content pages, min {lowest_ppi} ppi).",
        pdf=pdf_path.relative_to(job_dir).as_posix(),
        content_pages=CONTENT_PAGE_COUNT,
        endpapers=2 if endpapers and packaging == "single_pdf_cover_flat_first" else 0,
        min_effective_ppi=lowest_ppi,
        fonts=sorted(fonts_used),
        previews=previews,
    )
    return pdf_path
