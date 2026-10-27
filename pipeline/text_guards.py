"""Deterministic story spread guards (spec §11.7)."""

from __future__ import annotations

import difflib
import re
from collections import Counter

from .brand import BANNED_PHRASES, UK_SPELLING, word_cap_for_age_band

try:
    import textstat
except ImportError:  # pragma: no cover
    textstat = None  # type: ignore

RHETORICAL_MARKERS = ("?", "…")


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _proper_nouns(text: str) -> set[str]:
    found = set()
    for match in re.finditer(r"\b([A-Z][a-z]+(?:'[a-z]+)?)\b", text or ""):
        word = match.group(1)
        if word.lower() in {"i"}:
            continue
        found.add(word)
    return found


def _child_name_count(text: str, child_name: str) -> int:
    if not child_name:
        return 0
    pattern = re.compile(r"\b" + re.escape(child_name) + r"\b", re.IGNORECASE)
    return len(pattern.findall(text or ""))


def _banned_hits(text: str) -> list[str]:
    lowered = (text or "").lower()
    hits = []
    for phrase in BANNED_PHRASES:
        p = phrase.lower()
        if " " in p:
            if p in lowered:
                hits.append(phrase)
        else:
            if re.search(rf"\b{re.escape(p)}\b", lowered):
                hits.append(phrase)
    return hits


def _us_spelling_hits(text: str) -> list[str]:
    hits = []
    lowered = (text or "").lower()
    for us_word in UK_SPELLING:
        if re.search(rf"\b{re.escape(us_word)}\b", lowered):
            hits.append(us_word)
    return hits


def _flesch_grade(text: str) -> float | None:
    if not textstat:
        return None
    try:
        return float(textstat.flesch_kincaid_grade(text))
    except Exception:
        return None


def _text_for_readability(text: str, child_name: str, last_line: str) -> str:
    """Neutralize locked/personal tokens that inflate Flesch–Kincaid on short spreads."""
    graded = text or ""
    if last_line and last_line.strip():
        graded = graded.replace(last_line.strip(), "")
    if child_name and child_name.strip():
        graded = re.sub(
            rf"\b{re.escape(child_name.strip())}\b",
            "child",
            graded,
            flags=re.IGNORECASE,
        )
    return graded.strip()


# Soft: logged / shown but must not block story rebuild (FK is noisy on short copy).
SOFT_GUARD_CODES = frozenset({"readability"})


def hard_issues(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("code") not in SOFT_GUARD_CODES]


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


# Curly / typographic quotes -> straight ones; an ellipsis character -> "..." so it can be trimmed.
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2026": "...",
    }
)


def _norm_text(text: str) -> str:
    """Comparison form used by every last-line check.

    Lower-case, curly quotes -> straight, whitespace collapsed, quotes / brackets stripped (an
    apostrophe inside a word, as in "I'm", is kept), trailing ``.`` / ``...`` / ``!`` removed.
    """
    value = (text or "").translate(_QUOTE_MAP)
    value = re.sub(r"[\"()\[\]{}]", "", value)
    value = re.sub(r"(?<![A-Za-z0-9])'|'(?![A-Za-z0-9])", "", value)
    value = _norm_ws(value).lower()
    return value.rstrip(" .!")


def last_line_present(text: str, last_line: str) -> bool:
    """True if the catalogue last_line appears in the spread (quote / case / punctuation tolerant)."""
    needle = _norm_text(last_line)
    if not needle:
        return True
    return needle in _norm_text(text)


def last_line_message(text: str, last_line: str) -> str:
    """Guard message for a missing last line, quoting the last 12 words actually found."""
    found = " ".join((text or "").split()[-12:])
    return f'Spread 14 must end with: "{last_line}". Found: "{found}".'


_SENTENCE_SPLIT = re.compile("([.!?\u2026]+[\"'\u201d\u2019)\\]]*)\\s+")
NEAR_MATCH_RATIO = 0.9


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split((text or "").strip())
    sentences = []
    for index in range(0, len(parts), 2):
        piece = parts[index] + (parts[index + 1] if index + 1 < len(parts) else "")
        if piece.strip():
            sentences.append(piece.strip())
    return sentences


