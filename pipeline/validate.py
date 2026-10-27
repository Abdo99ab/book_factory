"""Validate story spreads, page map, and prompts before expensive image generation.

Raised Ready schema (spec §11, §13): the story is ``{title, locked, spreads,
recurring_outfit, ...}`` — not Magical ``rebuilt_story`` prose.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .brand import READ_IT_AGAIN_LINE
from .catalogue import check_band_grammar, get_title
from .constants import CONTENT_PAGE_COUNT, SPREAD_COUNT
from .job_log import append_event, write_json
from .openai_util import chat_json
from .pages import compile_pages, set_spread_text, spread_text
from .text_guards import (
    SOFT_GUARD_CODES,
    _norm_text,
    check_spreads,
    ensure_catalogue_last_line,
    last_line_present,
)

CALM_ZONES = {"left", "right"}

# Guard codes that are advisory only (everything else from text_guards is an error).
WARNING_GUARD_CODES: set[str] = set(SOFT_GUARD_CODES)

# LLM often flags generic "toys" even when the comfort object is a soft toy — never hard-fail those.
_VAGUE_INVENTED = re.compile(
    r"\b(unspecified\s+)?toys?\b|\bplush(?:es)?\b|\bstuffed\b|\bsoft\s+toys?\b",
    re.IGNORECASE,
)

STRUCTURAL_SYSTEM = """You are a strict QA editor for Raised Ready personalised picture books (UK English).
Validate the 14 story spreads and page briefs BEFORE illustration.

Return JSON:
{
  "passed": boolean,
  "score": 0-100,
  "summary": "one sentence",
  "issues": [
    {
      "severity": "error" | "warning",
      "code": "short_code",
      "message": "what is wrong",
      "page": number or null,
      "fix_hint": "how to fix"
    }
  ],
  "invented_elements": ["named characters/props not grounded in the beats, carer, comfort object or slots"],
  "repair": {
    "needed": boolean,
    "supporting_characters": [{"name":"","description":""}],
    "recurring_outfit": "string or null to leave unchanged",
    "page_fixes": [
      {"number": 2, "scene": "corrected scene", "text": "corrected text or null"}
    ]
  }
}

Hard errors (must set passed=false):
- Named characters, pets or mascots invented and NOT in the beats, carer, comfort object or slots
- The child's name missing or misspelled
- Anything listed under must_not_show appearing in a scene
- Scenes that demand painted letters/titles/captions in the illustration
- Outcome claims, timelines, deficit words about the child, or a stated moral
  (EXCEPT the locked catalogue last_line on spread 14 — see locked_last_line in the user JSON)
- Unsafe / age-inappropriate content

Allowed without error:
- The comfort_object from the order (including soft toys / plush / bunny / blanket)
- Ordinary household props already implied by the beats (fridge, list, shoes, coat, cup, potty, door)
- The locked_last_line on spread 14 (approved brand copy — never a moral / outcome / deficit flag)

Warnings only (passed can still be true):
- Vague "toys" with no new named character
- Minor pacing; text that mentions something the scene does not draw

