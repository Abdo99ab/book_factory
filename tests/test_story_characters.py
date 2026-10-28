"""Recurring story characters: sheets, approval gate, per-spread refs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from pipeline.catalogue import load_locked_story
from pipeline.illustrate import build_reference_pack, reference_prompt_block
from pipeline.story_characters import (
    StoryCharacterError,
    assert_characters_ready,
    characters_for_spread,
    characters_named_in_text,
    list_story_characters,
    resolve_sheet_path,
    unapproved_or_missing,
)


def test_dinosaur_egg_characters_defined_and_approved():
    chars = list_story_characters("dinosaur_egg")
    ids = {c["id"] for c in chars}
    assert "pip" in ids and "pip_mum" in ids
    for c in chars:
        path = resolve_sheet_path(c, "dinosaur_egg")
        assert path.is_file(), path
        assert c.get("approved") is True
    assert_characters_ready("dinosaur_egg")


def test_characters_for_spread_filters():
    locked = load_locked_story("dinosaur_egg")
    story = {"characters": locked["characters"]}
    s2 = characters_for_spread("dinosaur_egg", story, 2)
    assert {c["id"] for c in s2} == {"pip"}
    s11 = characters_for_spread("dinosaur_egg", story, 11)
    assert {c["id"] for c in s11} == {"pip", "pip_mum"}
    s1 = characters_for_spread("dinosaur_egg", story, 1)
    assert s1 == []


def test_cover_art_picks_pip():
    locked = load_locked_story("dinosaur_egg")
    named = characters_named_in_text({"characters": locked["characters"]}, locked["cover_art"])
    assert any(c["id"] == "pip" for c in named)


def test_unapproved_blocks_illustration(tmp_path: Path, monkeypatch):
    locked = load_locked_story("dinosaur_egg")
    fake = [{**locked["characters"][0], "approved": False}]
    monkeypatch.setattr(
        "pipeline.story_characters.list_story_characters",
        lambda title_id: fake,
    )
    with pytest.raises(StoryCharacterError) as exc:
        assert_characters_ready("dinosaur_egg")
    assert "unapproved" in str(exc.value).lower() or "not approved" in str(exc.value).lower()


def test_reference_pack_caps_at_four_and_labels(tmp_path: Path):
    locked = load_locked_story("dinosaur_egg")
    story = {
        "title_id": "dinosaur_egg",
        "characters": locked["characters"],
        "spreads": locked["spreads"],
        "cover_art": locked["cover_art"],
    }
    char = tmp_path / "character"
    char.mkdir()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(char / "face.png")
    Image.new("RGB", (32, 32), (4, 5, 6)).save(char / "character.png")
    inputs = {"title_id": "dinosaur_egg", "name": "Emma"}
    refs, labels, meta = build_reference_pack(tmp_path, inputs, story, n=11)
    assert len(refs) <= 4
    assert labels[0].startswith("Emma's face")
    assert labels[1].startswith("Emma's outfit")
    assert any("Pip" in lab for lab in labels)
    block = reference_prompt_block(labels)
    assert "image 1 =" in block
    assert "do not copy backgrounds" in block
    assert meta["refs"]
