"""Spread prompts: one continuous double-page, fold safety, calm half, must-not-show, memory scope."""

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


def test_prompt_is_one_continuous_double_page(bundle):
    inputs, story, pages = bundle
    spread = _spread(pages, 1)  # calm zone: left on odd spreads
    prompt = illustrate._spread_prompt(spread, inputs, story)

    assert "ONE continuous double-page" in prompt
    assert "NOT two separate pictures" in prompt
    assert "426 x 216 mm" in prompt
    assert "centre fold" in prompt and "15 mm" in prompt and "10 mm" in prompt
    assert "CALM ZONE: the LEFT half carries the story text" in prompt
    assert "CENTRE of the LEFT half" in prompt
    assert "PROPS: only objects named in the beat" in prompt
    assert "The RIGHT half carries the action" in prompt
    assert "musical instruments" in prompt
    # the one scene (both halves described together) is in the prompt, once
    assert spread["scene"] in prompt
    # no per-page wording survives
    for gone in ("LEFT page", "RIGHT page", "Do not repeat", "The other page of this spread"):
        assert gone not in prompt
    for item in get_title("big_kid_list")["must_not_show"]:
        assert item in prompt
    assert "MUST NOT SHOW (binding)" in prompt


def test_even_spread_puts_calm_zone_on_the_right(bundle):
    inputs, story, pages = bundle
    spread = _spread(pages, 2)
    assert spread["calm_zone"] == "right"
    prompt = illustrate._spread_prompt(spread, inputs, story)
    assert "CALM ZONE: the RIGHT half carries the story text" in prompt
    assert "CENTRE of the RIGHT half" in prompt
    assert "The LEFT half carries the action" in prompt


def test_no_theme_or_purpose_lines(bundle):
    inputs, story, pages = bundle
    inputs = {**inputs, "theme": "Adventure", "purpose": "Bedtime"}  # legacy keys must be ignored
    prompt = illustrate._spread_prompt(_spread(pages, 3), inputs, story)
    assert "Theme:" not in prompt
    assert "Purpose:" not in prompt
    assert "Adventure" not in prompt
    assert "Bedtime" not in prompt


def test_anchor_state_and_outfit_in_prompt(bundle):
    inputs, story, pages = bundle
    prompt = illustrate._spread_prompt(_spread(pages, 4), inputs, story)
    assert "exactly 3 tick(s)" in prompt  # state_schedule[3] for big_kid_list
    assert story["recurring_outfit"] in prompt
    assert "NO text" in prompt or "Do not include any text" in prompt


def test_reference_labels_are_listed_in_order(bundle):
    inputs, story, pages = bundle
    labels = ["hero character sheet", "set sheet"]
    prompt = illustrate._spread_prompt(_spread(pages, 1), inputs, story, references=labels)
    assert "1. hero character sheet" in prompt
    assert "2. set sheet" in prompt


def test_fix_zones_are_named_on_the_retry(bundle):
    inputs, story, pages = bundle
    prompt = illustrate._spread_prompt(_spread(pages, 1), inputs, story, fix_zones=["fold"])
    assert "fold" in prompt.lower()
    plain = illustrate._spread_prompt(_spread(pages, 1), inputs, story)
    assert len(prompt) > len(plain)


def test_edit_instruction_is_included(bundle):
    inputs, story, pages = bundle
    prompt = illustrate._spread_prompt(_spread(pages, 1), inputs, story, edit_instruction="Emma wears sunglasses")
    assert "OPERATOR CHANGE REQUEST" in prompt and "sunglasses" in prompt


def test_memory_off_by_default_and_excluded(bundle, monkeypatch):
    inputs, story, pages = bundle
    prefs = {"preferences": [{"text": "Always use neon colours"}], "history": []}
    monkeypatch.setattr(memory, "load_memory", lambda path=None: prefs)

    monkeypatch.setattr(constants, "MEMORY_SCOPE", "off")
    assert memory.format_memory_for_prompt() == ""
    off = illustrate._spread_prompt(_spread(pages, 1), inputs, story)
    assert "neon" not in off and "LEARNED OPERATOR PREFERENCES" not in off

    monkeypatch.setattr(constants, "MEMORY_SCOPE", "global")
    on = illustrate._spread_prompt(_spread(pages, 1), inputs, story)
    assert "neon" in on
