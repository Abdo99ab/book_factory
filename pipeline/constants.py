import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ASSET_ROOT = BASE_DIR / "book_assets"
JOB_ROOT = ASSET_ROOT / "jobs"
ARCHIVE_ROOT = ASSET_ROOT / "archived_jobs"
PREFERENCES_DIR = BASE_DIR / "data" / "preferences"
FONTS_DIR = BASE_DIR / "assets" / "fonts"

TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-6-luna")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2.5-flare")
PAGE_CONCURRENCY = max(1, int(os.getenv("PAGE_CONCURRENCY", "1")))
OPENAI_TIMEOUT_SECONDS = max(1.0, float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300")))
OPENAI_MAX_ATTEMPTS = max(1, int(os.getenv("OPENAI_MAX_ATTEMPTS", "3")))

# Portrait 3:4 interior pages (OpenAI supported size)
PAGE_IMAGE_SIZE = "1024x1536"
PAGE_WIDTH_PT = 432.0  # 6 in
PAGE_HEIGHT_PT = 576.0  # 8 in (3:4)
DPI = 300
PAGE_PIXEL_WIDTH = round(PAGE_WIDTH_PT / 72 * DPI)
PAGE_PIXEL_HEIGHT = round(PAGE_HEIGHT_PT / 72 * DPI)

# Wraparound: back + spine + front (both panels portrait 3:4)
SPINE_WIDTH_PT = 36.0  # 0.5 in
COVER_WIDTH_PT = PAGE_WIDTH_PT * 2 + SPINE_WIDTH_PT
COVER_HEIGHT_PT = PAGE_HEIGHT_PT
COVER_PIXEL_WIDTH = round(COVER_WIDTH_PT / 72 * DPI)
COVER_PIXEL_HEIGHT = round(COVER_HEIGHT_PT / 72 * DPI)

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
