"""Raised Ready brand constants (LOCKED / OPEN values from the spec).

Cite: docs/RAISED-READY-BOOK-FACTORY-SPEC.md
"""

from __future__ import annotations

import os
from pathlib import Path

# Resolved locally (not imported from constants) so constants.py can read SAFE_MM from here
# without a circular import.
BASE_DIR = Path(__file__).resolve().parent.parent

BRAND_DIR = BASE_DIR / "brand"
ASSETS_BRAND = BASE_DIR / "assets" / "brand"

# Spec §6 — Colour (LOCKED 22 Oct)
COLOURS = {
    "ink_navy": {"hex": "#22324A", "rgb": (34, 50, 74)},
    "oat": {"hex": "#F3EBDD", "rgb": (243, 235, 221)},
    "paper": {"hex": "#FAF6EF", "rgb": (250, 246, 239)},
    "white": {"hex": "#FFFFFF", "rgb": (255, 255, 255)},
    "rust": {"hex": "#A9502F", "rgb": (169, 80, 47)},  # Next steps
    "teal": {"hex": "#0F5257", "rgb": (15, 82, 87)},  # Adventures
    "sage": {"hex": "#8FA58B", "rgb": (143, 165, 139)},  # Growing skills
    "butter": {"hex": "#F2C94C", "rgb": (242, 201, 76)},  # Tomorrow's world
}

# Spec §4 — band text colour by family
BAND_TEXT_COLOUR = {
    "next_steps": "white",  # white on rust
    "adventures": "white",  # white on teal (oat allowed)
    "growing_skills": "ink_navy",  # ink navy on sage
    "tomorrows_world": "ink_navy",  # ink navy on butter
}

FAMILY_COLOUR = {
    "next_steps": "rust",
    "adventures": "teal",
    "growing_skills": "sage",
    "tomorrows_world": "butter",
}

# Spec §5 — font files (filenames under assets/fonts/)
# ``inworld`` = Caveat (OFL): ONLY for in-story-world lettering set in code over the art
# (Ocean Map banner name). Never for story text, titles, bands, covers, or ads.
FONT_FILES = {
    "title": "NotoSerif-SemiBold.ttf",
    "body": "Nunito-Regular.ttf",
    "body_bold": "Nunito-SemiBold.ttf",
    "label": "DMMono-Medium.ttf",
    "inworld": "Caveat/static/Caveat-Regular.ttf",
}

# Spec §5 — size scale (pt)
SIZE_SCALE = {
    "front_title_rest": (44, 52),
    "front_title_and_the": (22, 26),
    "front_band": 10,
    "band_tracking_pct": 12,
    "band_depth_mm": 13,
    "back_question": (22, 24),
    "back_blurb": (12, 16),
    "spine_title": 8,
    "mini_r_mm": (4, 5),
    "morning_light_story": (22, 24),
    "morning_light_leading": 1.35,
    "field_guide_story": (16, 18),
    "field_guide_leading": 1.4,
    "parent_heading": 15,
    "parent_body": 11.5,
    "parent_label": 9,
    "parent_spot_mm": 88,
    "page_furniture": 9,
    "dedication": 18,
    "order_code": 9,
    "illustrated_from": 12,
}

# Spec §7 — logo masters under assets/brand/ (B1b Round 5 lockup + symbol)
LOGO_FILES = {
    "main": ASSETS_BRAND / "B1b_colour.png",
    "full_colour": ASSETS_BRAND / "B1b_colour.png",
    "one_ink": ASSETS_BRAND / "B1b_one_ink.png",
    "mini_r": ASSETS_BRAND / "B1b_symbol_colour.png",
    "reverse": ASSETS_BRAND / "B1b_reverse.png",
}

# Spec §9 — fixed line, identical in every title
READ_IT_AGAIN_LINE = (
    "Read this story a few nights in a row, and once more just before the day itself."
)

# Spec §4 / §1 — AI honesty line
HONESTY_LINE = (
    "Illustrated from {name}'s photo and checked by a person before print."
)

# Spec §5 — UK English (US → UK)
UK_SPELLING = {
    "color": "colour",
    "colors": "colours",
    "colored": "coloured",
    "personalize": "personalise",
    "personalized": "personalised",
    "diaper": "nappy",
    "diapers": "nappies",
    "mom": "mum",
    "moms": "mums",
    "pacifier": "dummy",
    "candy": "sweets",
    "sidewalk": "pavement",
    "flashlight": "torch",
    "preschool": "nursery",
    "kindergarten": "reception",
    # Do NOT map pants→trousers: Raised Ready UK uses "pants" for underwear (spec §5).
    "practice": "practise",  # verb; noun stays practice — guard uses word lists carefully
}

# Spec §11.5 — banned phrases (case-insensitive, word-boundary)
BANNED_PHRASES = [
    "fix",
    "fixes",
    "cure",
    "cures",
    "stops your",
    "toilet-trained",
    "toilet trained",
    "by month",
    "in 3 days",
    "dry by",
    "behind",
    "delayed",
    "struggling",
    "meltdown",
    "naughty",
    "magical",
    "little did",
    "suddenly",
    "couldn't help but",
    "could not help but",
    "journey",
    "no more nappies",
    "ready for september",
    "sleeps through the night",
    "stays in bed",
    "no more tears",
    "ends separation anxiety",
    "stops hitting",
    "cures anxiety",
    "no more worries",
    "stress-free",
    "stress free",
    "protects your child from misinformation",
    "printed in the uk",
    "starring",
    "hand-painted",
    "hand painted",
]

