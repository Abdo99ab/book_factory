"""TJ Books printer geometry + OpenAI model flags (v2).

``brand/chosen_printer_specs.txt`` overrides brand guidelines where they disagree.
"""

from __future__ import annotations

import os
from pathlib import Path

from .brand import SAFE_MM as BRAND_SAFE_MM

BASE_DIR = Path(__file__).resolve().parent.parent
ASSET_ROOT = BASE_DIR / "assets"
FONTS_DIR = ASSET_ROOT / "fonts"
JOB_ROOT = BASE_DIR / "book_assets" / "jobs"
ARCHIVE_ROOT = BASE_DIR / "book_assets" / "archive"

# --- TJ Books (overrides brand 4 mm bleed) -------------------------------------------------------

BLEED_MM = 3.0
TRIM_MM = 210.0
SPINE_MM = 8.0  # 32 pp @ 170 gsm only
WRAP_MM = 16.0
PAGE_WITH_BLEED_MM = TRIM_MM + 2 * BLEED_MM  # 216
COVER_PANEL_MM = PAGE_WITH_BLEED_MM  # 216
COVER_WIDTH_MM = WRAP_MM + COVER_PANEL_MM + SPINE_MM + COVER_PANEL_MM + WRAP_MM  # 472
COVER_HEIGHT_MM = WRAP_MM + PAGE_WITH_BLEED_MM + WRAP_MM  # 248

CONTENT_PAGE_COUNT = 32
SPREAD_COUNT = 14
PREVIEW_SPREAD_COUNT = 2  # spreads 1–2 after front cover
SPREAD_WIDTH_MM = 2 * TRIM_MM + 2 * BLEED_MM  # 426
SPREAD_HEIGHT_MM = PAGE_WITH_BLEED_MM

# Back QR keep-clear (Meda confirming exact; bottom-left inside the copy plate)
BACK_QR_SAFE_MM = 28.0
BACK_QR_SAFE_CORNER = "bottom_left"

SAFE_MM = BRAND_SAFE_MM

# Soft white plate under story text (not front title; not parent paper pages)
TEXT_PLATE = os.getenv("TEXT_PLATE", "always").strip().lower()
PRINTED_IN_UK_LINE = os.getenv("PRINTED_IN_UK_LINE", "0").strip() in {"1", "true", "yes"}

# TJ packaging only in v2
PRINTER_PROFILE_NAME = "tj_books"
PRINTER = {
    "packaging": "single_pdf_cover_flat_first",
    "endpapers_in_pdf": True,
    "qr_safe_mm": BACK_QR_SAFE_MM,
    "qr_corner": BACK_QR_SAFE_CORNER,
}

# --- Models --------------------------------------------------------------------------------------

TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-4.1").strip()
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1").strip()
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high").strip().lower()
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "auto").strip().lower()
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300"))
OPENAI_MAX_ATTEMPTS = int(os.getenv("OPENAI_MAX_ATTEMPTS", "3"))
PAGE_CONCURRENCY = max(1, int(os.getenv("PAGE_CONCURRENCY", "1")))
JOB_CONCURRENCY = max(1, int(os.getenv("JOB_CONCURRENCY", "1")))

PRONOUNS = {"she", "he", "they"}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MM_TO_PT = 72.0 / 25.4


def mm_to_pt(mm: float) -> float:
    return float(mm) * MM_TO_PT


def mm_to_px(mm: float, ppi: float = 300.0) -> int:
    return int(round(mm / 25.4 * ppi))
