"""Face sheet prompt must lock the book outfit neckline, not photo clothes."""

from __future__ import annotations

from pipeline.brand import ROUTE_PRESETS
from pipeline.character import _sheet_prompt


def test_face_prompt_requires_book_outfit_not_photo_clothes():
    preset = ROUTE_PRESETS["morning_light"]
    inputs = {
        "title_id": "dinosaur_egg",
        "name": "Emma",
        "age": "4",
        "pronouns": "she",
        "outfit": "teal jumper, navy trousers, rust wellies",
    }
    title = {"outfit": "teal jumper, navy trousers, rust wellies"}
    prompt = _sheet_prompt("face", preset, inputs, title)
    assert "teal jumper" in prompt
    assert "neckline" in prompt.lower()
    assert "not the clothes from the photo" in prompt.lower()
    assert "head-and-shoulders" in prompt.lower() or "Head-and-shoulders" in prompt


def test_body_prompt_still_uses_full_outfit():
    preset = ROUTE_PRESETS["morning_light"]
    inputs = {
        "title_id": "dinosaur_egg",
        "name": "Emma",
        "age": "4",
        "pronouns": "she",
        "outfit": "teal jumper, navy trousers, rust wellies",
    }
    title = {"outfit": inputs["outfit"]}
    prompt = _sheet_prompt("body", preset, inputs, title)
    assert "Everyday outfit: teal jumper" in prompt