Never edit the locked block (title, front band, back question, parent spread, dedication).
Never edit or remove locked_last_line from spread 14 text in repair; you may edit the rest of that spread.
repair.page_fixes may only touch story spread pages (2-29).
"""


class ValidationError(RuntimeError):
    """Raised when content fails validation and cannot be auto-repaired."""

    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


# --- must_not_show: catalogue phrases + extra trigger words (spec §8.4) ---------------------------

_MUST_NOT_SHOW_TERMS: dict[str, list[str]] = {
    "deadline or countdown": [r"\bdeadline\b", r"\bcountdown\b"],
    "unhappy adult face at an accident": [r"\b(sad|angry|cross|upset|unhappy)\b.{0,30}\b(face|mum|dad|adult)\b"],
    "child ahead of another": [r"\bahead of\b"],
    "reward chart, sticker or present": [r"\breward chart\b", r"\bsticker", r"\bpresent\b"],
    "nappy shaming": [r"\bshame", r"\bembarrass"],
    "toilet humour making the potty the joke": [r"\bpoo(?:h|p)?\b", r"\bfart"],
    "crying child alone": [r"\bcrying\b.{0,20}\balone\b", r"\balone\b.{0,20}\bcrying\b"],
    "parent looking back sadly through the window": [r"\blooking back\b.{0,30}\bwindow\b"],
    "late parent": [r"\blate (?:mum|dad|parent|carer)\b"],
    "monsters": [r"\bmonsters?\b"],
    "scary dark corners": [r"\bscary\b", r"\bdark corner"],
    "threats": [r"\bthreat"],
    "scary aliens": [r"\balien"],
    "being lost": [r"\blost\b"],
    "fire or crash": [r"\bcrash", r"\bon fire\b", r"\bflames?\b"],
    "predators": [r"\bpredator"],
    "fights": [r"\bfights?\b", r"\bfighting\b"],
    "chase": [r"\bchase[sd]?\b", r"\bchasing\b"],
    "fierce dinosaurs": [r"\bfierce\b", r"\broar(?:ing|s)?\b"],
    "drowning peril": [r"\bdrown"],
    "attacking sharks": [r"\bshark.{0,20}\battack", r"\battack.{0,20}\bshark"],
    "punishment": [r"\bpunish"],
    "angry face": [r"\bangry\b"],
    "time-out": [r"\btime-?out\b", r"\bnaughty step\b"],
    "catastrophe drawn": [r"\bcatastroph"],
    "diagnosis words": [r"\banxiety\b", r"\bdisorder\b", r"\bdiagnos"],
    "lost pet": [r"\blost (?:pet|cat|dog)\b"],
    "arguments": [r"\barguing\b", r"\bargument\b", r"\bshouting\b"],
    "scams": [r"\bscam"],
    "adults losing money": [r"\blos(?:e|es|ing|t) (?:their )?money\b"],
    "named apps or platforms": [r"\b(?:tiktok|instagram|youtube|facebook|snapchat|whatsapp)\b"],
    "deepfakes of real people": [r"\bdeepfake"],
}

_NEGATION = re.compile(r"\b(no|never|not|without|avoid|nor|don't|do not)\b[^.;]{0,30}$", re.IGNORECASE)


def _phrase_pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase.strip().lower()).replace(r"\ ", r"\s+")
    return re.compile(escaped, re.IGNORECASE)


def _hit_without_negation(pattern: re.Pattern, text: str) -> bool:
    for match in pattern.finditer(text or ""):
        before = (text or "")[max(0, match.start() - 40) : match.start()]
        # Only look at the current clause
        before = re.split(r"[.;\n]", before)[-1]
        if _NEGATION.search(before + " "):
            continue
        return True
    return False


def must_not_show_hits(scene: str, phrases: list[str]) -> list[str]:
    """Return catalogue must_not_show phrases that a scene brief appears to depict."""
    hits = []
    for phrase in phrases or []:
        patterns = [_phrase_pattern(phrase)]
        for raw in _MUST_NOT_SHOW_TERMS.get(phrase.strip().lower(), []):
            patterns.append(re.compile(raw, re.IGNORECASE))
        if any(_hit_without_negation(p, scene) for p in patterns):
            hits.append(phrase)
    return hits


# --- scene-vs-text sanity ----------------------------------------------------------------------

_TEXT_NOUN = re.compile(r"\b(?:the|a|an|her|his|their|my)\s+([a-z]{4,})\b", re.IGNORECASE)
_COMMON_WORDS = {
    "day", "time", "moment", "morning", "night", "evening", "afternoon", "today", "tomorrow",
    "step", "turn", "list", "again", "same", "next", "first", "last", "other", "little",
    "with", "that", "this", "then", "when", "just", "very", "more", "some", "good",
    "big", "small", "long", "slow", "quiet", "soft", "warm", "cold", "right", "left",
    "back", "home", "hand", "hands", "face", "feet", "head", "door", "floor", "light",
    "word", "words", "thing", "things", "place", "kind", "sound", "smile", "choice",
    "feeling", "story", "start", "end", "way", "idea", "plan", "rest", "bit", "much",
    "child", "kid", "grown-up", "grownup", "adult", "carer",
}


def _stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ing", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    return w


def scene_text_mismatches(text: str, scene: str, context: str = "") -> list[str]:
    """Concrete nouns in the story text that the scene brief (and cast context) never mention."""
    haystack = f"{scene} {context}".lower()
    hay_stems = {_stem(w) for w in re.findall(r"[a-z']+", haystack)}
    missing = []
    for match in _TEXT_NOUN.finditer(text or ""):
        word = match.group(1).lower()
        if word in _COMMON_WORDS:
            continue
        if _stem(word) in hay_stems or word in haystack:
            continue
        if word not in missing:
            missing.append(word)
    return missing


# --- structural checks -------------------------------------------------------------------------


def _issue(severity: str, code: str, message: str, page=None, fix_hint: str = "") -> dict:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "page": page,
        "fix_hint": fix_hint,
    }


def _expected_locked(title: dict, inputs: dict) -> dict:
    # Lazy import: story.py pulls in humanize / httpx.
    from .story import _build_locked

    return _build_locked(title, inputs)


def _context_text(inputs: dict, title: dict) -> str:
    carer = inputs.get("carer") or {}
    slots = " ".join(str(v) for v in (inputs.get("slots") or {}).values())
    anchor = (title.get("anchor") or {}).get("description") or ""
    return " ".join(
        str(part)
        for part in (
            inputs.get("name"),
            carer.get("name"),
            carer.get("role"),
            inputs.get("comfort_object"),
            inputs.get("home_type"),
            slots,
            anchor,
            title.get("outfit"),
        )
        if part
    )


def _structural_issues(inputs: dict, story: dict, pages: list) -> list:
    issues: list[dict] = []
    name = str(inputs.get("name") or "").strip()

    try:
        title = get_title(inputs["title_id"])
    except Exception as error:
        return [
            _issue(
                "error",
                "unknown_title",
                f"Cannot validate: {error}",
                fix_hint="Re-create the job with a catalogue title_id.",
            )
        ]

    locked = story.get("locked") or {}
    expected_locked = _expected_locked(title, inputs)
    age_band = inputs.get("age_band") or title["age_band"]
    last_line = (title.get("last_line") or "").replace("{name}", name)

    # --- schema: spreads-based, no rebuilt_story required -------------------------------------
    if not (story.get("title") or locked.get("title")):
        issues.append(_issue("error", "missing_title", "Story title is missing.", fix_hint="Use catalogue title_template."))
    if not locked:
        issues.append(_issue("error", "missing_locked", "Story is missing the locked block.", fix_hint="Rebuild story."))
    if not story.get("recurring_outfit"):
        issues.append(_issue("error", "missing_outfit", "Recurring outfit lock is missing.", fix_hint="Use catalogue outfit."))
    spreads = story.get("spreads") or []
    if len(spreads) != SPREAD_COUNT:
        issues.append(
            _issue(
                "error",
                "spread_count",
                f"Expected {SPREAD_COUNT} spreads, found {len(spreads)}.",
                fix_hint="Rebuild story to 14 spreads.",
            )
        )

    # --- band / question / title / parent spread match the catalogue --------------------------
    if locked.get("front_band") != title["band"]:
        issues.append(
            _issue(
                "error",
                "band_mismatch",
                f"Front band {locked.get('front_band')!r} != catalogue {title['band']!r}.",
                fix_hint="Restore locked.front_band from the catalogue.",
            )
        )
    if not check_band_grammar(title["band"], title.get("family") or ""):
        issues.append(
            _issue("error", "band_grammar", f"Band {title['band']!r} breaks band grammar.", fix_hint="Fix catalogue band.")
        )
    if locked.get("back_question") != expected_locked["back_question"]:
        issues.append(
            _issue(
                "error",
                "question_mismatch",
                f"Back question {locked.get('back_question')!r} != {expected_locked['back_question']!r}.",
                fix_hint="Restore locked.back_question from the catalogue.",
            )
        )
    if (story.get("title") or locked.get("title")) != expected_locked["title"] or locked.get("title") != expected_locked["title"]:
        issues.append(
            _issue(
                "error",
                "title_mismatch",
                f"Title must be {expected_locked['title']!r} (catalogue + name).",
                fix_hint="Restore title from catalogue title_template.",
            )
        )
    expected_parent = expected_locked["parent_spread"]
    if locked.get("parent_spread") != expected_parent:
        issues.append(
            _issue(
                "error",
                "parent_spread_changed",
                "Words for the real moment must be byte-identical to the approved catalogue text.",
                fix_hint="Restore locked.parent_spread from the catalogue.",
            )
        )
    if (locked.get("parent_spread") or {}).get("read_it_again") != READ_IT_AGAIN_LINE:
        issues.append(
            _issue("error", "read_it_again_changed", "READ IT AGAIN line must be the fixed brand line.", fix_hint="Restore fixed line.")
        )
    if (locked.get("dedication") or "") != expected_locked["dedication"]:
        issues.append(
            _issue("error", "dedication_changed", "Dedication differs from the order.", fix_hint="Restore from inputs.")
        )
    elif not expected_locked["dedication"]:
        issues.append(
            _issue(
                "warning",
                "dedication_empty",
                "No dedication supplied; page 32 will use the default 'For [Name], from [carer]'.",
                page=32,
                fix_hint="Ask the parent for dedication text.",
            )
        )

    # --- text guards (spec §11.7): the story is its own draft here -----------------------------
    if len(spreads) == SPREAD_COUNT:
        guard_input = {"spreads": spreads, "locked": locked}
        for guard in check_spreads(
            guard_input,
            guard_input,
            expected_locked,
            child_name=name,
            age_band=age_band,
            last_line=last_line,
            require_scenes=True,
        ):
            code = guard.get("code") or "guard"
            spread_n = guard.get("spread")
            issues.append(
                _issue(
                    "warning" if code in WARNING_GUARD_CODES else "error",
                    f"guard_{code}",
                    guard.get("message") or code,
                    page=(2 * spread_n if spread_n else None),
                    fix_hint="Rewrite the spread to satisfy the text guard.",
                )
            )
        total_name = sum(
            len(re.findall(r"\b" + re.escape(name) + r"\b", s.get("text") or "")) for s in spreads
        )
        if name and total_name == 0:
            issues.append(
                _issue("error", "name_missing", f"Child name {name!r} never appears in the story.", fix_hint="Name the child.")
            )
        if name:
            for spread in spreads:
                for match in re.finditer(re.escape(name), spread.get("text") or "", re.IGNORECASE):
                    if match.group(0) != name:
                        issues.append(
                            _issue(
                                "error",
                                "name_spelling",
                                f"Spread {spread.get('n')} spells the name {match.group(0)!r}, expected {name!r}.",
                                page=2 * int(spread.get("n") or 0) or None,
                                fix_hint="Spell the name exactly as entered.",
                            )
                        )

    # --- page map -----------------------------------------------------------------------------
    if len(pages) != CONTENT_PAGE_COUNT:
        issues.append(
            _issue(
                "error",
                "page_count",
                f"Expected {CONTENT_PAGE_COUNT} pages, found {len(pages)}.",
                fix_hint="Recompile pages from the 14 spreads.",
            )
        )
    numbers = [p.get("number") for p in pages]
    if len(numbers) != len(set(numbers)):
        issues.append(_issue("error", "duplicate_pages", "Duplicate page numbers found.", fix_hint="Recompile pages."))
    elif sorted(n for n in numbers if n is not None) != list(range(1, len(pages) + 1)) and len(pages) == CONTENT_PAGE_COUNT:
        issues.append(_issue("error", "page_numbers", "Pages must be numbered 1..32.", fix_hint="Recompile pages."))

    by_number = {p.get("number"): p for p in pages}
    expected_kinds = {1: "title", 30: "parent_left", 31: "parent_right", 32: "dedication"}
    for number, kind in expected_kinds.items():
        page = by_number.get(number)
        if page is not None and page.get("kind") != kind:
            issues.append(
                _issue("error", "page_map", f"Page {number} must be kind '{kind}', found '{page.get('kind')}'.", page=number, fix_hint="Recompile pages.")
            )
    for number in range(2, 30):
        page = by_number.get(number)
        if page is None:
            continue
        spread_n = number // 2
        side = "left" if number % 2 == 0 else "right"
        if page.get("kind") != "spread_half" or page.get("spread") != spread_n or page.get("side") != side:
            issues.append(
                _issue(
                    "error",
                    "page_map",
                    f"Page {number} must be spread {spread_n} ({side} half).",
                    page=number,
                    fix_hint="Recompile pages from the 14 spreads.",
                )
            )

    # parent / dedication page copy vs locked text
    for number, key in ((30, "you_could_say"), (31, "try_together")):
        page = by_number.get(number)
        if page is not None and (page.get("text") or "") != str(expected_parent.get(key) or ""):
            issues.append(
                _issue("error", "parent_spread_changed", f"Page {number} text differs from locked '{key}'.", page=number, fix_hint="Recompile pages.")
            )

    # --- per-spread scene / text / calm zone / anchor / must_not_show ------------------------------
    text_in_art = re.compile(
        r"\b(paint|draw|render|include|show|write)\b.{0,40}\b(text|title|letters?|caption|words?)\b",
        re.I,
    )
    must_not_show = title.get("must_not_show") or []
    schedule = (title.get("anchor") or {}).get("state_schedule")
    context = _context_text(inputs, title)
    for page in pages:
        number = page.get("number")
        if page.get("kind") != "spread_half" or page.get("blank"):
            continue
        spread_n = page.get("spread")
        scene = page.get("scene") or ""
        # Story text lives on the calm-zone half only; the other half is intentionally empty.
        text = spread_text(pages, spread_n) if spread_n else (page.get("text") or "")
        if not scene.strip():
            issues.append(_issue("error", "empty_scene", "Scene description is empty.", page=number, fix_hint="Add a visual scene description."))
        if not text.strip():
            issues.append(_issue("error", "empty_text", "Page story text is empty.", page=number, fix_hint="Add readable story text."))
        if page.get("calm_zone") not in CALM_ZONES:
            issues.append(
                _issue("error", "bad_calm_zone", f"Invalid calm_zone '{page.get('calm_zone')}'.", page=number, fix_hint="Use 'left' or 'right'.")
            )
        if text_in_art.search(scene):
            issues.append(
                _issue("error", "text_in_scene", "Scene asks for painted text/letters in the art.", page=number, fix_hint="Remove text-in-image instructions; text is set in code.")
            )
        for phrase in must_not_show_hits(scene, must_not_show):
            issues.append(
                _issue("error", "must_not_show", f"Spread {spread_n} scene depicts a must-not-show item: {phrase}.", page=number, fix_hint="Rewrite the scene without it.")
            )
        if schedule is not None and spread_n and 1 <= int(spread_n) <= len(schedule):
            if page.get("anchor_state") != int(schedule[int(spread_n) - 1]):
                issues.append(
                    _issue(
                        "error",
                        "anchor_state",
                        f"Spread {spread_n} anchor_state {page.get('anchor_state')} != schedule {schedule[int(spread_n) - 1]}.",
                        page=number,
                        fix_hint="Recompile pages.",
                    )
                )
        if name and page.get("personalized") and name.lower() not in f"{scene} {text}".lower():
            issues.append(
                _issue("warning", "hero_not_named", f"Spread {spread_n} never names {name} in scene or text.", page=number, fix_hint=f"Mention {name}.")
            )
        # Only evaluate once per spread (left half carries the same text/scene as the right)
        if page.get("side") == "left":
            missing = scene_text_mismatches(text, scene, context)
            if missing:
                issues.append(
                    _issue(
                        "warning",
                        "scene_text_mismatch",
                        f"Spread {spread_n} text mentions {', '.join(missing)} but the scene does not draw it.",
                        page=number,
                        fix_hint="Add it to the scene or remove it from the text.",
                    )
                )
    return issues


def _resolved_last_line(inputs: dict) -> str:
    try:
        title = get_title(inputs["title_id"])
    except Exception:
        return ""
    return (title.get("last_line") or "").replace("{name}", str(inputs.get("name") or ""))


def _refers_to_last_line(message: str, last_line: str) -> bool:
    """True when an LLM issue quotes or paraphrases the locked catalogue last_line."""
    if not last_line or not message:
        return False
    msg = _norm_text(message)
    line = _norm_text(last_line)
    if not line:
        return False
    if line in msg:
        return True
    # Significant chunks (handles partial quotes of a two-sentence last_line).
    for chunk in re.split(r"[.!?]+", line):
        chunk = chunk.strip()
        if len(chunk.split()) >= 4 and chunk in msg:
            return True
    return False


def _scrub_repair_preserving_last_line(repair: dict, last_line: str, pages: list) -> dict:
    """Drop spread-14 text rewrites that would remove the locked last_line; keep scene edits."""
    if not repair or not last_line:
        return repair or {"needed": False}
    out = copy.deepcopy(repair)
    by_number = {int(p.get("number") or 0): p for p in pages or []}
    cleaned = []
    for raw in out.get("page_fixes") or []:
        fix = dict(raw)
        num = int(fix.get("number") or 0)
        page = by_number.get(num) or {}
        is_spread_14 = int(page.get("spread") or 0) == 14 or num in {28, 29}
        if is_spread_14 and fix.get("text") is not None:
            if not last_line_present(str(fix.get("text") or ""), last_line):
                fix.pop("text", None)
        if fix.get("scene") or fix.get("text") is not None or fix.get("calm_zone"):
            cleaned.append(fix)
    out["page_fixes"] = cleaned
    if not cleaned and not out.get("supporting_characters") and not out.get("recurring_outfit"):
        out["needed"] = False
    return out


def _llm_validate(client, inputs: dict, story: dict, pages: list) -> dict:
    last_line = _resolved_last_line(inputs)
    spread_pages = [
        {
            "number": p.get("number"),
            "spread": p.get("spread"),
            "scene": p.get("scene"),
            "text": spread_text(pages, p.get("spread")),
            "calm_zone": p.get("calm_zone"),
            "anchor_state": p.get("anchor_state"),
        }
        for p in pages
        if p.get("kind") == "spread_half" and p.get("side") == "left"
    ]
    user = {
        "title_id": inputs.get("title_id"),
        "child_name": inputs.get("name"),
        "age": inputs.get("age"),
        "age_band": inputs.get("age_band"),
        "carer": inputs.get("carer"),
        "comfort_object": inputs.get("comfort_object"),
        "slots": inputs.get("slots"),
        "must_not_show": story.get("must_not_show") or [],
        "locked_last_line": last_line,
        "locked_last_line_rule": (
            "Spread 14 must end with this exact sentence. It is approved brand copy: "
            "never flag it as a moral, outcome claim or deficit, and never edit or remove "
            "it in repair. You may still edit the rest of spread 14."
            if last_line
            else ""
        ),
        "story": {
            "title": story.get("title"),
            "recurring_outfit": story.get("recurring_outfit"),
            "supporting_characters": story.get("supporting_characters") or [],
            "locked": story.get("locked"),
        },
        "spreads": spread_pages,
    }
    raw = chat_json(
        client,
        STRUCTURAL_SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Content validation",
    )
    return json.loads(raw)


def _apply_repair(story: dict, pages: list, repair: dict) -> tuple[dict, list]:
    """Apply LLM repair to spread scenes/text only. NEVER mutates story['locked']."""
    if not repair or not repair.get("needed"):
        return story, pages

    locked_snapshot = copy.deepcopy(story.get("locked"))
    title_snapshot = story.get("title")

    if "supporting_characters" in repair and repair["supporting_characters"] is not None:
        story["supporting_characters"] = repair["supporting_characters"]
    if repair.get("recurring_outfit"):
        story["recurring_outfit"] = repair["recurring_outfit"]

    spreads_by_n = {int(s.get("n") or 0): s for s in story.get("spreads") or []}
    fixes = {
        int(item["number"]): item
        for item in repair.get("page_fixes") or []
        if item.get("number") is not None
    }
    for page in pages:
        fix = fixes.get(int(page["number"]))
        if not fix or page.get("kind") != "spread_half":
            continue  # title, parent spread and dedication are LOCKED
        spread = spreads_by_n.get(int(page.get("spread") or 0))
        for target in [t for t in (page, spread) if t is not None]:
            if fix.get("scene"):
                target["scene"] = fix["scene"]
            if fix.get("text") is not None:
                target["text"] = fix["text"]
        # keep the paired half in sync (text lives on the calm-zone half only)
        for other in pages:
            if other.get("kind") == "spread_half" and other.get("spread") == page.get("spread"):
                if fix.get("scene"):
                    other["scene"] = fix["scene"]
        if fix.get("text") is not None:
            set_spread_text(pages, int(page["spread"]), fix["text"])

    # Defence in depth: whatever happened above, locked content is restored.
    story["locked"] = locked_snapshot
    if title_snapshot is not None:
        story["title"] = title_snapshot
    return story, pages


def _coerce_llm_passed(value) -> bool | None:
    """Parse model passed flag; None if missing/unusable (do not treat string 'false' as True)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "pass", "passed"}:
        return True
    if text in {"false", "no", "0", "fail", "failed"}:
        return False
    return None


