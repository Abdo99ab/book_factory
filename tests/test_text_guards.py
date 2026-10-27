"""Step 7 — text-guard fixtures (spec §11.5–11.7)."""

from __future__ import annotations

from emma_data import NAME, clone, emma_inputs, emma_story
from pipeline.catalogue import get_title
from pipeline.text_guards import check_spreads


def _run(candidate: dict, draft: dict | None = None):
    inputs = emma_inputs()
    title = get_title(inputs["title_id"])
    base = emma_story(inputs)
    draft = draft or base
    return check_spreads(
        {"spreads": draft["spreads"], "locked": draft["locked"]},
        candidate,
        base["locked"],
        child_name=NAME,
        age_band=inputs["age_band"],
        last_line=title["last_line"].replace("{name}", NAME),
    )


def _codes(issues):
    return {i["code"] for i in issues}


def test_clean_story_passes_all_guards():
    assert _run(emma_story()) == []


def test_banned_phrase_is_flagged():
    cand = emma_story()
    cand["spreads"][2]["text"] = "Emma gets the coat. A magical coat."
    issues = _run(cand)
    assert any(i["code"] == "banned_phrase" and i["spread"] == 3 for i in issues)


def test_banned_multi_word_phrase_is_flagged():
    cand = emma_story()
    cand["spreads"][4]["text"] = "Emma washes her hands. Little did Emma know."
    assert "banned_phrase" in _codes(_run(cand))


def test_us_spelling_is_flagged():
    cand = emma_story()
    cand["spreads"][1]["text"] = "Emma likes the color red. Emma puts on the shoes."
    issues = _run(cand)
    assert any(i["code"] == "us_spelling" and "color" in i["message"] for i in issues)


def test_locked_field_change_is_flagged():
    cand = emma_story()
    cand["locked"]["back_question"] = "Ready for anything, Emma?"
    issues = _run(cand)
    assert any(i["code"] == "locked_changed" and "back_question" in i["message"] for i in issues)


def test_parent_spread_edit_is_a_locked_change():
    cand = emma_story()
    cand["locked"]["parent_spread"]["you_could_say"] = "This will help them settle."
    assert "locked_changed" in _codes(_run(cand))


def test_renamed_child_drops_name_frequency():
    cand = emma_story()
    for spread in cand["spreads"]:
        spread["text"] = spread["text"].replace("Emma", "Emily")
    issues = _run(cand)
    codes = _codes(issues)
    assert "name_frequency" in codes
    assert "new_proper_noun" in codes


def test_word_increase_over_twenty_percent():
    draft = emma_story()
    cand = clone(draft)
    cand["spreads"][0]["text"] = (
        "Emma sees the list. It is on the fridge. Emma looks at the list. Emma smiles."
    )
    issues = _run(cand, draft=draft)
    assert any(i["code"] == "word_increase" and i["spread"] == 1 for i in issues)


def test_small_growth_is_allowed():
    draft = emma_story()
    cand = clone(draft)
    cand["spreads"][0]["text"] = "Emma sees the list. It is on the big fridge."  # 9 -> 9, same count
    assert "word_increase" not in _codes(_run(cand, draft=draft))


def test_missing_last_line_is_flagged():
    cand = emma_story()
    cand["spreads"][-1]["text"] = "Emma smiles. Emma goes to play."
    issues = _run(cand)
    assert any(i["code"] == "last_line" and i["spread"] == 14 for i in issues)


def test_ensure_catalogue_last_line_appends_locked_line():
    from pipeline.text_guards import ensure_catalogue_last_line, last_line_present

    title = get_title("big_kid_list")
    last = title["last_line"].replace("{name}", NAME)
    cand = emma_story()
    cand["spreads"][-1]["text"] = "Emma smiles."
    fixed = ensure_catalogue_last_line(cand["spreads"], last)
    assert last_line_present(fixed[-1]["text"], last)
    assert "last_line" not in _codes(_run({**cand, "spreads": fixed}))


LAST = "I'm the one who decides. And I decided."


def test_norm_text_handles_curly_and_surrounding_quotes():
    from pipeline.text_guards import _norm_text, last_line_present

    assert _norm_text("\u201cI\u2019m the one who decides. And I decided.\u201d") == _norm_text(LAST)
    assert _norm_text('"I\'m the one who decides. And I decided!"') == _norm_text(LAST)
    assert _norm_text("I\u2019m  the one who decides.\nAnd I decided...") == _norm_text(LAST)
    assert "'" in _norm_text(LAST)  # the apostrophe inside "I'm" is kept
    assert last_line_present("Emma smiles. \u201cI\u2019m the one who decides. And I decided.\u201d", LAST)
    assert not last_line_present("Emma smiles.", LAST)


def test_ensure_replaces_a_near_match_without_duplicating():
    from pipeline.text_guards import ensure_catalogue_last_line

    for variant in (
        "\u201cI\u2019m the one who decides. And I decided.\u201d",
        "I'm the one who decides, and I decided!",
        "I'm the one who decides. And I decided",
    ):
        spreads = [{"n": 13, "text": "Emma ticks."}, {"n": 14, "text": f"Emma holds the pen. {variant}"}]
        fixed = ensure_catalogue_last_line(spreads, LAST)
        text = fixed[1]["text"]
        assert text == f"Emma holds the pen. {LAST}", variant
        assert text.count("I decided") == 1 and text.count("who decides") == 1  # the line appears once
        assert fixed[0] == spreads[0]


def test_ensure_appends_only_when_nothing_similar_and_is_idempotent():
    from pipeline.text_guards import ensure_catalogue_last_line

    spreads = [{"n": 14, "text": "Emma smiles at the list."}]
    once = ensure_catalogue_last_line(spreads, LAST)
    assert once[0]["text"] == f"Emma smiles at the list. {LAST}"
    assert ensure_catalogue_last_line(once, LAST) == once
    assert spreads[0]["text"] == "Emma smiles at the list."  # input is not mutated
    assert ensure_catalogue_last_line([{"n": 14, "text": ""}], LAST)[0]["text"] == LAST


def test_guard_message_names_the_last_line_and_what_was_found():
    from pipeline.text_guards import last_line_message

    message = last_line_message("Emma goes to play with her bunny in the garden today.", LAST)
    assert message.startswith('Spread 14 must end with: "I\'m the one who decides. And I decided."')
    assert 'Found: "Emma goes to play with her bunny in the garden today."' in message


def test_calm_zone_must_be_left_or_right():
    cand = emma_story()
    cand["spreads"][2]["calm_zone"] = "middle"
    assert "calm_zone" in _codes(_run(cand))


def test_word_cap_for_age_band():
    cand = emma_story()
    cand["spreads"][5]["text"] = " ".join(["Emma sits."] * 30)  # 60 words > cap 45
    assert "word_cap" in _codes(_run(cand))


def test_spread_count_must_be_fourteen():
    cand = emma_story()
    cand["spreads"] = cand["spreads"][:13]
    assert _codes(_run(cand)) == {"spread_count"}


def test_at_most_one_rhetorical_question():
    cand = emma_story()
    cand["spreads"][0]["text"] = "Emma sees the list. Is it new?"
    cand["spreads"][1]["text"] = "Emma looks at the shoes. Are they red?"
    assert "rhetorical_questions" in _codes(_run(cand))
