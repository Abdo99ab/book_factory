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


def test_llm_false_pass_flag_with_clean_issues_still_passes(bundle):
    """Models often return passed=false with a glowing summary and score 100."""
    job_dir, inputs, story, pages = bundle
    llm = {
        "passed": False,
        "score": 100,
        "summary": (
            "All 14 story spreads are suitable for illustration and comply "
            "with the supplied personalisation and safety constraints."
        ),
        "issues": [],
        "invented_elements": [],
        "repair": {"needed": False},
    }
    with patch.object(validate, "_llm_validate", return_value=llm):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert report["score"] == 100


def test_validate_auto_appends_missing_catalogue_last_line(bundle):
    job_dir, inputs, story, pages = bundle
    story = clone(story)
    pages = clone(pages)
    story["spreads"][-1]["text"] = "Emma smiles at the list."
    for page in pages:
        if page.get("spread") == 14:
            page["text"] = story["spreads"][-1]["text"]
    with patch.object(validate, "_llm_validate", return_value=_llm_pass()):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    saved = json.loads((job_dir / "story" / "rebuilt_story.json").read_text(encoding="utf-8"))
    assert "I'm the one who decides. And I decided." in saved["spreads"][-1]["text"]


LAST_LINE = "I'm the one who decides. And I decided."


def test_llm_moral_flag_on_last_line_does_not_block_or_strip_it(bundle):
    """Catalogue last_line often sounds like a moral — never fail or rewrite it away."""
    job_dir, inputs, story, pages = bundle
    llm = {
        "passed": False,
        "score": 70,
        "summary": "Spread 14 ends with a stated moral.",
        "issues": [
            {
                "severity": "error",
                "code": "stated_moral",
                "message": (
                    f'Spread 14 states a moral: "{LAST_LINE}" — remove the lesson sentence.'
                ),
                "page": 28,
                "fix_hint": "Rewrite without the moral.",
            }
        ],
        "invented_elements": [],
        "repair": {
            "needed": True,
            "page_fixes": [
                {
                    "number": 28,
                    "text": "Emma holds the pen and looks at the list.",
                }
            ],
        },
    }
    with patch.object(validate, "_llm_validate", return_value=llm):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert all(i["code"] != "stated_moral" or i["severity"] == "warning" for i in report["issues"])
    assert story["spreads"][-1]["text"].rstrip().endswith(LAST_LINE)
    # Scrub dropped the text rewrite that would have removed the last_line.
    assert report["repair"].get("needed") is False or not any(
        f.get("text") for f in report["repair"].get("page_fixes") or []
    )


def test_repair_rewrite_of_spread_14_restores_last_line(bundle):
    job_dir, inputs, story, pages = bundle
    story = clone(story)
    pages = clone(pages)
    # Soften would normally scrub a last_line-removing text fix; exercise the post-repair
    # restore path with a repair that still rewrites spread 14 (scene + text), then force.
    repair = {
        "needed": True,
        "page_fixes": [
            {
                "number": 28,
                "scene": "Left: quiet fridge. Right: Emma with the pen.",
                "text": "Emma holds the pen. The list is finished.",
            }
        ],
    }
    story, pages = validate._apply_repair(story, pages, repair)
    assert LAST_LINE not in story["spreads"][-1]["text"]
    story, pages, restored = validate._force_catalogue_last_line(inputs, story, pages)
    assert restored is True
    assert story["spreads"][-1]["text"].rstrip().endswith(LAST_LINE)
    with patch.object(validate, "_llm_validate", return_value=_llm_pass()):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert story["spreads"][-1]["text"].rstrip().endswith(LAST_LINE)


def test_stated_moral_elsewhere_still_hard_error(bundle):
    job_dir, inputs, story, pages = bundle
    llm = {
        "passed": False,
        "score": 60,
        "summary": "Spread 9 states a moral.",
        "issues": [
            {
                "severity": "error",
                "code": "stated_moral",
                "message": 'Spread 9: "And that\'s why we always share." is a stated moral.',
                "page": 18,
                "fix_hint": "Remove the lesson sentence.",
            }
        ],
        "invented_elements": [],
        "repair": {"needed": False},
    }
    with patch.object(validate, "_llm_validate", return_value=llm):
        with pytest.raises(ValidationError) as caught:
            validate_content(None, job_dir, inputs, story, pages)
    assert any(
        i.get("code") == "stated_moral" and i.get("severity") == "error"
        for i in caught.value.report["issues"]
    )


