"""Step 8 — spread prompts: calm zone, must-not-show, no Theme/Purpose, memory scope."""

from __future__ import annotations

import pytest

from emma_data import emma_bundle
from pipeline import constants, illustrate, memory
from pipeline.catalogue import get_title
from pipeline.pages import spread_pages


@pytest.fixture
def bundle(tmp_path):
    return emma_bundle(tmp_path / "job")


def _spread(pages, n):
    return {**spread_pages(pages)[n], "spread": n}


def test_prompt_has_calm_zone_and_must_not_show(bundle):
    inputs, story, pages = bundle
    spread = _spread(pages, 1)  # calm zone: left on odd spreads
    left = illustrate._spread_prompt(spread, "left", inputs, story)
    right = illustrate._spread_prompt(spread, "right", inputs, story)

    assert "Calm zone: the LEFT page carries the story text" in left
    assert "Calm zone: the LEFT page carries the text, so this RIGHT page" in right
    for item in get_title("big_kid_list")["must_not_show"]:
        assert item in left
    assert "MUST NOT SHOW (binding)" in left
    # gutter safety is side-aware
    assert "RIGHT edge is the gutter" in left
    assert "LEFT edge is the gutter" in right


def test_even_spread_puts_calm_zone_on_the_right(bundle):
    inputs, story, pages = bundle
    spread = _spread(pages, 2)
    assert spread["calm_zone"] == "right"
    prompt = illustrate._spread_prompt(spread, "right", inputs, story)
    assert "Calm zone: the RIGHT page carries the story text" in prompt


def test_no_theme_or_purpose_lines(bundle):
    inputs, story, pages = bundle
    inputs = {**inputs, "theme": "Adventure", "purpose": "Bedtime"}  # legacy keys must be ignored
    prompt = illustrate._spread_prompt(_spread(pages, 3), "left", inputs, story)
    assert "Theme:" not in prompt
    assert "Purpose:" not in prompt
    assert "Adventure" not in prompt
    assert "Bedtime" not in prompt


def test_anchor_state_and_outfit_in_prompt(bundle):
    inputs, story, pages = bundle
    prompt = illustrate._spread_prompt(_spread(pages, 4), "left", inputs, story)
    assert "exactly 3 tick(s)" in prompt  # state_schedule[3] for big_kid_list
    assert story["recurring_outfit"] in prompt
    assert "NO text" in prompt or "Do not include any text" in prompt


def test_right_page_lists_left_page_reference(bundle):
    inputs, story, pages = bundle
    labels = ["hero character sheet", "the LEFT page of this same spread, already drawn"]
    prompt = illustrate._spread_prompt(_spread(pages, 1), "right", inputs, story, references=labels)
    assert "1. hero character sheet" in prompt
    assert "2. the LEFT page of this same spread" in prompt


def test_edit_instruction_is_included(bundle):
    inputs, story, pages = bundle
    prompt = illustrate._spread_prompt(
        _spread(pages, 1), "left", inputs, story, edit_instruction="Emma wears sunglasses"
    )
    assert "OPERATOR CHANGE REQUEST" in prompt and "sunglasses" in prompt


def test_memory_off_by_default_and_excluded(bundle, monkeypatch):
    inputs, story, pages = bundle
    prefs = {"preferences": [{"text": "Always use neon colours"}], "history": []}
    monkeypatch.setattr(memory, "load_memory", lambda path=None: prefs)

    monkeypatch.setattr(constants, "MEMORY_SCOPE", "off")
    assert memory.format_memory_for_prompt() == ""
    off = illustrate._spread_prompt(_spread(pages, 1), "left", inputs, story)
    assert "neon" not in off and "LEARNED OPERATOR PREFERENCES" not in off

    monkeypatch.setattr(constants, "MEMORY_SCOPE", "global")
    on = illustrate._spread_prompt(_spread(pages, 1), "left", inputs, story)
    assert "neon" in on
