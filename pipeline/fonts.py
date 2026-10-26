"""Raised Ready brand fonts — ReportLab registration only (no Magical catalogue)."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .brand import FONT_FILES
from .constants import FONTS_DIR

# ReportLab internal names (stable across PDF builds)
REPORTLAB_NAMES = {
    "title": "RR-Title",
    "body": "RR-Body",
    "body_bold": "RR-Body-Bold",
    "label": "RR-Label",
}

DEFAULT_FONT_ID = "brand"
BRAND_FONT_ID = "brand"


def normalize_font_id(font_id: str | None) -> str:
    """All jobs use the locked Raised Ready font stack."""
    return BRAND_FONT_ID


def font_label(font_id: str | None = None) -> str:
    return "Raised Ready brand fonts"


def font_file_path(font_id: str | None = None, role: str = "body") -> Path | None:
    """Path to a brand TTF; default role is body (story overlays)."""
    filename = FONT_FILES.get(role) or FONT_FILES["body"]
    path = FONTS_DIR / filename
    return path if path.is_file() else None


def reportlab_name(role: str) -> str:
    return REPORTLAB_NAMES[role]


def register_brand_fonts(
    fonts_dir: Path | None = None,
    font_files: dict[str, str] | None = None,
) -> None:
    root = fonts_dir or FONTS_DIR
    files = font_files or FONT_FILES
    missing = []
    for role, filename in files.items():
        path = root / filename
        if not path.is_file():
            missing.append(filename)
            continue
        internal = REPORTLAB_NAMES[role]
        if internal not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(internal, str(path)))
    if missing:
        raise RuntimeError(
            "Missing Raised Ready brand font file(s) under assets/fonts/: "
            + ", ".join(sorted(missing))
        )


register_brand_fonts()