def _best_last_line_window(sentences: list[str], last_line: str) -> tuple[int, int, float] | None:
    """(start, size, ratio) of the run of sentences most like ``last_line`` (later windows win ties)."""
    target = _norm_text(last_line)
    base = max(1, len(_split_sentences(last_line)))
    best: tuple[int, int, float] | None = None
    for size in sorted({base, max(1, base - 1), base + 1}):
        for start in range(0, len(sentences) - size + 1):
            window = " ".join(sentences[start : start + size])
            candidate = _norm_text(window)
            ratio = 1.0 if candidate == target else difflib.SequenceMatcher(None, candidate, target).ratio()
            if best is None or ratio >= best[2]:
                best = (start, size, ratio)
    return best


def ensure_catalogue_last_line(spreads: list, last_line: str) -> list:
    """Force the exact catalogue last_line onto spread 14 (spec section 11 - not LLM luck).

    * a near match (same after ``_norm_text``, or difflib ratio >= 0.9: curly quotes, wrapping quotes,
      "!" for ".", a comma for a full stop) is REPLACED by the exact catalogue string;
    * the line is APPENDED only when nothing similar is there at all;
    * it is never duplicated, so calling this repeatedly is a no-op.
    """
    target = _norm_ws(last_line)
    if not target or not spreads:
        return spreads
    out = []
    for spread in spreads:
        item = dict(spread)
        if int(item.get("n") or 0) == 14:
            text = (item.get("text") or "").strip()
            sentences = _split_sentences(text)
            best = _best_last_line_window(sentences, target) if sentences else None
            if best is not None and best[2] >= NEAR_MATCH_RATIO:
                start, size, _ratio = best
                item["text"] = " ".join(sentences[:start] + [target] + sentences[start + size :])
            elif text and last_line_present(text, target):
                pass  # embedded in a longer sentence: leave it, never duplicate
            else:
                item["text"] = f"{text} {target}".strip() if text else target
        out.append(item)
    return out


def scene_halves_issue(spread: dict) -> dict | None:
    """Guard: the single ``scene`` must describe BOTH halves of the picture ("left" and "right")."""
    n = int(spread.get("n") or spread.get("spread") or 0)
    lowered = (spread.get("scene") or "").lower()
    missing = [word for word in ("left", "right") if not re.search(rf"\b{word}\b", lowered)]
    if not missing:
        return None
    return {
        "code": "scene_halves",
        "spread": n,
        "message": (
            f"Spread {n}: the scene must describe both the left half and the right half of the "
            f"double-page picture (it never mentions: {', '.join(missing)})."
        ),
    }


def scene_halves_issues(spreads: list, *, require: bool = False) -> list[dict]:
    """``scene_halves_issue`` over every spread. An empty scene only fails when ``require`` is set."""
    issues: list[dict] = []
    for spread in spreads or []:
        if not (spread.get("scene") or "").strip():
            if require:
                n = int(spread.get("n") or 0)
                issues.append(
                    {
                        "code": "scene_halves",
                        "spread": n,
                        "message": f"Spread {n} needs a scene describing its left half and its right half.",
                    }
                )
            continue
        issue = scene_halves_issue(spread)
        if issue:
            issues.append(issue)
    return issues


