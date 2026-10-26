"""Deterministic story spread guards (spec §11.7)."""

from __future__ import annotations

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


def check_spreads(
    draft: dict,
    candidate: dict,
    locked: dict,
    *,
    child_name: str,
    age_band: str,
    last_line: str,
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
        # The catalogue's approved last line is LOCKED copy; do not grade it against the band.
        graded_text = text.replace(last_line.strip(), "") if last_line and last_line.strip() else text
        grade = _flesch_grade(graded_text) if graded_text.strip() else None
        if grade is not None and grade > fk_limit:
            issues.append(
                {
                    "code": "readability",
                    "spread": n,
                    "message": f"Spread {n} readability grade {grade:.1f} exceeds {fk_limit}.",
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

    if last_line:
        last_text = (cand_spreads[-1].get("text") or "").strip()
        if last_line.strip() not in last_text:
            issues.append(
                {
                    "code": "last_line",
                    "spread": 14,
                    "message": "Final spread must end with the catalogue last_line.",
                }
            )

    return issues
