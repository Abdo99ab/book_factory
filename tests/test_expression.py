"""Child expression follows beat text/art; covers smile; no forced camera face."""

from __future__ import annotations

from pipeline.catalogue import load_locked_story
from pipeline.illustrate import _cover_prompt, _spread_prompt, expression_from_beat


def test_expression_from_surprise_beat():
    phrase = expression_from_beat(
        {"art": "face lit with surprise", "text": "It's an egg!"}
    )
    assert "surpris" in phrase.lower() or "wonder" in phrase.lower()
    assert "not a polite smile" in phrase.lower() or "not" in phrase.lower()


def test_expression_from_determined_beat():
    phrase = expression_from_beat(
        {
            "art": "[Name] looks determined, cheeks a little puffed.",
            "text": "Pip is heavier than a bag of shopping. Step, step, step.",
        }
    )
    assert "determin" in phrase.lower() or "effort" in phrase.lower()
    assert "not a broad smile" in phrase.lower()


def test_expression_default_avoids_camera_smile():
    phrase = expression_from_beat({"art": "They walk along the path.", "text": "Off they go."})
    assert "camera" in phrase.lower() or "do not default" in phrase.lower()


def test_spread_prompt_drops_face_never_turned_away():
    locked = load_locked_story("dinosaur_egg")
    spread = locked["spreads"][0]  # surprise / egg
    story = {
        "title": "Emma and the Lost Dinosaur Egg",
        "outfit": locked["outfit"],
        "anchor": locked["anchor"],
        "must_not_show": locked["must_not_show"],
        "spreads": locked["spreads"],
    }
    inputs = {"title_id": "dinosaur_egg", "name": "Emma", "pronouns": "she"}
    prompt = _spread_prompt(inputs, story, spread, "FACE LOCK")
    assert "Face never covered or turned away" not in prompt
    assert "EXPRESSION / GAZE:" in prompt
    assert "do not default to facing the camera" in prompt
    assert "surpris" in prompt.lower() or "wonder" in prompt.lower()


def test_cover_prompts_ask_for_smile():
    story = {
        "title": "Emma and the Lost Dinosaur Egg",
        "cover_art": "Emma at the hedge with Pip.",
        "outfit": "teal jumper",
        "characters": [],
    }
    inputs = {"title_id": "dinosaur_egg", "name": "Emma"}
    front = _cover_prompt("front", inputs, story)
    back = _cover_prompt("back", inputs, story)
    assert "smile" in front.lower()
    assert "smile" in back.lower()
