"""Raised Ready brand fonts — Noto Serif, Nunito, DM Mono only (spec §5)."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .brand import FONT_FILES
from .constants import FONTS_DIR

# ReportLab registration names (must match pdf_build usage)
REPORTLAB_NAMES = {
    "title": "NotoSerif-SemiBold",
    "body": "Nunito-Regular",
    "body_bold": "Nunito-SemiBold",
    "label": "DMMono-Medium",
}

DEFAULT_FONT_ID = "brand"
FONT_OPTIONS = [
    {
        "id": "brand",
        "label": "Raised Ready (brand)",
        "file": FONT_FILES["body"],
        "web_family": '"Nunito", "Segoe UI", sans-serif',
        "swatch": "#F3EBDD",
    }
]
FONT_BY_ID = {item["id"]: item for item in FONT_OPTIONS}

_REGISTERED = False


def normalize_font_id(font_id: str | None) -> str:
    """Backward compatible; always brand."""
    return "brand"


def font_file_path(font_id: str | None = None) -> Path | None:
    path = FONTS_DIR / FONT_FILES["body"]
    return path if path.is_file() else None


def font_label(font_id: str | None = None) -> str:
    return "Raised Ready"


def brand_font_path(role: str) -> Path:
    filename = FONT_FILES[role]
    path = FONTS_DIR / filename
    if not path.is_file():
        raise RuntimeError(
            f"Missing brand font file for role={role}: {path}. "
            f"Install {filename} into assets/fonts/."
        )
    return path


def register_brand_fonts() -> list[str]:
    """Register all four brand TTFs with ReportLab. Raises if any file is missing."""
    global _REGISTERED
    names = []
    for role, rl_name in REPORTLAB_NAMES.items():
        path = brand_font_path(role)
        if rl_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(rl_name, str(path)))
        names.append(rl_name)
    _REGISTERED = True
    return names


# Register at import — fail loudly if fonts are absent.
register_brand_fonts()
