"""Overlay story text onto page art and assemble a wraparound print PDF."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

from .constants import (
    COVER_HEIGHT_PT,
    COVER_PIXEL_HEIGHT,
    COVER_PIXEL_WIDTH,
    COVER_WIDTH_PT,
    FONTS_DIR,
    PAGE_HEIGHT_PT,
    PAGE_PIXEL_HEIGHT,
    PAGE_PIXEL_WIDTH,
    PAGE_WIDTH_PT,
    SPINE_WIDTH_PT,
)
from .fonts import font_file_path, normalize_font_id
from .job_log import append_event


def _load_font(size: int, font_id: str | None = None):
    candidates = []
    primary = font_file_path(font_id)
    if primary is not None:
        candidates.append(primary)
    candidates.extend(
        [
            FONTS_DIR / "Nunito-Bold.ttf",
            FONTS_DIR / "Montserrat-Bold.ttf",
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


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
    if placement == "top":
        return int(height * 0.04)
    if placement == "upper_third":
        return int(height * 0.10)
    if placement == "middle":
        return max(int(height * 0.5 - box_height / 2), int(height * 0.08))
    if placement == "lower_third":
        return int(height * 0.62)
    return int(height * 0.72)


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
    # Truncate lines that still overflow vertically
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
    margin_x = int(image.width * 0.07)
    inner_pad_x = 40
    inner_pad_y = 36
    max_box_w = image.width - 2 * margin_x
    text_max_w = max_box_w - 2 * inner_pad_x
    max_box_h = int(image.height * 0.30)

    body = (text or "").strip()
    full_text = body
    if title:
        full_text = f"{title.strip()}\n{body}".strip() if body else title.strip()

    font, lines, block_w, block_h, line_gap, _size = _fit_text_block(
        draw, full_text, font_id, text_max_w, max_box_h - 2 * inner_pad_y
    )

    box_w = min(max_box_w, max(block_w + 2 * inner_pad_x, int(image.width * 0.55)))
    box_h = min(max_box_h, block_h + 2 * inner_pad_y)
    # Keep a readable minimum so short lines still feel like a caption panel
    box_h = max(box_h, int(image.height * 0.12))

    left = margin_x + (max_box_w - box_w) // 2
    top = _placement_top(placement, image.height, box_h)
    if top + box_h > image.height - int(image.height * 0.03):
        top = image.height - int(image.height * 0.03) - box_h
    right = left + box_w
    bottom = top + box_h

    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=42,
        fill=(255, 250, 240, 225),
    )

    text_color = (28, 38, 52, 255)
    # Vertically center the text block inside the panel
    cursor_y = top + (box_h - block_h) // 2
    for line in lines:
        w, h = _text_size(draw, line or " ", font)
        x = left + (box_w - w) // 2
        draw.text((x, cursor_y), line, font=font, fill=text_color)
        cursor_y += h + line_gap

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, format="JPEG", quality=95, optimize=True)
    return output_path


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
    back = Image.open(back_art).convert("RGB").resize(
        (PAGE_PIXEL_WIDTH, PAGE_PIXEL_HEIGHT), Image.Resampling.LANCZOS
    )
    front = Image.open(front_art).convert("RGB").resize(
        (PAGE_PIXEL_WIDTH, PAGE_PIXEL_HEIGHT), Image.Resampling.LANCZOS
    )

    front_rgba = front.convert("RGBA")
    draw = ImageDraw.Draw(front_rgba, "RGBA")
    margin_x = int(front_rgba.width * 0.07)
    max_box_w = front_rgba.width - 2 * margin_x
    text_max_w = max_box_w - 80
    max_box_h = int(front_rgba.height * 0.32)

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
    box_w = min(max_box_w, max(block_w + 80, int(front_rgba.width * 0.7)))
    box_h = min(max_box_h, block_h + 72)
    left = margin_x + (max_box_w - box_w) // 2
    top = int(front_rgba.height * 0.05)
    right = left + box_w
    bottom = top + box_h
    draw.rounded_rectangle((left, top, right, bottom), radius=44, fill=(255, 250, 240, 230))

    cursor_y = top + (box_h - block_h) // 2
    for i, line in enumerate(lines):
        color = (200, 100, 70, 255) if line.lower().startswith("starring") else (28, 38, 52, 255)
        w, h = _text_size(draw, line or " ", font)
        x = left + (box_w - w) // 2
        draw.text((x, cursor_y), line, font=font, fill=color)
        cursor_y += h + line_gap
    front = front_rgba.convert("RGB")

    spine_w = COVER_PIXEL_WIDTH - 2 * PAGE_PIXEL_WIDTH
    cover.paste(back, (0, 0))
    spine = Image.new("RGB", (max(1, spine_w), COVER_PIXEL_HEIGHT), (70, 110, 100))
    cover.paste(spine, (PAGE_PIXEL_WIDTH, 0))
    cover.paste(front, (PAGE_PIXEL_WIDTH + spine_w, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cover.save(output_path, format="JPEG", quality=95, optimize=True)
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
        f"Overlaying text ({font_id}) and building print PDF.",
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
            Image.new("RGB", (PAGE_PIXEL_WIDTH, PAGE_PIXEL_HEIGHT), (255, 255, 255)).save(
                out, format="JPEG", quality=95
            )
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

    for page_path in composed_pages:
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
        f"PDF saved ({len(composed_pages)} interior pages + wraparound cover).",
        pdf="final_book.pdf",
        spine_width_pt=SPINE_WIDTH_PT,
        font_id=font_id,
    )
    return pdf_path