def _soften_llm_report(llm_report: dict, inputs: dict, pages: list | None = None) -> dict:
    """Downgrade vague toys and locked last_line false positives; scrub bad repairs."""
    report = dict(llm_report or {})
    comfort = str(inputs.get("comfort_object") or "").strip().lower()
    last_line = _resolved_last_line(inputs)
    softened_issues = []
    for raw in report.get("issues") or []:
        issue = dict(raw)
        severity = str(issue.get("severity") or "warning").strip().lower()
        if severity not in {"error", "warning"}:
            severity = "warning"
        issue["severity"] = severity
        message = str(issue.get("message") or "")
        if severity == "error" and _VAGUE_INVENTED.search(message):
            issue["severity"] = "warning"
            issue["code"] = "vague_prop"
        elif severity == "error" and _refers_to_last_line(message, last_line):
            # Catalogue last_line often sounds like a moral; never block on it.
            issue["severity"] = "warning"
            issue["code"] = "locked_last_line_ok"
        softened_issues.append(issue)

    invented = []
    for item in report.get("invented_elements") or []:
        label = str(item).strip()
        if not label:
            continue
        low = label.lower()
        if comfort and comfort in low:
            continue
        if _VAGUE_INVENTED.search(label) and len(label.split()) <= 4:
            continue  # drop "toys" / "unspecified toys"
        if last_line and _refers_to_last_line(label, last_line):
            continue
        invented.append(label)

    repair = _scrub_repair_preserving_last_line(
        report.get("repair") or {"needed": False},
        last_line,
        pages or [],
    )

    # Final gate is hard issues only. Models often set passed=false with a glowing summary.
    hard = [i for i in softened_issues if i.get("severity") == "error"]
    passed = len(hard) == 0 and len(invented) == 0
    if not passed and not hard and invented:
        # Named invented elements without a structured issue — still fail.
        passed = False

    report["issues"] = softened_issues
    report["invented_elements"] = invented
    report["repair"] = repair
    report["passed"] = passed
    if passed and "toy" in str(report.get("summary") or "").lower():
        report["summary"] = "Content validated (vague prop flags softened)."
    return report


