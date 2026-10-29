"""B1b logos, Caveat in-world font, Ocean Map lettering metadata."""

from __future__ import annotations

from reportlab.pdfbase import pdfmetrics

from pipeline.brand import FONT_FILES, LOGO_FILES
from pipeline.catalogue import load_locked_story
from pipeline.fonts import register_brand_fonts, reportlab_name


def test_b1b_logo_files_exist():
    for key in ("main", "full_colour", "one_ink", "mini_r", "reverse"):
        path = LOGO_FILES[key]
        assert path.is_file(), f"missing logo {key}: {path}"
    assert LOGO_FILES["main"].name == "B1b_colour.png"
    assert LOGO_FILES["one_ink"].name == "B1b_one_ink.png"
    assert LOGO_FILES["mini_r"].name == "B1b_symbol_colour.png"


def test_caveat_registered_as_inworld_only():
    register_brand_fonts()
    assert FONT_FILES["inworld"].endswith("Caveat-Regular.ttf")
    assert reportlab_name("inworld") == "RR-Inworld"
    assert "RR-Inworld" in pdfmetrics.getRegisteredFontNames()
    # Body / title stack unchanged
    assert reportlab_name("body") == "RR-Body"
    assert reportlab_name("title") == "RR-Title"


def test_ocean_map_inworld_lettering_meta():
    locked = load_locked_story("ocean_map")
    specs = locked.get("inworld_lettering") or []
    assert len(specs) == 1
    spec = specs[0]
    assert spec["spread"] == 1
    assert spec["kind"] == "map_banner_name"
    assert spec["font"] == "inworld"
    assert spec["fallback_font"] == "body"
    assert spec.get("side") == "right"
    # Banner art must leave letters to code
    art = locked["spreads"][0]["art"].lower()
    assert "blank banner" in art or "no letters" in art


def test_all_ten_locked_stories_load():
    from pipeline.catalogue import load_catalogue

    cat = load_catalogue()
    for tid in cat["order"]:
        locked = load_locked_story(tid)
        assert locked["approved"] is True
        assert len(locked["spreads"]) == 14
        assert cat["titles"][tid]["approved"] is True
