"""Story-text font catalog for PDF overlays (Magical-style choices)."""

from __future__ import annotations

from pathlib import Path

from .constants import FONTS_DIR

# id → display name, local TTF, web preview family, card tint
FONT_OPTIONS = [
    {
        "id": "storybook_classic",
        "label": "Storybook Classic",
        "file": "Merriweather-Bold.ttf",
        "web_family": '"Merriweather", Georgia, serif',
        "swatch": "#f6d7c3",
    },
    {
        "id": "smooth_sands",
        "label": "Smooth Sands",
        "file": "Nunito-Bold.ttf",
        "web_family": '"Nunito", "Segoe UI", sans-serif',
        "swatch": "#cfe8d8",
    },
    {
        "id": "bubbly_tales",
        "label": "Bubbly Tales",
        "file": "Fredoka-SemiBold.ttf",
        "web_family": '"Fredoka", "Segoe UI", sans-serif',
        "swatch": "#f5cfdc",
    },
    {
        "id": "candy_dreams",
        "label": "Candy Dreams",
        "file": "DancingScript-Bold.ttf",
        "web_family": '"Dancing Script", cursive',
        "swatch": "#e8d4f5",
    },
    {
        "id": "neon_nexus",
        "label": "Neon Nexus",
        "file": "Montserrat-Bold.ttf",
        "web_family": '"Montserrat", "Segoe UI", sans-serif',
        "swatch": "#c9dff7",
    },
    {
        "id": "cozy_curves",
        "label": "Cozy Curves",
        "file": "Quicksand-Bold.ttf",
        "web_family": '"Quicksand", "Segoe UI", sans-serif',
        "swatch": "#f8e2b8",
    },
    {
        "id": "school_script",
        "label": "School Script",
        "file": "PatrickHand-Regular.ttf",
        "web_family": '"Patrick Hand", "Comic Sans MS", cursive',
        "swatch": "#d8f0e4",
    },
    {
        "id": "adventure_book",
        "label": "Adventure Book",
        "file": "LuckiestGuy-Regular.ttf",
        "web_family": '"Luckiest Guy", Impact, sans-serif',
        "swatch": "#f5d0c0",
    },
]

DEFAULT_FONT_ID = "smooth_sands"
FONT_BY_ID = {item["id"]: item for item in FONT_OPTIONS}


def normalize_font_id(font_id: str | None) -> str:
    if font_id and font_id in FONT_BY_ID:
        return font_id
    return DEFAULT_FONT_ID


def font_file_path(font_id: str | None) -> Path | None:
    option = FONT_BY_ID.get(normalize_font_id(font_id))
    if not option:
        return None
    path = FONTS_DIR / option["file"]
    return path if path.is_file() else None


def font_label(font_id: str | None) -> str:
    return FONT_BY_ID[normalize_font_id(font_id)]["label"]
