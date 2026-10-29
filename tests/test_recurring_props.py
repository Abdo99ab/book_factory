"""Recurring props (egg, curly shell) should be fixed sheets like Pip."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pipeline.catalogue import load_locked_story
from pipeline.illustrate import MAX_REF_IMAGES, build_reference_pack
from pipeline.story_characters import (
    characters_for_spread,
    find_recurring_prop_candidates,
    list_story_characters,
    resolve_sheet_path,
)


def test_dinosaur_egg_has_egg_and_curly_shell_sheets():
    chars = {c["id"]: c for c in list_story_characters("dinosaur_egg")}
    assert "egg" in chars and chars["egg"].get("kind") == "prop"
    assert "curly_shell" in chars and chars["curly_shell"].get("kind") == "prop"
    assert chars["egg"]["spreads"] == [1, 2]
    assert chars["curly_shell"]["spreads"] == [13, 14]
    assert resolve_sheet_path(chars["egg"], "dinosaur_egg").is_file()
    assert resolve_sheet_path(chars["curly_shell"], "dinosaur_egg").is_file()
    assert chars["egg"].get("approved") is True
    assert chars["curly_shell"].get("approved") is True


def test_find_recurring_props_flags_nothing_missing_once_locked():
    locked = load_locked_story("dinosaur_egg")
    missing = find_recurring_prop_candidates(locked)
    ids = {m["id"] for m in missing}
    # Egg + curly shell are already characters; nest may still surface as a candidate.
    assert "egg" not in ids
    assert "curly_shell" not in ids


def test_spread_1_includes_egg_ref(tmp_path: Path):
    locked = load_locked_story("dinosaur_egg")
    story = {
        "title_id": "dinosaur_egg",
        "characters": locked["characters"],
        "spreads": locked["spreads"],
    }
    char = tmp_path / "character"
    char.mkdir()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(char / "face.png")
    Image.new("RGB", (32, 32), (4, 5, 6)).save(char / "character.png")
    inputs = {"title_id": "dinosaur_egg", "name": "Emma"}
    refs, labels, _ = build_reference_pack(tmp_path, inputs, story, n=1)
    assert any("egg" in lab.lower() for lab in labels)
    assert len(refs) <= MAX_REF_IMAGES
    assert characters_for_spread("dinosaur_egg", story, 1)[0]["id"] == "egg"


def test_spread_13_can_fit_pip_mum_and_shell(tmp_path: Path):
    locked = load_locked_story("dinosaur_egg")
    story = {
        "title_id": "dinosaur_egg",
        "characters": locked["characters"],
        "spreads": locked["spreads"],
    }
    char = tmp_path / "character"
    char.mkdir()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(char / "face.png")
    Image.new("RGB", (32, 32), (4, 5, 6)).save(char / "character.png")
    inputs = {"title_id": "dinosaur_egg", "name": "Emma"}
    refs, labels, _ = build_reference_pack(tmp_path, inputs, story, n=13)
    joined = " ".join(labels).lower()
    assert "pip" in joined
    assert "mum" in joined or "pip's mum" in joined
    assert "shell" in joined or "curly" in joined
    assert len(refs) <= MAX_REF_IMAGES
    assert MAX_REF_IMAGES >= 5
