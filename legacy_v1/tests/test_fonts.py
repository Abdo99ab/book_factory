"""Step 2 — brand font registration."""

from __future__ import annotations

import pytest
from reportlab.pdfbase import pdfmetrics

import pipeline.fonts as fonts_mod
from pipeline.fonts import (
    REPORTLAB_NAMES,
    font_file_path,
    normalize_font_id,
)


def test_normalize_font_id_always_brand():
    assert normalize_font_id(None) == "brand"
    assert normalize_font_id("smooth_sands") == "brand"
    assert normalize_font_id("storybook_classic") == "brand"


def test_registered_font_names():
    registered = set(pdfmetrics.getRegisteredFontNames())
    for role, internal in REPORTLAB_NAMES.items():
        assert internal in registered, f"missing registration for {role}"


def test_font_file_path_body_exists():
    path = font_file_path("brand", role="body")
    assert path is not None
    assert path.name == "Nunito-Regular.ttf"
    assert path.is_file()


def test_missing_font_raises(tmp_path):
    with pytest.raises(RuntimeError, match="Missing-Regular.ttf"):
        fonts_mod.register_brand_fonts(
            fonts_dir=tmp_path,
            font_files={"body": "Missing-Regular.ttf"},
        )
