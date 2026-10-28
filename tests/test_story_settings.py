"""Guards: route preambles are style-only; story art is self-contained with settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.brand import ROUTE_PRESETS
from pipeline.catalogue import STORIES_DIR, find_relative_art_refs, load_locked_story
from pipeline.illustrate import _spread_image_refs, _spread_prompt, settings_match

# Words that mean a place/room/prop — must not appear in route STYLE preambles.
PREAMBLE_SETTING_WORDS = (
    "interior",
    "house",
    "room",
    "hallway",
    "coat pegs",
    "wellies",
    "window",
    "garden",
)


def _locked_story_ids() -> list[str]:
    return sorted(p.stem for p in STORIES_DIR.glob("*.json") if p.is_file())


def test_route_preambles_are_style_only():
    for route, preset in ROUTE_PRESETS.items():
        preamble = (preset.get("preamble") or "").lower()
        for word in PREAMBLE_SETTING_WORDS:
            assert word not in preamble, f"{route} preamble must not contain setting word {word!r}"


@pytest.mark.parametrize("title_id", _locked_story_ids())
def test_locked_stories_have_settings_and_no_relative_art(title_id: str):
    locked = load_locked_story(title_id)
    for sp in locked["spreads"]:
        assert str(sp.get("setting") or "").strip(), f"{title_id} spread {sp['n']} missing setting"
        rel = find_relative_art_refs(sp.get("art") or "")
        assert not rel, f"{title_id} spread {sp['n']} relative art refs: {rel}"


def test_dinosaur_egg_garden_continuity_settings():
    locked = load_locked_story("dinosaur_egg")
    by_n = {int(s["n"]): s for s in locked["spreads"]}
    # Spreads 1–3 share the dig-spot garden setting for prev-art refs.
    assert settings_match(by_n[1], by_n[2])
    assert settings_match(by_n[2], by_n[3])
    assert not settings_match(by_n[3], by_n[4])
    assert settings_match(by_n[11], by_n[12])
    assert "rain" in by_n[9]["setting"].lower()
    assert "after" in by_n[10]["setting"].lower() and "rain" in by_n[10]["setting"].lower()


def test_spread_prompt_includes_binding_setting():
    locked = load_locked_story("dinosaur_egg")
    spread = locked["spreads"][1]  # spread 2
    story = {
        "title": "Emma and the Lost Dinosaur Egg",
        "outfit": locked["outfit"],
        "anchor": locked["anchor"],
        "must_not_show": locked["must_not_show"],
        "spreads": locked["spreads"],
    }
    inputs = {"title_id": "dinosaur_egg", "name": "Emma", "pronouns": "she"}
    prompt = _spread_prompt(inputs, story, spread, "FACE LOCK", prev_setting_ref=True)
    assert "SETTING (binding" in prompt
    assert spread["setting"] in prompt
    assert "SCENE:" in prompt
    assert "CONTINUITY:" in prompt
    assert "second reference image" in prompt


def test_spread_image_refs_attach_previous_when_setting_matches(tmp_path: Path):
    locked = load_locked_story("dinosaur_egg")
    story = {"spreads": locked["spreads"]}
    char = tmp_path / "character.png"
    char.write_bytes(b"x")
    prev = tmp_path / "spreads" / "spread-01" / "art.png"
    prev.parent.mkdir(parents=True)
    prev.write_bytes(b"y")

    refs, used = _spread_image_refs(tmp_path, story, char, 2)
    assert used is True
    assert refs == [char, prev]

    # Spread 5 setting differs from 4 — no previous ref even if file exists.
    (tmp_path / "spreads" / "spread-04").mkdir(parents=True, exist_ok=True)
    (tmp_path / "spreads" / "spread-04" / "art.png").write_bytes(b"z")
    refs5, used5 = _spread_image_refs(tmp_path, story, char, 5)
    assert used5 is False
    assert refs5 == [char]