def _merge_report(structural: list, llm_report: dict) -> dict:
    issues = list(structural) + list(llm_report.get("issues") or [])
    hard = [i for i in issues if str(i.get("severity") or "").lower() == "error"]
    invented = list(llm_report.get("invented_elements") or [])
    # Ignore the model's passed bit when it conflicts with hard evidence (common model glitch).
    passed = len(hard) == 0 and len(invented) == 0
    if passed:
        summary = llm_report.get("summary") or "Content validated."
    else:
        summary = (hard[0].get("message") if hard else None) or (
            f"Invented elements: {', '.join(invented)}" if invented else None
        ) or llm_report.get("summary") or "Content validation failed."
    return {
        "passed": passed,
        "score": llm_report.get("score"),
        "summary": summary,
        "issues": issues,
        "invented_elements": invented,
        "repair": llm_report.get("repair") or {"needed": False},
        "llm_passed": _coerce_llm_passed(llm_report.get("passed")),
    }


def _sync_spread_text_onto_pages(pages: list, spreads: list) -> list:
    """Keep page-map halves in sync after forcing locked spread text."""
    by_n = {int(s.get("n") or 0): s for s in spreads or []}
    for page in pages or []:
        if page.get("kind") != "spread_half":
            continue
        spread = by_n.get(int(page.get("spread") or 0))
        if spread and spread.get("text") is not None:
            set_spread_text(pages, int(page["spread"]), spread["text"], page.get("calm_zone"))
            if spread.get("scene") is not None:
                page["scene"] = spread["scene"]
    return pages


