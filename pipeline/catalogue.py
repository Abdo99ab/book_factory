"""Load Raised Ready title catalogue (spec §2)."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from .brand import BRAND_DIR, default_route_for_age_band

logger = logging.getLogger(__name__)

CATALOGUE_PATH = BRAND_DIR / "catalogue.json"
STORIES_DIR = BRAND_DIR / "stories"

REQUIRED_TITLE_KEYS = (
    "title_id",
    "family",
    "colour",
    "band",
    "question_form",
    "title_template",
    "age_band",
    "route",
    "outfit",
    "anchor",
    "must_not_show",
    "personalisation_slots",
    "blurb",
    "parent_spread",
    "last_line",
    "approved",
)

REQUIRED_PARENT_BLOCKS = (
    "you_could_say",
    "try_together",
    "in_real_life",
    "read_it_again",
)


class CatalogueError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_catalogue() -> dict:
    if not CATALOGUE_PATH.is_file():
        raise CatalogueError(f"Missing catalogue: {CATALOGUE_PATH}")
    data = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    titles = data.get("titles") or {}
    if not titles:
        raise CatalogueError("catalogue.json has no titles")
    for title_id, entry in titles.items():
        _validate_title(title_id, entry)
    return data


def list_titles() -> list[dict]:
    data = load_catalogue()
    return [data["titles"][tid] for tid in data.get("order") or data["titles"].keys()]


def get_title(title_id: str) -> dict:
    data = load_catalogue()
    entry = (data.get("titles") or {}).get(title_id)
    if not entry:
        raise CatalogueError(f"Unknown title_id: {title_id}")
    return entry


def resolve_route(title: dict) -> str:
    route = (title.get("route") or "open").strip().lower()
    if route == "open":
        resolved = default_route_for_age_band(title["age_band"])
        logger.warning(
            "title_id=%s route is open; defaulting to %s for age_band=%s",
            title.get("title_id"),
            resolved,
            title.get("age_band"),
        )
        return resolved
    return route


def locked_story_path(title_id: str) -> Path:
    return STORIES_DIR / f"{title_id}.json"


# Art briefs must stand alone — never lean on the previous spread.
# Do not use bare "still" / "again" (false positives: "stands still", "Evening again", "try again").
RELATIVE_ART_PATTERNS = (
    re.compile(r"\bsame spot\b", re.I),
    re.compile(r"\bsame place\b", re.I),
    re.compile(r"\bsame garden\b", re.I),
    re.compile(r"\bas before\b", re.I),
    re.compile(r"\bstill (?:there|here|at|in the same)\b", re.I),
    re.compile(r"\bback at (?:the )?(?:same )?(?:spot|place|garden|room)\b", re.I),
    re.compile(r"\bthe same (?:spot|place|location|garden|room)\b", re.I),
)


def find_relative_art_refs(text: str) -> list[str]:
    """Return matched relative phrases in an art brief (empty if self-contained)."""
    found: list[str] = []
    for pat in RELATIVE_ART_PATTERNS:
        m = pat.search(text or "")
        if m:
            found.append(m.group(0))
    return found


def load_locked_story(title_id: str) -> dict:
    """Load the FINAL locked story JSON (story 01 format). Required for Book Factory."""
    path = locked_story_path(title_id)
    if not path.is_file():
        raise CatalogueError(
            f"Missing locked story for {title_id}: {path}. "
            "Only FINAL story docs (see brand/stories/00) are loaded — no AI manuscript."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    spreads = data.get("spreads") or []
    if len(spreads) != 14:
        raise CatalogueError(f"title_id={title_id} locked story must have 14 spreads, found {len(spreads)}")
    for sp in spreads:
        if "text" not in sp or "art" not in sp:
            raise CatalogueError(f"title_id={title_id} spread {sp.get('n')} needs text + art")
        if not str(sp.get("setting") or "").strip():
            raise CatalogueError(f"title_id={title_id} spread {sp.get('n')} needs a self-contained setting")
        calm = (sp.get("calm_zone") or "").strip().lower()
        if calm not in {"left", "right"}:
            raise CatalogueError(
                f"title_id={title_id} spread {sp.get('n')} calm_zone must be left|right (got {calm!r})"
            )
        rel = find_relative_art_refs(str(sp.get("art") or ""))
        if rel:
            raise CatalogueError(
                f"title_id={title_id} spread {sp.get('n')} art has relative refs {rel}; "
                "rewrite to a self-contained scene"
            )
    data["title_id"] = title_id
    data["path"] = str(path)
    return data


def load_story_beats(title_id: str) -> dict:
    """Compatibility wrapper: locked JSON → beats-shaped dict for older helpers/tests."""
    locked = load_locked_story(title_id)
    beats = [
        {
            "n": int(sp["n"]),
            "text": sp.get("text") or "",
            "art": sp.get("art") or "",
            "anchor": sp.get("anchor") or "",
            "calm_zone": sp.get("calm_zone"),
            "calm_zone_note": sp.get("calm_zone_note") or "",
            "must_include": list(sp.get("must_include") or []),
        }
        for sp in locked["spreads"]
    ]
    meta = {
        "approved": bool(locked.get("approved")),
        "sets": list(locked.get("sets") or []),
        "carer_signature": locked.get("carer_signature") or "",
        "last_line": locked.get("last_line") or "",
        "title_id": title_id,
        "version": locked.get("version"),
    }
    return {"meta": meta, "beats": beats, "path": locked.get("path"), "locked": locked}


def _story_corpus(locked: dict) -> str:
    parts: list[str] = []
    for key in (
        "title_template",
        "back_question",
        "back_blurb",
        "honesty_line",
        "cover_art",
        "dedication_template",
        "last_line",
        "parent_art",
    ):
        parts.append(str(locked.get(key) or ""))
    for block in (locked.get("title_page") or {}, locked.get("parent_spread") or {}):
        parts.extend(str(v) for v in block.values())
    for sp in locked.get("spreads") or []:
        for key in ("text", "art", "anchor", "calm_zone_note", "scene"):
            parts.append(str(sp.get(key) or ""))
    return "\n".join(parts)


def order_inputs_for_story(locked: dict) -> dict:
    """Which create-form fields this locked story needs (beyond name/age/pronouns/photo/order id).

    Always off (removed from the product): ``home_type``, freeform ``dedication``.
    Detected from placeholders; optional ``order_inputs`` on the JSON overrides.
    """
    corpus = _story_corpus(locked)
    detected = {
        "carer": bool(
            re.search(r"\[Carer\]|\{[Cc]arer\}|\[carer\]", corpus)
            or locked.get("carer_signature")
        ),
        "comfort_object": bool(re.search(r"\[Object\]|\[object\]|\{comfort", corpus)),
        "giver": bool(re.search(r"\[Giver\]|\[giver\]", corpus)),
    }
    override = locked.get("order_inputs") or {}
    for key in ("carer", "comfort_object", "giver"):
        if key in override:
            detected[key] = bool(override[key])
    return detected


def list_factory_titles() -> list[dict]:
    """Titles ready for Book Factory (approved + locked story JSON on disk)."""
    out = []
    for entry in list_titles():
        tid = entry["title_id"]
        if not entry.get("approved"):
            continue
        if not locked_story_path(tid).is_file():
            continue
        locked = load_locked_story(tid)
        enriched = dict(entry)
        enriched["order_inputs"] = order_inputs_for_story(locked)
        # Prefer locked slots when present (story 01 standard).
        if "personalisation_slots" in locked:
            enriched["personalisation_slots"] = locked.get("personalisation_slots") or []
        out.append(enriched)
    return out


def resolve_carer_text(text: str, inputs: dict) -> str:
    """Fill ``{carer}`` in beats / signatures with the order's carer name (else role, else 'the carer')."""
    carer = (inputs or {}).get("carer") or {}
    label = (carer.get("name") or carer.get("role") or "the carer").strip()
    return (text or "").replace("{carer}", label)


