"""Runtime constants and printer profiles for Raised Ready Book Factory."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ASSET_ROOT = BASE_DIR / "book_assets"
JOB_ROOT = ASSET_ROOT / "jobs"
ARCHIVE_ROOT = ASSET_ROOT / "archived_jobs"
TEMPLATE_ROOT = ASSET_ROOT / "templates"
PREFERENCES_DIR = BASE_DIR / "data" / "preferences"
FONTS_DIR = BASE_DIR / "assets" / "fonts"
BRAND_DIR = BASE_DIR / "brand"

TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-6-luna")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2.5-flare")
PAGE_CONCURRENCY = max(1, int(os.getenv("PAGE_CONCURRENCY", "1")))
OPENAI_TIMEOUT_SECONDS = max(1.0, float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300")))
OPENAI_MAX_ATTEMPTS = max(1, int(os.getenv("OPENAI_MAX_ATTEMPTS", "3")))

DPI = 300
CONTENT_PAGE_COUNT = 32  # LOCKED page map (§3)
SPREAD_COUNT = 14


def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def mm_to_px(mm: float, dpi: int = DPI) -> int:
    return round(mm / 25.4 * dpi)


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


# --- Feature flags (OPEN / RECOMMENDED — owners in comments) ---
PRINTED_IN_UK_LINE = _env_bool("PRINTED_IN_UK_LINE", "0")  # OPEN: after UK printer contract
TEXT_PLATE = os.getenv("TEXT_PLATE", "auto").strip().lower()  # RECOMMENDED auto; Taoufik
OUTFIT_CONSTANT = _env_bool("OUTFIT_CONSTANT", "1")  # RECOMMENDED constant outfit
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high").strip().lower()
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "auto").strip().lower()
# RECOMMENDED off — learned prefs must not override brand (Taoufik confirms)
MEMORY_SCOPE = os.getenv("MEMORY_SCOPE", "off").strip().lower()
# OPEN after printed proof — no upscaler implementation in this pass
UPSCALER = os.getenv("UPSCALER", "none").strip().lower()
HUMANIZE_PASS = _env_bool("HUMANIZE_PASS", "1")  # keep existing humanize on by default
HUMANIZE_STORY = os.getenv("HUMANIZE_STORY", "1")  # alias used by humanize.py

# --- Printer profiles ---
# bleed_mm 3.0 LOCKED by TJ template 26 Oct (brand doc said 4; TJ form wins for now).
PRINTER_PROFILES = {
    "tj_books": {
        "bleed_mm": 3.0,  # LOCKED by TJ template 26 Oct
        "spine_mm": 8.0,  # WORKING: 32 pp on 170 gsm
        "wrap_mm": 16.0,
        "paper_gsm": 170,
        "trim_mm": 210.0,
        "endpapers_in_pdf": True,  # WORKING — Meda / TJ confirm
        "colour_profile": None,  # no ICC for now
        "packaging": "single_pdf_cover_flat_first",  # as today
        "qr_safe_mm": 28.0,  # WORKING — Meda confirms corner/size
        "qr_corner": "bottom_right",  # WORKING
        "barcode_required": False,
        "pdf_standard": None,
    },
    "prodigi": {
        "bleed_mm": 3.0,  # WORKING
        "spine_mm": 8.0,
        "wrap_mm": 16.0,
        "paper_gsm": 170,
        "trim_mm": 210.0,
        "endpapers_in_pdf": False,
        "colour_profile": None,
        "packaging": "single_pdf_cover_first_back_last",
        "qr_safe_mm": 28.0,
        "qr_corner": "bottom_right",
        "barcode_required": False,
        "pdf_standard": None,
    },
}

PRINTER_PROFILE_NAME = os.getenv("PRINTER_PROFILE", "tj_books").strip().lower()
if PRINTER_PROFILE_NAME not in PRINTER_PROFILES:
    raise RuntimeError(f"Unknown PRINTER_PROFILE={PRINTER_PROFILE_NAME}")

PRINTER = PRINTER_PROFILES[PRINTER_PROFILE_NAME]

TRIM_MM = float(PRINTER["trim_mm"])
BLEED_MM = float(PRINTER["bleed_mm"])
PAGE_WITH_BLEED_MM = TRIM_MM + 2 * BLEED_MM  # 216 under tj_books
SPINE_MM = float(PRINTER["spine_mm"])
WRAP_MM = float(PRINTER["wrap_mm"])
COVER_PANEL_MM = PAGE_WITH_BLEED_MM
COVER_WIDTH_MM = WRAP_MM + COVER_PANEL_MM + SPINE_MM + COVER_PANEL_MM + WRAP_MM  # 472
COVER_HEIGHT_MM = WRAP_MM + COVER_PANEL_MM + WRAP_MM  # 248
PAPER_GSM = int(PRINTER["paper_gsm"])
BACK_QR_SAFE_MM = float(PRINTER["qr_safe_mm"])
BACK_QR_SAFE_CORNER = PRINTER["qr_corner"]

PAGE_PX = mm_to_px(PAGE_WITH_BLEED_MM)  # 2551 under tj_books
PAGE_PIXEL_WIDTH = PAGE_PX
PAGE_PIXEL_HEIGHT = PAGE_PX
# Spread art is two page images; recorded width if ever composited:
SPREAD_PX_W = PAGE_PX * 2
SPREAD_PX_H = PAGE_PX

PAGE_WIDTH_PT = mm_to_pt(PAGE_WITH_BLEED_MM)
PAGE_HEIGHT_PT = mm_to_pt(PAGE_WITH_BLEED_MM)
WRAP_PX = mm_to_px(WRAP_MM)
COVER_PANEL_PX = mm_to_px(COVER_PANEL_MM)
SPINE_PX = mm_to_px(SPINE_MM)
BACK_QR_SAFE_PX = mm_to_px(BACK_QR_SAFE_MM)
BLEED_PX = mm_to_px(BLEED_MM)
COVER_PIXEL_WIDTH = WRAP_PX + COVER_PANEL_PX + SPINE_PX + COVER_PANEL_PX + WRAP_PX
COVER_PIXEL_HEIGHT = WRAP_PX + COVER_PANEL_PX + WRAP_PX
SPINE_WIDTH_PT = mm_to_pt(SPINE_MM)
COVER_WIDTH_PT = mm_to_pt(COVER_WIDTH_MM)
COVER_HEIGHT_PT = mm_to_pt(COVER_HEIGHT_MM)

# Default image size hint; openai_util.largest_image_size may override when IMAGE_SIZE=auto
PAGE_IMAGE_SIZE = os.getenv("PAGE_IMAGE_SIZE", "1024x1024")

# Spec §3 — pronouns (she / he / they)
PRONOUNS = {
    "she": {
        "key": "she",
        "character_type": "girl",
        "subject_pronoun": "she",
        "subject_pronoun_cap": "She",
        "possessive_pronoun": "her",
        "object_pronoun": "her",
        "possessive_pronoun_cap": "Her",
        "reflexive_pronoun": "herself",
    },
    "he": {
        "key": "he",
        "character_type": "boy",
        "subject_pronoun": "he",
        "subject_pronoun_cap": "He",
        "possessive_pronoun": "his",
        "object_pronoun": "him",
        "possessive_pronoun_cap": "His",
        "reflexive_pronoun": "himself",
    },
    "they": {
        "key": "they",
        "character_type": "child",
        "subject_pronoun": "they",
        "subject_pronoun_cap": "They",
        "possessive_pronoun": "their",
        "object_pronoun": "them",
        "possessive_pronoun_cap": "Their",
        "reflexive_pronoun": "themself",
    },
}

# Backward-compat aliases while modules migrate
GENDER_DETAILS = {
    "female": PRONOUNS["she"],
    "male": PRONOUNS["he"],
    "they": PRONOUNS["they"],
}


def pronouns_for(value: str) -> dict:
    key = (value or "").strip().lower()
    if key in PRONOUNS:
        return PRONOUNS[key]
    if key in GENDER_DETAILS:
        return GENDER_DETAILS[key]
    raise KeyError(f"Unknown pronouns: {value}")


# Identity paragraph kept for character sheets (rewired in Step 4)
CHARACTER_IDENTITY = """Treat the supplied reference image as the single source of truth for the child's identity.
Preserve the child's exact recognizable face, skin tone, eye colour and shape, hair colour,
hair texture, hairstyle, hairline, eyebrows, nose, mouth, ears, age, body proportions,
and distinctive features. Do not replace the child with a generic child."""

# Temporary Magical-era prompt; Step 4 replaces with route presets + catalogue outfit.
CHARACTER_PROMPT = (
    "Create a children's picture-book character based on this reference photo.\n"
    + CHARACTER_IDENTITY
    + """

Show the {character_type} full-body, front-facing, with clear facial features, hairstyle,
clothing, shoes and proportions. Keep the character suitable as a locked reference for
future illustrations. The character is {age} years old — use that for proportions only;
do not write the age anywhere in the image.

Art direction:
{design_rules}

{preference_memory}

NO TEXT, letters, watermarks, or logos in the image."""
)

NO_TEXT_RULE = """CRITICAL: Do not include any text, letters, numbers, words, captions, titles,
speech bubbles, signs with readable writing, watermarks, or logos in the image.
Story text will be overlaid later in production."""

# Magical THEMES/PURPOSES removed — catalogue titles replace them (Step 3).
# Empty shims so older imports do not crash mid-migration.
THEMES: list[str] = []
PURPOSES: list[str] = []