def _force_catalogue_last_line(inputs: dict, story: dict, pages: list) -> tuple[dict, list, bool]:
    """Append locked catalogue last_line to spread 14 if the model omitted it."""
    try:
        title = get_title(inputs["title_id"])
    except Exception:
        return story, pages, False
    last_line = (title.get("last_line") or "").replace("{name}", str(inputs.get("name") or ""))
    if not last_line.strip():
        return story, pages, False
    before = story.get("spreads") or []
    ensured = ensure_catalogue_last_line(before, last_line)
    if [s.get("text") for s in ensured] == [s.get("text") for s in before]:
        return story, pages, False
    story = dict(story)
    story["spreads"] = ensured
    pages = _sync_spread_text_onto_pages(list(pages or []), story["spreads"])
    return story, pages, True


def validate_content(client, job_dir: Path, inputs: dict, story: dict, pages: list) -> dict:
    """Validate and optionally auto-repair once. Raises ValidationError if still failing."""
    append_event(job_dir, "validation_start", "Validating story, pages, and prompts before illustration.")

    story, pages, last_line_fixed = _force_catalogue_last_line(inputs, story, pages)
    if last_line_fixed:
        write_json(job_dir / "story" / "rebuilt_story.json", story)
        write_json(job_dir / "story" / "pages.json", {"pages": pages})
        append_event(
            job_dir,
            "validation_last_line_fixed",
            "Appended catalogue last_line to spread 14 before validation.",
        )

    structural = _structural_issues(inputs, story, pages)
    llm_report = _soften_llm_report(
        _llm_validate(client, inputs, story, pages), inputs, pages
    )
    report = _merge_report(structural, llm_report)
    report["attempt"] = 1
    write_json(job_dir / "story" / "validation_report.json", report)
    append_event(
        job_dir,
        "validation_result",
        report["summary"],
        passed=report["passed"],
        issue_count=len(report["issues"]),
        invented_elements=report.get("invented_elements"),
    )

    if report["passed"]:
        append_event(job_dir, "validation_passed", "Content approved for illustration.")
        return report

    repair = report.get("repair") or {}
    if repair.get("needed"):
        append_event(job_dir, "validation_repair_start", "Auto-repairing invented or invalid content.")
        story, pages = _apply_repair(story, pages, repair)
        if not repair.get("page_fixes") and report.get("invented_elements"):
            pages = compile_pages(client, job_dir, inputs, story)
        # Repair must never strip the locked catalogue last_line from spread 14.
        story, pages, restored = _force_catalogue_last_line(inputs, story, pages)
        if restored:
            append_event(
                job_dir,
                "validation_last_line_restored",
                "Re-applied catalogue last_line after LLM repair.",
            )
        write_json(job_dir / "story" / "rebuilt_story.json", story)
        write_json(job_dir / "story" / "pages.json", {"pages": pages})

        structural2 = _structural_issues(inputs, story, pages)
        llm_report2 = _soften_llm_report(
            _llm_validate(client, inputs, story, pages), inputs, pages
        )
        report2 = _merge_report(structural2, llm_report2)
        report2["attempt"] = 2
        report2["repaired_from"] = report
        write_json(job_dir / "story" / "validation_report.json", report2)
        append_event(
            job_dir,
            "validation_result",
            report2["summary"],
            passed=report2["passed"],
            attempt=2,
            issue_count=len(report2["issues"]),
        )
        if report2["passed"]:
            append_event(job_dir, "validation_passed", "Content approved after auto-repair.")
            return report2
        report = report2

    write_json(job_dir / "story" / "validation_report.json", report)
    hard = [i for i in report.get("issues") or [] if str(i.get("severity") or "").lower() == "error"]
    fail_msg = (
        (hard[0].get("message") if hard else None)
        or (
            f"Invented elements: {', '.join(report.get('invented_elements') or [])}"
            if report.get("invented_elements")
            else None
        )
        or report.get("summary")
        or "Content validation failed."
    )
    raise ValidationError(fail_msg, report=report)
