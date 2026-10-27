"""Step 7 — catalogue-aware validation for the Raised Ready spreads schema."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from emma_data import clone, emma_bundle
from pipeline import validate
from pipeline.validate import (
    ValidationError,
    _apply_repair,
    _structural_issues,
    must_not_show_hits,
    scene_text_mismatches,
    validate_content,
)


def _errors(issues):
    return [i for i in issues if i["severity"] == "error"]


def _codes(issues):
    return {i["code"] for i in issues}


@pytest.fixture
def bundle(tmp_path):
    job_dir = tmp_path / "job"
    inputs, story, pages = emma_bundle(job_dir)
    return job_dir, inputs, story, pages


def test_spreads_story_without_rebuilt_story_passes(bundle):
    _job, inputs, story, pages = bundle
    assert "rebuilt_story" not in story
    issues = _structural_issues(inputs, story, pages)
    assert _errors(issues) == [], _errors(issues)


def test_missing_locked_and_outfit_are_errors(bundle):
    _job, inputs, story, pages = bundle
    story = clone(story)
    story.pop("locked")
    story["recurring_outfit"] = ""
    codes = _codes(_errors(_structural_issues(inputs, story, pages)))
    assert {"missing_locked", "missing_outfit"} <= codes


def test_band_and_question_must_match_catalogue(bundle):
    _job, inputs, story, pages = bundle
    story = clone(story)
    story["locked"]["front_band"] = "READY FOR NURSERY"
    story["locked"]["back_question"] = "Ready for nursery, Emma?"
    codes = _codes(_errors(_structural_issues(inputs, story, pages)))
    assert "band_mismatch" in codes
    assert "question_mismatch" in codes


def test_parent_spread_must_be_byte_identical(bundle):
    _job, inputs, story, pages = bundle
    story = clone(story)
    story["locked"]["parent_spread"]["try_together"] += " "
    assert "parent_spread_changed" in _codes(_errors(_structural_issues(inputs, story, pages)))


def test_text_guard_violation_surfaces_as_error(bundle):
    _job, inputs, story, pages = bundle
    story = clone(story)
    story["spreads"][0]["text"] = "Emma sees a magical list."
    codes = _codes(_errors(_structural_issues(inputs, story, pages)))
    assert "guard_banned_phrase" in codes


def test_readability_guard_is_warning_not_error(bundle):
    _job, inputs, story, pages = bundle
    story = clone(story)
    # Long words push Flesch–Kincaid over the 2.0 band cap without banned phrases.
    story["spreads"][0]["text"] = (
        "Emma contemplates the refrigerator checklist meticulously before deciding."
    )
    issues = _structural_issues(inputs, story, pages)
    readability = [i for i in issues if i["code"] == "guard_readability"]
    assert readability and all(i["severity"] == "warning" for i in readability)
    assert "guard_readability" not in _codes(_errors(issues))


def test_vague_toys_llm_flag_does_not_fail_validation(bundle):
    job_dir, inputs, story, pages = bundle
    llm = {
        "passed": False,
        "score": 90,
        "summary": "page 14 introduces toys that are not grounded in the supplied story elements.",
        "issues": [
            {
                "severity": "error",
                "code": "invented",
                "message": "page 14 introduces toys that are not grounded in the supplied story elements.",
                "page": 14,
            }
        ],
        "invented_elements": ["unspecified toys"],
        "repair": {"needed": False},
    }
    with patch.object(validate, "_llm_validate", return_value=llm):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert report["invented_elements"] == []
    assert all(i["severity"] != "error" or i["code"] != "invented" for i in report["issues"])


def test_must_not_show_in_scene_is_error(bundle):
    _job, inputs, story, pages = bundle
    pages = clone(pages)
    assert pages[2]["number"] == 3
    pages[2]["scene"] += " A countdown timer ticks on the wall."
    issues = _structural_issues(inputs, story, pages)
    assert any(i["code"] == "must_not_show" and i["page"] == 3 for i in issues)


def test_negated_must_not_show_is_allowed():
    assert must_not_show_hits("Emma plays. No countdown, no deadline.", ["deadline or countdown"]) == []
    assert must_not_show_hits("A countdown clock.", ["deadline or countdown"]) == ["deadline or countdown"]


def test_scene_text_mismatch_warns_not_errors(bundle):
    _job, inputs, story, pages = bundle
    pages = clone(pages)
    for p in pages:
        if p.get("spread") == 2:
            p["text"] = "Emma goes back to her blocks."
    issues = _structural_issues(inputs, story, pages)
    mismatch = [i for i in issues if i["code"] == "scene_text_mismatch"]
    assert mismatch and all(i["severity"] == "warning" for i in mismatch)
    assert "blocks" in scene_text_mismatches("Emma goes back to her blocks.", "Emma sits in the hall.")


def test_anchor_state_and_page_map_checked(bundle):
    _job, inputs, story, pages = bundle
    pages = clone(pages)
    pages[3]["anchor_state"] = 0  # spread 1 left half expects 0; page 3 is spread 1 right half
    pages[5]["anchor_state"] = 0  # spread 2 expects 1
    pages[29]["kind"] = "spread_half"  # page 30 must be the parent spread
    codes = _codes(_errors(_structural_issues(inputs, story, pages)))
    assert "anchor_state" in codes
    assert "page_map" in codes


def test_apply_repair_never_touches_locked(bundle):
    _job, _inputs, story, pages = bundle
    story = clone(story)
    pages = clone(pages)
    locked_before = json.dumps(story["locked"], sort_keys=True)
    title_before = story["title"]
    repair = {
        "needed": True,
        "recurring_outfit": "blue coat",
        "locked": {"front_band": "HACKED"},
        "page_fixes": [
            {"number": 4, "scene": "Emma in the hall by the door.", "text": "Emma puts on the shoes."},
            {"number": 1, "text": "Hacked title page"},
            {"number": 30, "text": "Hacked parent page"},
            {"number": 32, "text": "Hacked dedication"},
        ],
    }
    story, pages = _apply_repair(story, pages, repair)
    assert json.dumps(story["locked"], sort_keys=True) == locked_before
    assert story["title"] == title_before
    assert story["recurring_outfit"] == "blue coat"
    by_number = {p["number"]: p for p in pages}
    assert by_number[1]["kind"] == "title" and "Hacked" not in str(by_number[1].get("title"))
    assert "Hacked" not in by_number[30]["text"]
    assert "Hacked" not in by_number[32]["text"]
    # spread 2 = pages 4 and 5; both halves and the story spread updated together
    assert by_number[4]["text"] == by_number[5]["text"] == "Emma puts on the shoes."
    assert story["spreads"][1]["text"] == "Emma puts on the shoes."


def _llm_pass():
    return {"passed": True, "score": 95, "summary": "ok", "issues": [], "repair": {"needed": False}}


def test_validate_content_passes_without_rebuilt_story(bundle):
    job_dir, inputs, story, pages = bundle
    with patch.object(validate, "_llm_validate", return_value=_llm_pass()):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert (job_dir / "story" / "validation_report.json").is_file()


def test_validate_content_raises_on_structural_error(bundle):
    job_dir, inputs, story, pages = bundle
    story = clone(story)
    story["locked"]["front_band"] = "WRONG"
    with patch.object(validate, "_llm_validate", return_value=_llm_pass()):
        with pytest.raises(ValidationError) as caught:
            validate_content(None, job_dir, inputs, story, pages)
    assert any(i["code"] == "band_mismatch" for i in caught.value.report["issues"])