# Spec §11.4 — tells to cut (humanize / voice)
TELLS = [
    "It was a big day",
    "she felt so proud",
    "he felt so proud",
    "little did she know",
    "little did he know",
    "suddenly",
    "a wave of",
    "couldn't help but",
    "with a twinkle",
    "magical",
    "journey",
    "adventure awaits",
    "brave little",
    "special",
]

# Spec §11.2 — words per spread (min, max); cap = upper bound
WORDS_PER_SPREAD = {
    "2-3.5": (20, 45),
    "3-6": (30, 50),
    "5-8": (50, 70),
    "7-10": (70, 90),
}

# Humanize-spec tighter caps (OPEN: Taoufik picks via WORD_CAP_SOURCE)
HUMANIZE_CAPS = {
    "2-5": 40,
    "5-10": 70,
}

# OPEN, owner Taoufik — brand table vs humanize-spec caps
WORD_CAP_SOURCE = os.getenv("WORD_CAP_SOURCE", "brand").strip().lower()

# Spec §3 — safe zones (mm)
SAFE_MM = {"trim": 10, "gutter": 15}

# Spec §8.1–8.2 — illustration route presets (STYLE ONLY — no locations/rooms/props/weather).
# Place and props come exclusively from each story JSON ``setting`` / ``art`` fields.
ROUTE_PRESETS = {
    "morning_light": {
        "label": "Morning Light",
        "age_bands": ("2-3.5", "3-6"),
        "preamble": (
            "Children's picture-book illustration, matte gouache on textured paper, "
            "visible brush strokes, soft edges, warm soft daylight, muted palette of "
            "oat cream, warm clay, sage green, butter yellow and ink navy, "
            "drawn from a small child's eye level, large simple shapes, calm uncluttered "
            "composition with a quiet empty area reserved for text, no text, no letters, "
            "no logos, no glossy 3D rendering, no plastic skin."
        ),
        "character_sheet": (
            "Reference character sheet in Morning Light style: front, three-quarter and "
            "profile views of the same child, full body, neutral pose, everyday outfit, "
            "anchor object beside the child, soft gouache, warm daylight."
        ),
        "face_sheet": (
            "Reference face sheet in Morning Light style: large close-up head-and-shoulders "
            "portraits of the same child, front view and three-quarter view, faces filling "
            "most of the frame, plain light background, soft gouache, warm daylight. "
            "Show only the book outfit neckline — never the clothes from the photo."
        ),
        "story_pt": 23,
        "leading": 1.35,
        "negatives": (
            "no glossy 3D, no plastic skin, no toy-shop saturation, no text in art"
        ),
    },
    "field_guide": {
        "label": "Field Guide",
        "age_bands": ("5-8", "7-10"),
        "preamble": (
            "Children's book illustration in fine charcoal ink line with translucent "
            "watercolour wash on white paper, slightly wide observational viewpoint, "
            "older child proportions, small hand-drawn labels, arrows and map details as "
            "decoration only, accents of deep teal, sage and butter yellow, calm "
            "composition with an open area reserved for text, no readable text, no logos, "
            "no anime styling, no glossy colour, no clutter."
        ),
        "character_sheet": (
            "Reference character sheet in Field Guide style: front, three-quarter and "
            "profile views of the same child, full body, observed proportions, everyday "
            "outfit, anchor object beside the child, ink line and watercolour wash."
        ),
        "face_sheet": (
            "Reference face sheet in Field Guide style: large close-up head-and-shoulders "
            "portraits of the same child, front view and three-quarter view, faces filling "
            "most of the frame, plain light background, ink line and watercolour wash. "
            "Show only the book outfit neckline — never the clothes from the photo."
        ),
        "story_pt": 17,
        "leading": 1.4,
        "negatives": (
            "no anime styling, no glossy colour, no cluttered pages, no text in art"
        ),
    },
}

# Spec §8.2 shared negatives (appended to every image prompt)
SHARED_NEGATIVES = (
    "no text, letters, numbers or logos in the art; no helmet visor or mask covering "
    "the face; no unhappy carer face at an accident; no punishment; no fear monsters; "
    "screens show nothing readable; no child using an AI tool; no musical instruments "
    "(guitar, piano, drums, violin) unless the beat names them; no invented toys, pets "
    "or gadgets the beat does not name"
)


def word_cap_for_age_band(age_band: str) -> int:
    """Return the per-spread word cap for an age band string."""
    if WORD_CAP_SOURCE == "humanize":
        if age_band in {"2-3.5", "3-6", "2-5"}:
            return HUMANIZE_CAPS["2-5"]
        return HUMANIZE_CAPS["5-10"]
    # brand: upper bound of WORDS_PER_SPREAD
    key = age_band if age_band in WORDS_PER_SPREAD else _nearest_band(age_band)
    return WORDS_PER_SPREAD[key][1]


def _nearest_band(age_band: str) -> str:
    return "3-6" if age_band.startswith(("2", "3")) else "5-8"


def default_route_for_age_band(age_band: str) -> str:
    """When catalogue route is 'open', default by age band and warn at call site."""
    if age_band in {"2-3.5", "3-6"}:
        return "morning_light"
    return "field_guide"
