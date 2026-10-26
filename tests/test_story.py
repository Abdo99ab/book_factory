"""Step 5 — catalogue story, guards, humanize fallback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.catalogue import get_title
from pipeline.humanize import humanize_story_fields
from pipeline.story import _build_locked, rebuild_story
from pipeline.text_guards import check_spreads


def test_title_never_from_llm():
    title = get_title("big_kid_list")
    locked = _build_locked(title, {"name": "Emma", "dedication_text": "Hi"})
    assert locked["title"] == "Emma and the Big-Kid List"
    assert "READY FOR THE POTTY" in locked["front_band"]


def test_locked_keys_present():
    title = get_title("big_kid_list")
    locked = _build_locked(title, {"name": "Emma", "dedication_text": ""})
    for key in ("title", "front_band", "back_question", "parent_spread", "dedication"):
        assert key in locked


@patch("pipeline.story.humanize_story_fields", side_effect=lambda _c, _d, s: s)
@patch("pipeline.story.chat_json")
def test_guard_retry_path(mock_chat, _mock_humanize, tmp_path):
    spreads_ok = [{"n": i, "text": "Emma picks a step today.", "scene": "kitchen"} for i in range(1, 15)]
    spreads_ok[-1]["text"] = (
        "Emma smiles. The list waits on the fridge. Emma can choose again tomorrow."
    )
    bad_spreads = [
        {"n": i, "text": "Emma picks a step today.", "scene": "kitchen"}
        for i in range(1, 15)
    ]
    bad_spreads[0]["text"] = "A magical day for Emma."
    bad = {"spreads": bad_spreads}
    good = {"spreads": spreads_ok}
    mock_chat.side_effect = [json.dumps(bad), json.dumps(good)]

    job_dir = tmp_path / "job"
    (job_dir / "story").mkdir(parents=True)
    inputs = {
        "title_id": "big_kid_list",
        "name": "Emma",
        "age": "3",
        "pronouns": "she",
        "gender": "female",
        "age_band": "2-3.5",
        "carer": {"name": "Sam", "role": "Mum"},
        "home_type": "house",
        "comfort_object": "bunny",
        "slots": {"potty_or_seat": "potty"},
    }
    story = rebuild_story(MagicMock(), job_dir, inputs)
    assert len(story["spreads"]) == 14
    assert story["title"] == "Emma and the Big-Kid List"
    assert mock_chat.call_count == 2


@patch("pipeline.humanize.humanize_enabled", return_value=True)
@patch("pipeline.humanize.humanize_json")
def test_humanize_fallback_on_guard_failure(mock_humanize, _enabled, tmp_path):
    job_dir = tmp_path / "job"
    (job_dir / "story").mkdir(parents=True)
    pre = {
        "spreads": [
            {"n": i, "text": "Emma picks shoes.", "scene": "hall"} for i in range(1, 15)
        ],
        "locked": {"title": "T"},
    }
    bad = [{"n": i, "text": "Emma picks shoes.", "scene": "hall"} for i in range(1, 15)]
    bad[0]["text"] = "A magical journey begins."
    mock_humanize.return_value = {"spreads": bad}
    out = humanize_story_fields(MagicMock(), job_dir, dict(pre, humanized=False))
    # humanize_story_fields merges spreads — simulate story.py post-check separately
    issues = check_spreads(
        pre,
        out,
        pre["locked"],
        child_name="Emma",
        age_band="2-3.5",
        last_line="",
    )
    assert any(i["code"] == "banned_phrase" for i in issues)