def check_spreads(
    draft: dict,
    candidate: dict,
    locked: dict,
    *,
    child_name: str,
    age_band: str,
    last_line: str,
    require_scenes: bool = False,
) -> list[dict]:
    """Return guard violations; empty list means pass."""
    issues: list[dict] = []

    draft_spreads = draft.get("spreads") or []
    cand_spreads = candidate.get("spreads") or []
    if len(cand_spreads) != 14:
        issues.append(
            {
                "code": "spread_count",
                "spread": None,
                "message": f"Expected 14 spreads, got {len(cand_spreads)}.",
            }
        )
        return issues

    expected_ns = list(range(1, 15))
    actual_ns = [int(s.get("n") or 0) for s in cand_spreads]
    if actual_ns != expected_ns:
        issues.append(
            {
                "code": "spread_sequence",
                "spread": None,
                "message": f"Spread numbers must be 1..14 in order, got {actual_ns}.",
            }
        )

    for key, value in (locked or {}).items():
        if candidate.get("locked", {}).get(key) != value:
            issues.append(
                {
                    "code": "locked_changed",
                    "spread": None,
                    "message": f"Locked field '{key}' was altered.",
                }
            )

    draft_name_total = sum(_child_name_count(s.get("text", ""), child_name) for s in draft_spreads)
    cand_name_total = sum(_child_name_count(s.get("text", ""), child_name) for s in cand_spreads)
    if cand_name_total < draft_name_total:
        issues.append(
            {
                "code": "name_frequency",
                "spread": None,
                "message": f"Child name appears {cand_name_total} times; draft had {draft_name_total}.",
            }
        )

    cap = word_cap_for_age_band(age_band)
    fk_limit = 2.0 if age_band in {"2-3.5", "3-6", "2-5"} else 4.0

    draft_proper: set[str] = set()
    for spread in draft_spreads:
        draft_proper |= _proper_nouns(spread.get("text", ""))

    rhetorical = 0
    for spread in cand_spreads:
        n = int(spread.get("n") or 0)
        text = spread.get("text") or ""
        wc = _word_count(text)
        draft_match = next((d for d in draft_spreads if int(d.get("n") or 0) == n), None)
        draft_wc = _word_count((draft_match or {}).get("text", "")) if draft_match else wc
        if wc > cap:
            issues.append(
                {
                    "code": "word_cap",
                    "spread": n,
                    "message": f"Spread {n} has {wc} words (cap {cap}).",
                }
            )
        if len(draft_spreads) == 14 and draft_wc and wc > draft_wc * 1.2:
            issues.append(
                {
                    "code": "word_increase",
                    "spread": n,
                    "message": f"Spread {n} grew more than 20% vs draft ({wc} vs {draft_wc}).",
                }
            )
        for phrase in _banned_hits(text):
            issues.append(
                {
                    "code": "banned_phrase",
                    "spread": n,
                    "message": f"Spread {n} contains banned phrase '{phrase}'.",
                }
            )
        for us_word in _us_spelling_hits(text):
            issues.append(
                {
                    "code": "us_spelling",
                    "spread": n,
                    "message": f"Spread {n} uses US spelling '{us_word}'.",
                }
            )
        new_proper = _proper_nouns(text) - draft_proper
        if new_proper:
            issues.append(
                {
                    "code": "new_proper_noun",
                    "spread": n,
                    "message": f"Spread {n} introduces new proper noun(s): {', '.join(sorted(new_proper))}.",
                }
            )
        graded_text = _text_for_readability(text, child_name, last_line)
        # Skip FK on tiny fragments — the formula is meaningless under ~8 words.
        grade = _flesch_grade(graded_text) if _word_count(graded_text) >= 8 else None
        if grade is not None and grade > fk_limit:
            issues.append(
                {
                    "code": "readability",
                    "spread": n,
                    "message": f"Spread {n} readability grade {grade:.1f} exceeds {fk_limit}.",
                    "severity": "warning",
                }
            )
        if "?" in text:
            rhetorical += text.count("?")

    if rhetorical > 1:
        issues.append(
            {
                "code": "rhetorical_questions",
                "spread": None,
                "message": f"At most one rhetorical question allowed; found {rhetorical}.",
            }
        )

    for spread in cand_spreads:
        zone = spread.get("calm_zone")
        if zone is not None and zone not in ("left", "right"):
            issues.append(
                {
                    "code": "calm_zone",
                    "spread": int(spread.get("n") or 0),
                    "message": f"Spread {spread.get('n')}: calm_zone must be 'left' or 'right', got {zone!r}.",
                }
            )
    issues.extend(scene_halves_issues(cand_spreads, require=require_scenes))

    if last_line:
        # Prefer explicit n=14; fall back to list order.
        final = next((s for s in cand_spreads if int(s.get("n") or 0) == 14), cand_spreads[-1])
        if not last_line_present(final.get("text") or "", last_line):
            issues.append(
                {
                    "code": "last_line",
                    "spread": 14,
                    "message": last_line_message(final.get("text") or "", last_line),
                }
            )

    return issues
