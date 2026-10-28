"""Cover layout zones, reframe, and prompt rules."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pipeline.cover_layout import (
    check_front_faces,
    front_cover_zones,
    layout_prompt_rules,
    reframe_cover_art,
)
from pipeline.illustrate import _cover_prompt


def _solid_cover(path: Path, size: int = 400) -> None:
    Image.new("RGB", (size, size), (180, 200, 160)).save(path)


def test_front_zones_derived_from_title_layout():
    zones = front_cover_zones("Emma and the Lost Dinosaur Egg", "Emma")
    assert 0.25 <= zones["clear_top"] <= 0.42
    assert 0.75 <= zones["clear_bottom"] <= 0.92
    assert zones["clear_top"] < zones["face_centre_y"] < zones["clear_bottom"]
    assert abs(zones["face_centre_y"] - (zones["clear_top"] + zones["clear_bottom"]) / 2) < 1e-6
    # Prompt percentages match the computed fractions.
    assert zones["title_pct"] == round(zones["clear_top"] * 100)
    assert zones["centre_pct"] == round(zones["face_centre_y"] * 100)


def test_cover_prompt_keeps_layout_rules_when_cover_art_present():
    story = {
        "title": "Emma and the Lost Dinosaur Egg",
        "cover_art": "Emma at the hedge gap with Pip in her arms; ferns behind.",
        "outfit": "teal jumper",
    }
    inputs = {"title_id": "dinosaur_egg", "name": "Emma"}
    prompt = _cover_prompt("front", inputs, story)
    zones = front_cover_zones(story["title"], "Emma")
    assert "LAYOUT (binding)" in prompt
    assert f"from {zones['title_pct']}%" in prompt
    assert "never on the shoulders" in prompt
    assert story["cover_art"] in prompt or "Pip" in prompt


def test_reframe_moves_high_face_to_centre_without_api(tmp_path: Path):
    path = tmp_path / "front.png"
    _solid_cover(path, 400)
    zones = front_cover_zones("Emma and the Lost Dinosaur Egg", "Emma")
    # Face sitting under the title (~28% centre).
    faces = [{"x": 0.40, "y": 0.20, "w": 0.20, "h": 0.16}]  # centre y = 0.28
    before = check_front_faces(faces, zones)
    assert before["passed"] is False
    result = reframe_cover_art(path, faces, zones)
    assert result["shifted"] is True
    assert result["too_far"] is False
    assert abs(result["shift_y"]) > 0.05
    after_faces = result["faces"]
    cy = after_faces[0]["y"] + after_faces[0]["h"] / 2
    assert abs(cy - zones["face_centre_y"]) < 0.05
    assert path.is_file()


def test_companion_in_title_zone_fails_check_then_reframe(tmp_path: Path):
    path = tmp_path / "front.png"
    _solid_cover(path, 400)
    zones = front_cover_zones("Emma and the Lost Dinosaur Egg", "Emma")
    # Child OK-ish, companion (Pip) above title line.
    faces = [
        {"x": 0.35, "y": 0.45, "w": 0.18, "h": 0.16},
        {"x": 0.50, "y": 0.05, "w": 0.12, "h": 0.12},  # in title zone
    ]
    check = check_front_faces(faces, zones)
    assert check["passed"] is False
    assert any("title" in f for f in check["failures"])
    result = reframe_cover_art(path, faces, zones)
    # Union centre is high → shift down or too_far if extreme.
    assert result["shifted"] or result["too_far"]


def test_layout_prompt_rules_use_zone_percentages():
    zones = front_cover_zones("Emma and the Lost Dinosaur Egg", "Emma")
    text = layout_prompt_rules("front", zones)
    assert str(zones["title_pct"]) in text
    assert str(zones["centre_pct"]) in text