def test_last_line_apostrophe_variants_treated_as_same(bundle):
    curly = "I\u2019m the one who decides. And I decided."
    assert validate._refers_to_last_line(
        f'Spread 14 moral: "{curly}"',
        LAST_LINE,
    )
    soft = validate._soften_llm_report(
        {
            "passed": False,
            "issues": [
                {
                    "severity": "error",
                    "code": "stated_moral",
                    "message": f"Remove the lesson: {curly}",
                    "page": 28,
                }
            ],
            "invented_elements": [],
            "repair": {"needed": False},
        },
        bundle[1],
        bundle[3],
    )
    assert soft["passed"] is True
    assert soft["issues"][0]["severity"] == "warning"
    assert soft["issues"][0]["code"] == "locked_last_line_ok"


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
    # spread 2 = pages 4 and 5; calm zone is the right half, so only page 5 carries the text
    assert by_number[5]["text"] == "Emma puts on the shoes."
    assert by_number[4]["text"] == ""
    assert by_number[4]["scene"] == by_number[5]["scene"] == "Emma in the hall by the door."
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


# Spread 11 → pages 22 (left) / 23 (right). Emma text already has potty / wee / hand-wash song / sink.
SPREAD_11_TEXT = "Emma wees in the potty. They sing the hand-wash song at the sink."


def test_repair_that_drops_must_include_token_keeps_original_text(bundle):
    """Repair that drops 'wee' is scrubbed; original text kept and validation passes."""
    job_dir, inputs, story, pages = bundle
    story = clone(story)
    pages = clone(pages)
    assert "wee" in story["spreads"][10]["text"].lower()
    original = story["spreads"][10]["text"]
    llm = {
        "passed": False,
        "score": 70,
        "summary": "Reword spread 11.",
        "issues": [],
        "invented_elements": [],
        "repair": {
            "needed": True,
            "page_fixes": [
                {
                    "number": 22,
                    "text": "At the sink, Emma and Mum sing together to twenty.",
                }
            ],
        },
    }
    with patch.object(validate, "_llm_validate", return_value=llm):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert story["spreads"][10]["text"] == original
    assert "wee" in story["spreads"][10]["text"].lower()
    assert "potty" in story["spreads"][10]["text"].lower()
    # Soften dropped the token-stripping rewrite.
    assert not any(
        f.get("text") for f in (report.get("repair") or {}).get("page_fixes") or []
    )


def test_repair_that_keeps_must_include_tokens_is_applied(bundle):
    job_dir, inputs, story, pages = bundle
    story = clone(story)
    pages = clone(pages)
    # Keep every must_include token; stay within the 2–3½ sentence word cap (≤9).
    reworded = "Emma wees in the potty. They sing the hand-wash song at the sink."
    llm = {
        "passed": False,
        "score": 70,
        "summary": "Minor wording.",
        "issues": [
            {
                "severity": "error",
                "code": "awkward_wording",
                "message": "Spread 11 wording is awkward.",
                "page": 22,
                "fix_hint": "Reword.",
            }
        ],
        "invented_elements": [],
        "repair": {
            "needed": True,
            "page_fixes": [{"number": 22, "text": reworded}],
        },
    }
    with patch.object(
        validate,
        "_llm_validate",
        side_effect=[llm, _llm_pass()],
    ):
        report = validate_content(None, job_dir, inputs, story, pages)
    assert report["passed"] is True
    assert story["spreads"][10]["text"] == reworded
    from pipeline.text_guards import _text_has_token

    for token in ("potty", "wee", "hand-wash song", "sink"):
        assert _text_has_token(story["spreads"][10]["text"], token)


def test_apply_repair_rejects_text_that_drops_wee(bundle):
    _job, inputs, story, pages = bundle
    story = clone(story)
    pages = clone(pages)
    original = story["spreads"][10]["text"]
    repair = {
        "needed": True,
        "page_fixes": [
            {"number": 22, "text": "At the sink, Emma and Mum sing together."}
        ],
    }
    story, pages = _apply_repair(story, pages, repair, inputs=inputs)
    assert story["spreads"][10]["text"] == original
    assert "wee" in story["spreads"][10]["text"].lower()
