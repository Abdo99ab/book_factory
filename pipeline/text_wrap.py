"""Phrase-aware story text wrapping for print plates."""

from __future__ import annotations

import re

from reportlab.pdfbase import pdfmetrics

# Never end a line on these (move them to the next line with the following word).
HANGING_WORDS = frozenset(
    {"the", "a", "an", "and", "of", "to", "in", "on", "at", "her", "his", "my", "with"}
)
# Prefer a break after these tokens (punctuation already on the word).
_SOFT_AFTER = frozenset({",", ";", ":", "—", "–", "…", ".", "!", "?"})


def _ends_soft(token: str) -> bool:
    if not token:
        return False
    if token[-1] in _SOFT_AFTER:
        return True
    bare = token.rstrip(".,;:!?…\"'”’").lower()
    return bare in {"and", "or", "but"}


def _starts_dialogue(token: str) -> bool:
    return bool(token) and token[0] in {"\"", "“", "'", "‘"}


def _width(words: list[str], font: str, size: float) -> float:
    return pdfmetrics.stringWidth(" ".join(words), font, size) if words else 0.0


def _avoid_hanging(line: list[str], rest: list[str]) -> tuple[list[str], list[str]]:
    if not line:
        return line, rest
    last = line[-1].rstrip(".,;:!?…\"'”’").lower()
    if last in HANGING_WORDS and len(line) > 1:
        return line[:-1], [line[-1]] + rest
    return line, rest


def _balance_pair(first: list[str], second: list[str]) -> tuple[list[str], list[str]]:
    """Minimise raggedness: prefer two lines of similar width (by word count)."""
    total = first + second
    if len(total) < 4:
        return first, second
    best_cut = len(first)
    best_score = abs(len(first) - len(second))
    for cut in range(2, len(total) - 1):
        left, right = total[:cut], total[cut:]
        if left and left[-1].rstrip(".,;:!?…\"'”’").lower() in HANGING_WORDS:
            continue
        score = abs(len(left) - len(right))
        # Prefer soft break at the cut.
        if _ends_soft(left[-1]) or _starts_dialogue(right[0]):
            score -= 0.5
        if score < best_score:
            best_score = score
            best_cut = cut
    return total[:best_cut], total[best_cut:]


def _wrap_paragraph(words: list[str], font: str, size: float, width_pt: float) -> list[str]:
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = current + [word]
        if current and _width(trial, font, size) > width_pt:
            # Prefer latest soft-break / pre-dialogue point inside current.
            break_at = len(current)
            for index in range(len(current) - 1, 0, -1):
                left = current[:index]
                right = current[index:]
                if _width(left, font, size) > width_pt:
                    continue
                if _ends_soft(left[-1]) or _starts_dialogue(right[0]):
                    break_at = index
                    break
            first, rest = current[:break_at], current[break_at:]
            first, rest = _avoid_hanging(first, rest)
            rest = rest + [word]
            first, rest = _balance_pair(first, rest)
            first, rest = _avoid_hanging(first, rest)
            if first:
                lines.append(" ".join(first))
            current = rest if rest else [word]
            # If current still too wide, hard-flush greedily.
            while len(current) > 1 and _width(current, font, size) > width_pt:
                # Force a break that fits.
                cut = len(current) - 1
                for i in range(len(current) - 1, 0, -1):
                    if _width(current[:i], font, size) <= width_pt:
                        cut = i
                        break
                chunk, current = current[:cut], current[cut:]
                chunk, current = _avoid_hanging(chunk, current)
                if chunk:
                    lines.append(" ".join(chunk))
        else:
            current = trial
    if current:
        if len(current) < 3 and lines:
            prev = lines[-1].split()
            first, rest = _balance_pair(prev, current)
            first, rest = _avoid_hanging(first, rest)
            lines[-1] = " ".join(first)
            current = rest
        if current:
            lines.append(" ".join(current))
    return lines


def wrap_text(
    text: str,
    font: str,
    size: float,
    width_pt: float,
    *,
    honour_newlines: bool = True,
    on_forced_wrap=None,
) -> list[str]:
    """Wrap ``text``. Explicit ``\\n`` in the source are hard breaks when ``honour_newlines``.

    ``on_forced_wrap(original_line)`` is called when a human-approved line still exceeds
    ``width_pt`` and must be wrapped further.
    """
    if honour_newlines and text and ("\n" in text):
        lines: list[str] = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            if _width(words, font, size) <= width_pt:
                lines.append(" ".join(words))
            else:
                if on_forced_wrap:
                    on_forced_wrap(paragraph)
                lines.extend(_wrap_paragraph(words, font, size, width_pt))
        return lines
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        lines.extend(_wrap_paragraph(words, font, size, width_pt))
    return lines


def _sentences(text: str) -> list[str]:
    # Keep explicit newlines out — caller handles those via wrap_text.
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    return [p for p in parts if p]


def fit_lines_for_story(
    text: str,
    font: str,
    size: float,
    width_pt: float,
    *,
    on_forced_wrap=None,
) -> list[str]:
    """Honour explicit newlines; else keep sentences together and phrase-wrap."""
    if text and "\n" in text:
        return wrap_text(
            text, font, size, width_pt, honour_newlines=True, on_forced_wrap=on_forced_wrap
        )
    lines: list[str] = []
    for sentence in _sentences(text) or [text]:
        if pdfmetrics.stringWidth(sentence, font, size) <= width_pt:
            lines.append(sentence)
        else:
            lines.extend(wrap_text(sentence, font, size, width_pt, honour_newlines=False))
    return lines
