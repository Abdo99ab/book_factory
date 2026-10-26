from reportlab.pdfbase import pdfmetrics

from pipeline import fonts as fonts_mod
from pipeline.fonts import REPORTLAB_NAMES, register_brand_fonts


def test_registered_font_names():
    names = set(pdfmetrics.getRegisteredFontNames())
    for rl_name in REPORTLAB_NAMES.values():
        assert rl_name in names


def test_normalize_always_brand():
    assert fonts_mod.normalize_font_id("storybook_classic") == "brand"
    assert fonts_mod.normalize_font_id(None) == "brand"


def test_missing_font_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts_mod, "FONTS_DIR", tmp_path)
    # Force brand_font_path to look in empty dir
    from pipeline import brand as brand_mod

    monkeypatch.setattr(fonts_mod, "FONT_FILES", brand_mod.FONT_FILES)
    try:
        fonts_mod.brand_font_path("title")
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "NotoSerif-SemiBold.ttf" in str(exc)
    assert raised
