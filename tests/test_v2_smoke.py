"""v2 smoke: catalogue, calm zones, content page map, app imports."""

from __future__ import annotations

from pipeline.catalogue import get_title, list_titles, load_story_beats
from pipeline.content import _pages_from_manuscript, calm_zone_for
from pipeline.constants import COVER_HEIGHT_MM, COVER_WIDTH_MM, PAGE_WITH_BLEED_MM, SPINE_MM, WRAP_MM


def test_tj_geometry():
    assert PAGE_WITH_BLEED_MM == 216.0
    assert SPINE_MM == 8.0
    assert WRAP_MM == 16.0
    assert COVER_WIDTH_MM == 472.0
    assert COVER_HEIGHT_MM == 248.0


def test_calm_zone_alternates():
    assert [calm_zone_for(n) for n in range(1, 5)] == ["left", "right", "left", "right"]


def test_catalogue_and_beats():
    titles = list_titles()
    assert len(titles) == 10
    beats = load_story_beats("big_kid_list")
    assert len(beats["beats"]) == 14
    assert get_title("big_kid_list")["family"] == "next_steps"


def test_pages_from_manuscript():
    data = {
        "title": "Emma and the Big-Kid List",
        "spreads": [
            {"n": n, "text": f"text {n}", "scene": "s", "calm_zone": calm_zone_for(n)}
            for n in range(1, 15)
        ],
        "parent_spread": {
            "you_could_say": "a",
            "try_together": "b",
            "in_real_life": "c",
            "read_it_again": "d",
        },
        "dedication": "For Emma",
    }
    pages = _pages_from_manuscript(data, {"name": "Emma"})
    assert len(pages) == 32
    assert pages[0]["kind"] == "title"
    assert pages[1]["spread"] == 1 and pages[1]["text"] == "text 1"  # calm left
    assert pages[2]["text"] == ""
    assert pages[3]["spread"] == 2 and pages[3]["text"] == ""  # calm right → text on page 5
    assert pages[4]["text"] == "text 2"


def test_app_imports():
    from app import create_app

    app = create_app()
    client = app.test_client()
    assert client.get("/create").status_code == 200
