"""v2 smoke: locked story, calm zones from docs, content personalization, app imports."""

from __future__ import annotations

from pipeline.catalogue import (
    get_title,
    list_factory_titles,
    list_titles,
    load_locked_story,
    load_story_beats,
    order_inputs_for_story,
)
from pipeline.content import _pages_from_manuscript, calm_zone_for, generate_content, personalize
from pipeline.constants import COVER_HEIGHT_MM, COVER_WIDTH_MM, PAGE_WITH_BLEED_MM, SPINE_MM, WRAP_MM


def test_tj_geometry():
    assert PAGE_WITH_BLEED_MM == 216.0
    assert SPINE_MM == 8.0
    assert WRAP_MM == 16.0
    assert COVER_WIDTH_MM == 472.0
    assert COVER_HEIGHT_MM == 248.0


def test_calm_zone_fallback_alternates_starting_right():
    # Story 01 standard starts calm on the right for spread 1.
    assert [calm_zone_for(n) for n in range(1, 5)] == ["right", "left", "right", "left"]


def test_factory_titles_only_final_locked():
    factory = list_factory_titles()
    assert len(factory) >= 1
    assert all(t["approved"] for t in factory)
    assert {t["title_id"] for t in factory} <= {t["title_id"] for t in list_titles()}
    assert "dinosaur_egg" in {t["title_id"] for t in factory}


def test_locked_dinosaur_egg_matches_story_01_standard():
    locked = load_locked_story("dinosaur_egg")
    assert locked["approved"] is True
    assert len(locked["spreads"]) == 14
    assert locked["spreads"][0]["calm_zone"] == "right"
    assert locked["spreads"][1]["calm_zone"] == "left"
    assert "[Name]" in locked["spreads"][0]["text"]
    assert "Pip" in locked["spreads"][1]["text"]
    assert locked["parent_spread"]["in_real_life"].startswith("Next time it rains")
    needed = order_inputs_for_story(locked)
    assert needed == {"carer": False, "comfort_object": False, "giver": True}
    factory = {t["title_id"]: t for t in list_factory_titles()}
    assert factory["dinosaur_egg"]["order_inputs"]["carer"] is False
    title = get_title("dinosaur_egg")
    assert title["approved"] is True
    assert "teal jumper" in title["outfit"]
    beats = load_story_beats("dinosaur_egg")
    assert len(beats["beats"]) == 14


def test_personalize_placeholders():
    inputs = {
        "name": "Emma",
        "pronouns": "she",
        "giver": "Nana",
        "slots": {},
    }
    assert personalize("[Name] finds two biscuits in [their] pocket.", inputs) == (
        "Emma finds two biscuits in her pocket."
    )
    assert personalize("For [Name], from [Giver]", inputs) == "For Emma, from Nana"


def test_generate_content_no_ai_uses_locked_text(tmp_path):
    inputs = {
        "title_id": "dinosaur_egg",
        "name": "Emma",
        "age": "4",
        "pronouns": "she",
        "giver": "Nana",
        "slots": {},
        "order_id": "RR-TEST",
    }
    data = generate_content(tmp_path, inputs)
    assert data["title"] == "Emma and the Lost Dinosaur Egg"
    assert "Emma digs up" in data["back_blurb"] or "Emma digs" in data["back_blurb"]
    s1 = data["spreads"][0]
    assert s1["calm_zone"] == "right"
    assert "Emma" in s1["text"] and "[Name]" not in s1["text"]
    assert "spade" in s1["art"].lower() or "spade" in s1["scene"].lower()
    assert data["parent_spread"]["try_together"] == '"What would you have packed for Pip?"'
    assert data["dedication"] == "For Emma, from Nana"
    pages = _pages_from_manuscript(data, inputs)
    assert len(pages) == 32
    # Text on calm side only: spread 1 calm right → page 3
    assert pages[1]["spread"] == 1 and pages[1]["side"] == "left" and pages[1]["text"] == ""
    assert pages[2]["side"] == "right" and "egg" in pages[2]["text"].lower()


def test_app_imports():
    from app import create_app

    app = create_app()
    client = app.test_client()
    page = client.get("/create")
    assert page.status_code == 200
    body = page.data
    assert b"dinosaur" in body.lower() or b"Dinosaur" in body
    assert b'name="home_type"' not in body
    assert b'name="dedication_text"' not in body
    assert b'data-order-inputs' in body