def must_include_map(title_id: str) -> dict[int, list[str]]:
    """``{spread_n: [token, ...]}`` from the story beats file (empty dict when none are set)."""
    try:
        beats = load_story_beats(title_id).get("beats") or []
    except CatalogueError:
        return {}
    return {
        int(beat["n"]): list(beat.get("must_include") or [])
        for beat in beats
        if beat.get("must_include")
    }


def beat_text_map(title_id: str) -> dict[int, str]:
    """``{spread_n: beat body}`` for dialogue detection against must_include tokens."""
    try:
        beats = load_story_beats(title_id).get("beats") or []
    except CatalogueError:
        return {}
    return {int(beat["n"]): str(beat.get("text") or "") for beat in beats}


def _validate_title(title_id: str, entry: dict) -> None:
    missing = [k for k in REQUIRED_TITLE_KEYS if k not in entry]
    if missing:
        raise CatalogueError(f"title_id={title_id} missing keys: {missing}")
    if entry.get("title_id") != title_id:
        raise CatalogueError(f"title_id mismatch for {title_id}")
    parent = entry.get("parent_spread") or {}
    for block in REQUIRED_PARENT_BLOCKS:
        if block not in parent or not str(parent[block]).strip():
            raise CatalogueError(f"title_id={title_id} missing parent_spread.{block}")
    blurb = (entry.get("blurb") or "").strip()
    if len(blurb.split()) > 45:
        raise CatalogueError(f"title_id={title_id} blurb exceeds 45 words")
    schedule = (entry.get("anchor") or {}).get("state_schedule")
    if schedule is not None and len(schedule) != 14:
        raise CatalogueError(
            f"title_id={title_id} state_schedule length {len(schedule)} != 14"
        )


def check_band_grammar(band: str, family: str) -> bool:
    """Band grammar: Ready for + step; Ready to + skill; adventures = READY FOR ADVENTURE."""
    b = (band or "").strip().upper()
    if family == "adventures":
        return b == "READY FOR ADVENTURE"
    if b.startswith("READY TO "):
        return True
    if b.startswith("READY FOR "):
        return True
    return False
