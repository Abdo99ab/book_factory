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


def _spread(n: int) -> dict:
    # Catalogue must_include tokens for spreads 6 and 11 must appear in the mock text/scene.
    if n == 6:
        text = "Emma looks at the potty. Not today."
        scene = (
            "Spread 6: on the left the potty sits quiet; on the right Emma says Not today."
        )
    elif n == 11:
        text = "Emma wees in the potty. They sing the hand-wash song at the sink."
        scene = (
            "Spread 11: on the left the potty and a wee; on the right they wash at the sink "
            "and sing the hand-wash song."
        )
    else:
        text = "Emma picks a shoe."
        scene = (
            f"Spread {n}: on the left the kitchen is wide and quiet; "
            "on the right Emma's hands hold a shoe."
        )
    return {
        "n": n,
        "text": text,
        "scene": scene,
        "calm_zone": "left" if n % 2 else "right",
    }


_INPUTS = {
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


@patch("pipeline.story.humanize_story_fields", side_effect=lambda _c, _d, s: s)
@patch("pipeline.story.chat_json")
def test_payload_has_binding_beats_signature_sets_and_spread_schema(mock_chat, _h, tmp_path):
    from pipeline import story as story_module

    mock_chat.return_value = json.dumps({"spreads": [_spread(i) for i in range(1, 15)]})
    job_dir = tmp_path / "job"
    (job_dir / "story").mkdir(parents=True)
    out = rebuild_story(MagicMock(), job_dir, dict(_INPUTS))

    system, user_json = mock_chat.call_args.args[1], mock_chat.call_args.args[2]
    payload = json.loads(user_json)
    assert len(payload["beats"]) == 14
    assert "{carer}" not in json.dumps(payload["beats"]) and "{name}" not in json.dumps(payload["beats"])
    assert payload["carer_signature"] == "Mum's yellow scarf"
    assert payload["sets"] == ["kitchen", "hallway", "garden", "nursery_door"]
    assert payload["last_line"] == "I'm the one who decides. And I decided."
    schema = payload["output_schema"]["spreads"][0]
    assert {"n", "text", "scene", "calm_zone"} <= set(schema)
    assert "scene_left" not in schema and "scene_right" not in schema
    for phrase in ("BEATS ARE BINDING", "SAME events in the SAME order", "Add nothing", "exact"):
        assert phrase in story_module.SYSTEM
    assert "ONE CONTINUOUS PICTURE" in system and "calm_zone" in system
    assert "scene_left" not in system and "DIFFERENT pictures" not in system
    assert [sp["calm_zone"] for sp in out["spreads"][:2]] == ["left", "right"]
    assert out["carer_signature"] == "Mum's yellow scarf"
    assert "{name}" not in json.dumps(out["locked"]["parent_spread"])


@patch("pipeline.story.humanize_story_fields", side_effect=lambda _c, _d, s: s)
@patch("pipeline.story.chat_json")
def test_scene_that_skips_a_half_is_a_hard_fail_and_retried(mock_chat, _h, tmp_path):
    bad = [_spread(i) for i in range(1, 15)]
    bad[2]["scene"] = "Emma sits in the kitchen with a shoe."  # never mentions left / right
    good = [_spread(i) for i in range(1, 15)]
    mock_chat.side_effect = [json.dumps({"spreads": bad}), json.dumps({"spreads": good})]
    job_dir = tmp_path / "job"
    (job_dir / "story").mkdir(parents=True)
    out = rebuild_story(MagicMock(), job_dir, dict(_INPUTS))
    assert mock_chat.call_count == 2
    assert "left" in out["spreads"][2]["scene"] and "right" in out["spreads"][2]["scene"]


def test_scene_halves_guard_rules():
    from pipeline.text_guards import scene_halves_issues

    ok = {"n": 1, "scene": "On the left, a quiet hall. On the right, Emma reaches up."}
    assert scene_halves_issues([ok], require=True) == []
    only_left = {"n": 2, "scene": "A quiet hall on the left."}
    assert [i["code"] for i in scene_halves_issues([only_left])] == ["scene_halves"]
    assert "right" in scene_halves_issues([only_left])[0]["message"]
    empty = {"n": 3, "scene": ""}
    assert scene_halves_issues([empty]) == []  # legacy spreads are skipped ...
    assert scene_halves_issues([empty], require=True)  # ... unless story generation requires them
    assert scene_halves_issues([{"n": 4, "scene": "The leftover bread and a copyright note."}])  # whole words only


@patch("pipeline.story.humanize_story_fields", side_effect=lambda _c, _d, s: s)
@patch("pipeline.story.chat_json")
def test_guard_retry_path(mock_chat, _mock_humanize, tmp_path):
    spreads_ok = [_spread(i) for i in range(1, 15)]
    spreads_ok[-1]["text"] = "Emma holds the pen. I'm the one who decides. And I decided."
    bad_spreads = [_spread(i) for i in range(1, 15)]
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
