"""Overlay story text onto page art and assemble a TJ print PDF.

Inner pages: 210×210 mm trim + 3 mm bleed (single pages, no colour profile).
Cover flat: 16 mm wrap + 216 back + 8 mm spine + 216 front + 16 mm wrap.
Endpapers are plain and not counted in the 32 content pages.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

from .constants import (
    BACK_QR_SAFE_PX,
    BLEED_PX,
    COVER_HEIGHT_PT,
    COVER_PANEL_PX,
    COVER_PIXEL_HEIGHT,
    COVER_PIXEL_WIDTH,
    COVER_WIDTH_PT,
    FONTS_DIR,
    PAGE_HEIGHT_PT,
    PAGE_PIXEL_HEIGHT,
    PAGE_PIXEL_WIDTH,
    PAGE_WIDTH_PT,
    SPINE_MM,
    SPINE_PX,
    SPINE_WIDTH_PT,
    WRAP_PX,
)
from .fonts import font_file_path, normalize_font_id
from .job_log import append_event


def _load_font(size: int, font_id: str | None = None):
    candidates = []
    primary = font_file_path(font_id, role="body")
    if primary is not None:
        candidates.append(primary)
    title_path = font_file_path(font_id, role="title")
    if title_path is not None:
        candidates.append(title_path)
    candidates.extend(
        [
            FONTS_DIR / "Nunito-SemiBold.ttf",
            FONTS_DIR / "Nunito-Regular.ttf",
        ]
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("Missing Raised Ready brand font files under assets/fonts/")


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int):
    paragraphs = (text or "").split("\n")
    lines = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            width, _ = _text_size(draw, trial, font)
            if width <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _measure_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    line_gap: int,
):
    if not lines:
        return 0, 0
    widths = []
    heights = []
    for line in lines:
        w, h = _text_size(draw, line or " ", font)
        widths.append(w)
        heights.append(h)
    total_h = sum(heights) + line_gap * max(0, len(lines) - 1)
    return max(widths) if widths else 0, total_h


def _placement_top(placement: str, height: int, box_height: int) -> int:
    # Stay inside trim (outside the 3 mm bleed) with a little breathing room.
    safe = BLEED_PX + int(height * 0.04)
    if placement == "top":
        return safe
    if placement == "upper_third":
        return int(height * 0.12)
    if placement == "middle":
        return max(int(height * 0.5 - box_height / 2), safe)
    if placement == "lower_third":
        return int(height * 0.58)
    return int(height * 0.68)


def _fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_id: str | None,
    max_width: int,
    max_height: int,
    min_size: int = 48,
    max_size: int = 110,
):
    """Largest font that fits text in the max box; returns font, lines, sizes."""
    best = None
    low, high = min_size, max_size
    while low <= high:
        size = (low + high) // 2
        font = _load_font(size, font_id)
        line_gap = max(8, size // 5)
        lines = _wrap_lines(draw, text, font, max_width)
        block_w, block_h = _measure_block(draw, lines, font, line_gap)
        if block_h <= max_height and block_w <= max_width:
            best = (font, lines, block_w, block_h, line_gap, size)
            low = size + 1
        else:
            high = size - 1

    if best:
        return best

    font = _load_font(min_size, font_id)
    line_gap = max(8, min_size // 5)
    lines = _wrap_lines(draw, text, font, max_width)
    kept = []
    height = 0
    for line in lines:
        _, h = _text_size(draw, line or " ", font)
        next_h = height + h + (line_gap if kept else 0)
        if next_h > max_height:
            break
        kept.append(line)
        height = next_h
    block_w, block_h = _measure_block(draw, kept, font, line_gap)
    return font, kept, block_w, block_h, line_gap, min_size


def overlay_text_on_image(
    art_path: Path,
    text: str,
    placement: str,
    output_path: Path,
    title: str | None = None,
    font_id: str | None = None,
):
    font_id = normalize_font_id(font_id)
    with Image.open(art_path) as source:
        image = source.convert("RGBA").resize(
            (PAGE_PIXEL_WIDTH, PAGE_PIXEL_HEIGHT),
            Image.Resampling.LANCZOS,
        )

    draw = ImageDraw.Draw(image, "RGBA")
    # Keep text inside trim + margin (outside 3 mm bleed).
    margin_x = BLEED_PX + int(image.width * 0.06)
    inner_pad_x = 40
    inner_pad_y = 36
    max_box_w = image.width - 2 * margin_x
    text_max_w = max_box_w - 2 * inner_pad_x
    max_box_h = int(image.height * 0.28)

    body = (text or "").strip()
    full_text = body
    if title:
        full_text = f"{title.strip()}\n{body}".strip() if body else title.strip()

    font, lines, block_w, block_h, line_gap, _size = _fit_text_block(
        draw, full_text, font_id, text_max_w, max_box_h - 2 * inner_pad_y
    )

    box_w = min(max_box_w, max(block_w + 2 * inner_pad_x, int(image.width * 0.55)))
    box_h = min(max_box_h, block_h + 2 * inner_pad_y)
    box_h = max(box_h, int(image.height * 0.12))

    left = margin_x + (max_box_w - box_w) // 2
    top = _placement_top(placement, image.height, box_h)
    bottom_limit = image.height - BLEED_PX - int(image.height * 0.03)
    if top + box_h > bottom_limit:
        top = bottom_limit - box_h
    right = left + box_w
    bottom = top + box_h

    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=42,
        fill=(255, 250, 240, 225),
    )

    text_color = (28, 38, 52, 255)
    cursor_y = top + (box_h - block_h) // 2
    for line in lines:
        w, h = _text_size(draw, line or " ", font)
        x = left + (box_w - w) // 2
        draw.text((x, cursor_y), line, font=font, fill=text_color)
        cursor_y += h + line_gap

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, format="JPEG", quality=95, optimize=True)
    return output_path


def _cover_side_strip(art: Image.Image, *, side: str) -> Image.Image:
    """Full-bleed strip for one cover side: panel + outer wrap, top/bottom wraps.

    back:  [left wrap | panel]
    front: [panel | right wrap]
    """
    target_w = COVER_PANEL_PX + WRAP_PX
    target_h = COVER_PIXEL_HEIGHT
    src = art.convert("RGB")
    scale = max(target_w / src.width, target_h / src.height)
    nw = max(target_w, int(src.width * scale + 0.5))
    nh = max(target_h, int(src.height * scale + 0.5))
    scaled = src.resize((nw, nh), Image.Resampling.LANCZOS)
    # Bias crop so the outer wrap gets edge pixels and the spine edge stays inward.
    if side == "back":
        left = 0  # keep left (outer) edge
        top = (nh - target_h) // 2
    elif side == "front":
        left = nw - target_w  # keep right (outer) edge
        top = (nh - target_h) // 2
    else:
        raise ValueError(side)
    return scaled.crop((left, top, left + target_w, top + target_h))


def build_wraparound_cover(
    back_art: Path,
    front_art: Path,
    title: str,
    subtitle: str,
    child_name: str,
    output_path: Path,
    font_id: str | None = None,
):
    font_id = normalize_font_id(font_id)
    cover = Image.new("RGB", (COVER_PIXEL_WIDTH, COVER_PIXEL_HEIGHT), (245, 234, 213))

    back_src = Image.open(back_art).convert("RGB")
    front_src = Image.open(front_art).convert("RGB")

    back_strip = _cover_side_strip(back_src, side="back")
    front_strip = _cover_side_strip(front_src, side="front")

    # Title overlay on the front *panel* only (not into wraps).
    front_panel = front_strip.crop(
        (0, WRAP_PX, COVER_PANEL_PX, WRAP_PX + COVER_PANEL_PX)
    ).convert("RGBA")
    draw = ImageDraw.Draw(front_panel, "RGBA")
    margin_x = int(front_panel.width * 0.08)
    max_box_w = front_panel.width - 2 * margin_x
    text_max_w = max_box_w - 80
    max_box_h = int(front_panel.height * 0.30)

    cover_text = title
    if subtitle:
        cover_text = f"{title}\n{subtitle}"
    cover_text = f"{cover_text}\nstarring {child_name}"

    font, lines, block_w, block_h, line_gap, _ = _fit_text_block(
        draw,
        cover_text,
        font_id,
        text_max_w,
        max_box_h - 72,
        min_size=52,
        max_size=120,
    )
    box_w = min(max_box_w, max(block_w + 80, int(front_panel.width * 0.7)))
    box_h = min(max_box_h, block_h + 72)
    left = margin_x + (max_box_w - box_w) // 2
    top = int(front_panel.height * 0.06)
    right = left + box_w
    bottom = top + box_h
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=44, fill=(255, 250, 240, 230)
    )

    cursor_y = top + (box_h - block_h) // 2
    for line in lines:
        color = (
            (200, 100, 70, 255)
            if line.lower().startswith("starring")
            else (28, 38, 52, 255)
        )
        w, h = _text_size(draw, line or " ", font)
        x = left + (box_w - w) // 2
        draw.text((x, cursor_y), line, font=font, fill=color)
        cursor_y += h + line_gap
    front_strip.paste(front_panel.convert("RGB"), (0, WRAP_PX))

    # Soft clear zone for TJ QR/batch — bottom-right of back cover (near spine).
    # back strip: [wrap | panel], so panel right edge is at WRAP+PANEL.
    qr_left = WRAP_PX + COVER_PANEL_PX - BACK_QR_SAFE_PX
    qr_top = WRAP_PX + COVER_PANEL_PX - BACK_QR_SAFE_PX
    qr_right = WRAP_PX + COVER_PANEL_PX
    qr_bottom = WRAP_PX + COVER_PANEL_PX
    back_draw = ImageDraw.Draw(back_strip)
    sample = back_strip.getpixel(
        (max(0, qr_left - 4), max(0, min(back_strip.height - 1, qr_top - 4)))
    )
    back_draw.rectangle((qr_left, qr_top, qr_right, qr_bottom), fill=sample)

    spine = Image.new("RGB", (max(1, SPINE_PX), COVER_PIXEL_HEIGHT), (70, 110, 100))

    # Flat: left wrap+back | spine | front+right wrap
    cover.paste(back_strip, (0, 0))
    cover.paste(spine, (WRAP_PX + COVER_PANEL_PX, 0))
    cover.paste(front_strip, (WRAP_PX + COVER_PANEL_PX + SPINE_PX, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cover.save(output_path, format="JPEG", quality=95, optimize=True)
    return output_path


def _blank_page_jpg(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (PAGE_PIXEL_WIDTH, PAGE_PIXEL_HEIGHT), (255, 255, 255)).save(
        output_path, format="JPEG", quality=95
    )
    return output_path


def build_pdf(job_dir: Path, story: dict, pages: list, inputs: dict) -> Path:
    print_dir = job_dir / "print"
    print_dir.mkdir(parents=True, exist_ok=True)
    pages_print = print_dir / "pages"
    pages_print.mkdir(parents=True, exist_ok=True)

    font_id = normalize_font_id(inputs.get("font_id"))
    append_event(
        job_dir,
        "pdf_start",
        f"Overlaying text ({font_id}) and building TJ print PDF "
        f"(210×210+3mm bleed, {SPINE_MM:g}mm spine, 16mm wrap).",
        font_id=font_id,
    )

    front_art = job_dir / "cover" / "front_art.png"
    back_art = job_dir / "cover" / "back_art.png"
    cover_jpg = build_wraparound_cover(
        back_art,
        front_art,
        story["title"],
        story.get("subtitle") or "",
        inputs["name"],
        print_dir / "cover.jpg",
        font_id=font_id,
    )

    composed_pages = []
    for page in pages:
        page_dir = job_dir / "pages" / f"page-{page['number']:02d}"
        out = pages_print / f"page-{page['number']:02d}.jpg"
        if page.get("blank"):
            _blank_page_jpg(out)
        else:
            art = page_dir / "art.png"
            if not art.is_file():
                raise FileNotFoundError(f"Missing art for page {page['number']}")
            overlay_text_on_image(
                art,
                page.get("text") or "",
                page.get("text_placement") or "bottom",
                out,
                font_id=font_id,
            )
        composed_pages.append(out)

    front_endpaper = _blank_page_jpg(pages_print / "endpaper-front.jpg")
    back_endpaper = _blank_page_jpg(pages_print / "endpaper-back.jpg")

    # Single-page PDF, no colour profile. Cover flat first, then plain endpapers
    # (not counted in the 32), then 32 content pages, then back endpaper.
    pdf_path = job_dir / "final_book.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(COVER_WIDTH_PT, COVER_HEIGHT_PT))
    pdf.drawImage(
        str(cover_jpg),
        0,
        0,
        width=COVER_WIDTH_PT,
        height=COVER_HEIGHT_PT,
        preserveAspectRatio=False,
    )
    pdf.showPage()
    pdf.setPageSize((PAGE_WIDTH_PT, PAGE_HEIGHT_PT))

    for page_path in [front_endpaper, *composed_pages, back_endpaper]:
        pdf.drawImage(
            str(page_path),
            0,
            0,
            width=PAGE_WIDTH_PT,
            height=PAGE_HEIGHT_PT,
            preserveAspectRatio=True,
            anchor="c",
        )
        pdf.showPage()

    pdf.save()
    append_event(
        job_dir,
        "pdf_done",
        f"PDF saved (wraparound cover + endpapers + {len(composed_pages)} content pages).",
        pdf="final_book.pdf",
        spine_width_pt=SPINE_WIDTH_PT,
        content_pages=len(composed_pages),
        endpapers=2,
        font_id=font_id,
    )
    return pdf_path
