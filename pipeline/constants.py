import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ASSET_ROOT = BASE_DIR / "book_assets"
JOB_ROOT = ASSET_ROOT / "jobs"
ARCHIVE_ROOT = ASSET_ROOT / "archived_jobs"
TEMPLATE_ROOT = ASSET_ROOT / "templates"
PREFERENCES_DIR = BASE_DIR / "data" / "preferences"
FONTS_DIR = BASE_DIR / "assets" / "fonts"

TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-6-luna")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2.5-flare")
PAGE_CONCURRENCY = max(1, int(os.getenv("PAGE_CONCURRENCY", "1")))
OPENAI_TIMEOUT_SECONDS = max(1.0, float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300")))
OPENAI_MAX_ATTEMPTS = max(1, int(os.getenv("OPENAI_MAX_ATTEMPTS", "3")))

DPI = 300


def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def mm_to_px(mm: float, dpi: int = DPI) -> int:
    return round(mm / 25.4 * dpi)


# --- TJ print template (170 gsm, 32 content pages) ---
# Inner pages: 210 × 210 mm trim + 3 mm bleed → 216 × 216 mm art/PDF page.
# Cover flat: 16 wrap + 216 back + 8 spine + 216 front + 16 wrap; height 16+216+16.
# Spine 8 mm is valid only for 32 pages on 170 gsm. Endpapers are plain and not
# counted in the 32. PDF: single pages, no colour profile.
CONTENT_PAGE_COUNT = 32
PAPER_GSM = 170

TRIM_MM = 210.0
BLEED_MM = 3.0
PAGE_WITH_BLEED_MM = TRIM_MM + 2 * BLEED_MM  # 216

COVER_PANEL_MM = 216.0
SPINE_MM = 8.0
WRAP_MM = 16.0
COVER_WIDTH_MM = WRAP_MM + COVER_PANEL_MM + SPINE_MM + COVER_PANEL_MM + WRAP_MM  # 472
COVER_HEIGHT_MM = WRAP_MM + COVER_PANEL_MM + WRAP_MM  # 248

# Temporary QR/batch safe zone on back cover (Meda confirming exact corner/size).
# Bottom-right of the finished back cover = near-spine bottom of the back panel.
BACK_QR_SAFE_MM = 28.0
BACK_QR_SAFE_CORNER = "bottom_right"

# Square OpenAI size (print art is upscaled to bleed pixels at 300 dpi).
PAGE_IMAGE_SIZE = "1024x1024"

PAGE_WIDTH_PT = mm_to_pt(PAGE_WITH_BLEED_MM)
PAGE_HEIGHT_PT = mm_to_pt(PAGE_WITH_BLEED_MM)
PAGE_PIXEL_WIDTH = mm_to_px(PAGE_WITH_BLEED_MM)
PAGE_PIXEL_HEIGHT = mm_to_px(PAGE_WITH_BLEED_MM)

WRAP_PX = mm_to_px(WRAP_MM)
COVER_PANEL_PX = mm_to_px(COVER_PANEL_MM)
SPINE_PX = mm_to_px(SPINE_MM)
BACK_QR_SAFE_PX = mm_to_px(BACK_QR_SAFE_MM)
BLEED_PX = mm_to_px(BLEED_MM)

# Derive cover pixels from parts so rounding cannot leave a 1px gap/overlap.
COVER_PIXEL_WIDTH = WRAP_PX + COVER_PANEL_PX + SPINE_PX + COVER_PANEL_PX + WRAP_PX
COVER_PIXEL_HEIGHT = WRAP_PX + COVER_PANEL_PX + WRAP_PX
SPINE_WIDTH_PT = mm_to_pt(SPINE_MM)
COVER_WIDTH_PT = mm_to_pt(COVER_WIDTH_MM)
COVER_HEIGHT_PT = mm_to_pt(COVER_HEIGHT_MM)

THEMES = [
    "Adventure",
    "Fairy Tales",
    "Activities",
    "Worlds",
    "Holidays",
    "Family",
    "Educational",
    "Feelings",
]

PURPOSES = [
    "educational",
    "entertaining",
    "both",
]

GENDER_DETAILS = {
    "male": {
        "character_type": "boy",
        "subject_pronoun": "he",
        "subject_pronoun_cap": "He",
        "possessive_pronoun": "his",
        "object_pronoun": "him",
        "possessive_pronoun_cap": "His",
        "reflexive_pronoun": "himself",
    },
    "female": {
        "character_type": "girl",
        "subject_pronoun": "she",
        "subject_pronoun_cap": "She",
        "possessive_pronoun": "her",
        "object_pronoun": "her",
        "possessive_pronoun_cap": "Her",
        "reflexive_pronoun": "herself",
    },
}

CHARACTER_PROMPT = """Create a children's picture-book character based on this reference photo.
Treat the supplied reference image as the single source of truth for the child's identity.
Preserve the child's exact recognizable face, skin tone, eye color and shape, hair color,
hair texture, hairstyle, hairline, eyebrows, nose, mouth, ears, age, body proportions,
and distinctive features. Do not replace the child with a generic child.

Show the {character_type} full-body, front-facing, with clear facial features, hairstyle,
clothing, shoes and proportions. Keep the character suitable as a locked reference for
future illustrations. The character is {age} years old — use that for proportions only;
do not write the age anywhere in the image.

Art direction:
{design_rules}

{preference_memory}

NO TEXT, letters, watermarks, or logos in the image."""

NO_TEXT_RULE = """CRITICAL: Do not include any text, letters, numbers, words, captions, titles,
speech bubbles, signs with readable writing, watermarks, or logos in the image.
Story text will be overlaid later in production."""
