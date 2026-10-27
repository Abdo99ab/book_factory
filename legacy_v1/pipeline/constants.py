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
TEXT_PLATE = os.getenv("TEXT_PLATE", "always").strip().lower()  # always white plate under story/cover text
OUTFIT_CONSTANT = _env_bool("OUTFIT_CONSTANT", "1")  # RECOMMENDED constant outfit
# Read from the environment (openai_util.image_quality re-reads IMAGE_QUALITY at call time); "high" is
# only the default when the variable is unset.
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high").strip().lower()
# auto = the largest LANDSCAPE size the image model offers (spread art; openai_util.largest_image_size).
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
        "qr_corner": "bottom_left",  # inside the back-cover white plate
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
        "qr_corner": "bottom_left",
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

# --- True spreads: ONE wide picture per spread, split into two pages at PDF time -----------------
# The interior art is a single continuous double-page image (spreads/spread-NN/art.png). It is NOT
# generated as two pages. The PDF builder (and the operator UI) cut it into a left and a right page
# with pages.split_spread().
#
# Geometry (all from the active printer profile; tj_books: trim 210, bleed 3):
#   spread width  = 2 x trim + 2 x bleed = 426 mm   (outer bleed 3 + page 210 + page 210 + outer bleed 3)
#   spread height = trim + 2 x bleed     = 216 mm
#   fold          = the centre line, x = 213 mm
#
# SPLIT RULE. Every printed page is a 216 mm box (trim + both bleeds):
#   left  page = spread x in [0, 216] mm    = 3 mm outer bleed + 210 mm trim + 3 mm taken from the right
#                                             half as INNER bleed
#   right page = spread x in [210, 426] mm  = 3 mm taken from the left half as INNER bleed + 210 mm
#                                             trim + 3 mm outer bleed
# The two pages therefore OVERLAP by 6 mm (x in [210, 216]). That 6 mm is exactly what binding and
# trimming consume at the fold, so a continuous picture still reads as one scene across the page turn.
SPREAD_WIDTH_MM = 2 * TRIM_MM + 2 * BLEED_MM  # 426 under tj_books
SPREAD_HEIGHT_MM = PAGE_WITH_BLEED_MM  # 216 under tj_books
SPREAD_ASPECT = SPREAD_WIDTH_MM / SPREAD_HEIGHT_MM  # ~1.972
FOLD_X_MM = SPREAD_WIDTH_MM / 2  # 213 under tj_books
SPREAD_PX_W = mm_to_px(SPREAD_WIDTH_MM)  # print-native width of a spread at 300 ppi
SPREAD_PX_H = mm_to_px(SPREAD_HEIGHT_MM)

from .brand import SAFE_MM as _BRAND_SAFE_MM  # noqa: E402  (brand does not import constants)

GUTTER_BAND_MM = float(_BRAND_SAFE_MM["gutter"])  # 15 mm each side of the fold: no faces / hands / props
# Nothing important within the brand trim-safe distance of an outer edge; the outer bleed is already
# part of the image, so measured from the image edge it is bleed + trim-safe (13 mm under tj_books).
EDGE_BAND_MM = BLEED_MM + float(_BRAND_SAFE_MM["trim"])
# The image model's landscape frame is taller than SPREAD_ASPECT, so the top and bottom are cropped
# (pages.fit_to_spread). Heads / the anchor object stay out of these bands (fraction of the height).
CROP_BAND_FRACTION = 0.12

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

# --- Print resolution (no upscaler): native model pixels placed in the page / spread box ---
PPI_WARN = 300  # below this: print_resolution_warning
PPI_FAIL = 150  # below this: print_resolution_fail when ENFORCE_PRINT_PPI is on
# WORKING: off while proving the image path; set ENFORCE_PRINT_PPI=1 to refuse <150 ppi again.
ENFORCE_PRINT_PPI = os.getenv("ENFORCE_PRINT_PPI", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def effective_ppi(pixel_width: int, box_mm: float | None = None) -> float:
    """Pixels per inch when ``pixel_width`` px fills a box ``box_mm`` wide (default: page + bleed)."""
    box = PAGE_WITH_BLEED_MM if box_mm is None else box_mm
    return round(pixel_width / (box / 25.4), 1)


def resolution_status(ppi: float) -> str:
    if ppi < PPI_FAIL:
        return "fail"
    if ppi < PPI_WARN:
        return "warning"
    return "ok"

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
