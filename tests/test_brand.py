from pipeline.brand import BAND_TEXT_COLOUR, COLOURS, FAMILY_COLOUR
from pipeline.catalogue import load_catalogue


def test_colour_hex_list():
    expected = {
        "ink_navy": "#22324A",
        "oat": "#F3EBDD",
        "paper": "#FAF6EF",
        "white": "#FFFFFF",
        "rust": "#A9502F",
        "teal": "#0F5257",
        "sage": "#8FA58B",
        "butter": "#F2C94C",
    }
    for name, hex_value in expected.items():
        assert COLOURS[name]["hex"].upper() == hex_value.upper()


def test_band_text_colours():
    assert BAND_TEXT_COLOUR["next_steps"] == "white"
    assert BAND_TEXT_COLOUR["adventures"] == "white"
    assert BAND_TEXT_COLOUR["growing_skills"] == "ink_navy"
    assert BAND_TEXT_COLOUR["tomorrows_world"] == "ink_navy"


def test_family_colours_match_catalogue():
    data = load_catalogue()
    for entry in data["titles"].values():
        assert FAMILY_COLOUR[entry["family"]] == entry["colour"]
