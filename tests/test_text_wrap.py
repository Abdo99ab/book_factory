"""Phrase-aware line breaks and explicit newlines."""

from __future__ import annotations

from pipeline.fonts import register_brand_fonts, reportlab_name
from pipeline.text_wrap import HANGING_WORDS, fit_lines_for_story, wrap_text


def _font():
    register_brand_fonts()
    return reportlab_name("body")


def test_explicit_newlines_honoured():
    font = _font()
    text = "Then she lowers her great\nhead, slowly, gently."
    lines = wrap_text(text, font, 18, 400)
    assert lines[0] == "Then she lowers her great"
    assert lines[1].startswith("head,")


def test_never_ends_line_on_hanging_word():
    font = _font()
    # Force a wrap in a phrase that would otherwise leave "the" at the end.
    text = "Behind it, where the garden usually stops, giant ferns sway."
    lines = wrap_text(text, font, 22, 180)
    for line in lines[:-1]:
        last = line.rstrip(".,;:!?…\"'").split()[-1].lower()
        assert last not in HANGING_WORDS, f"line ends on hanging word: {line!r}"


def test_forced_wrap_callback_when_explicit_line_too_wide():
    font = _font()
    forced = []
    text = "A really long human-approved line that cannot possibly fit in a narrow measure at all " * 2
    text = text.strip() + "\nShort."
    lines = wrap_text(text, font, 20, 120, honour_newlines=True, on_forced_wrap=forced.append)
    assert forced, "expected forced-wrap callback"
    assert any("Short" in line for line in lines)


def test_fit_lines_keeps_sentences_when_possible():
    font = _font()
    text = "Pip jumps. Pip lands. Pip smiles."
    lines = fit_lines_for_story(text, font, 18, 500)
    assert len(lines) == 3
