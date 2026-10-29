"""Recurring story characters (Pip, etc.): one sheet per story, reused every order."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from .brand import BRAND_DIR, ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import STORIES_DIR, CatalogueError, get_title, load_locked_story, resolve_route
from .openai_util import image_generate, square_image_size


class StoryCharacterError(RuntimeError):
    """Raised when illustration cannot start because a character sheet is missing/unapproved."""


def story_character_dir(title_id: str) -> Path:
    return STORIES_DIR / title_id


def resolve_sheet_path(entry: dict, title_id: str) -> Path:
    """Resolve ``sheet`` relative to the book_factory root (parent of brand/)."""
    raw = (entry.get("sheet") or "").strip()
    if not raw:
        raise StoryCharacterError(f"{title_id}/{entry.get('id')}: missing sheet path")
    path = Path(raw)
    if not path.is_absolute():
        path = BRAND_DIR.parent / path
    return path


def _default_sheet_rel(title_id: str, character_id: str) -> str:
    return f"brand/stories/{title_id}/{character_id}.png"


def link_character_to_story(entry: dict, title_id: str) -> dict:
    """Stamp ``title_id`` and keep ``sheet`` under that story's folder."""
    out = dict(entry)
    out["title_id"] = title_id
    cid = str(out.get("id") or "character").strip() or "character"
    raw = (out.get("sheet") or "").strip().replace("\\", "/")
    expected_prefix = f"brand/stories/{title_id}/"
    if not raw or not raw.startswith(expected_prefix):
        # Keep filename if present under another path; otherwise default.
        name = Path(raw).name if raw else f"{cid}.png"
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            name = f"{cid}.png"
        out["sheet"] = f"{expected_prefix}{name}"
    return out


def list_story_characters(title_id: str) -> list[dict]:
    locked = load_locked_story(title_id)
    return [link_character_to_story(c, title_id) for c in (locked.get("characters") or [])]


def sync_character_story_links(title_id: str | None = None) -> dict[str, int]:
    """Persist ``title_id`` + sheet path on every character (one story or all). Returns counts."""
    from .catalogue import load_catalogue

    ids = [title_id] if title_id else list(load_catalogue().get("order") or [])
    counts: dict[str, int] = {}
    for tid in ids:
        if not tid:
            continue
        locked = load_locked_story(tid)
        raw = list(locked.get("characters") or [])
        if not raw:
            counts[tid] = 0
            continue
        linked = [link_character_to_story(c, tid) for c in raw]
        if linked != raw:
            _write_locked_characters(tid, linked)
        counts[tid] = len(linked)
    return counts


def characters_for_spread(title_id: str | None, story: dict, n: int) -> list[dict]:
    """Characters whose ``spreads`` list includes ``n`` (approved sheets only when gating later)."""
    tid = title_id or story.get("title_id")
    chars = story.get("characters")
    if chars is None and tid:
        chars = list_story_characters(tid)
    out = []
    for entry in chars or []:
        spreads = entry.get("spreads") or []
        if int(n) in {int(s) for s in spreads}:
            out.append(link_character_to_story(entry, tid) if tid else dict(entry))
    return out


def _entry_aliases(entry: dict) -> set[str]:
    names = {
        str(entry.get("id") or "").lower().replace("_", " "),
        str(entry.get("name") or "").lower(),
    }
    for alias in entry.get("aliases") or []:
        names.add(str(alias).lower())
    names.discard("")
    return names


def characters_named_in_text(story: dict, text: str) -> list[dict]:
    """Characters/props whose name/id/alias appears in cover_art (or other brief text)."""
    hay = (text or "").lower()
    out = []
    for entry in story.get("characters") or []:
        if any(name and name in hay for name in _entry_aliases(entry)):
            out.append(entry)
    return out


def find_recurring_prop_candidates(locked: dict) -> list[dict]:
    """Heuristic: noun-ish props named in art/text/anchor on ≥2 spreads (or title + 1 spread).

    Used to spot missing fixed sheets (egg, curly shell, …). Does not mutate the story.
    """
    import re
    from collections import defaultdict

    # Lightweight prop lexicon for picture-book locks (extend per title family as needed).
    lexicon = (
        "egg",
        "shell",
        "curly shell",
        "spade",
        "nest",
        "biscuit",
        "biscuits",
        "footprint",
        "wellies",
        "jumper",
    )
    hits: dict[str, set[int]] = defaultdict(set)
    title_page = " ".join(str(v) for v in (locked.get("title_page") or {}).values()).lower()
    for term in lexicon:
        if term in title_page:
            hits[term].add(0)  # 0 = title page
    for sp in locked.get("spreads") or []:
        n = int(sp.get("n") or 0)
        blob = " ".join(
            str(sp.get(k) or "") for k in ("text", "art", "anchor", "scene")
        ).lower()
        for term in lexicon:
            if re.search(rf"\b{re.escape(term)}\b", blob) or term in blob:
                hits[term].add(n)
    # Merge shell / curly shell
    if "curly shell" in hits or "shell" in hits:
        combined = hits.get("curly shell", set()) | hits.get("shell", set())
        hits["curly shell"] = combined
        hits.pop("shell", None)
    existing = {
        str(c.get("id") or "").lower().replace("_", " ")
        for c in (locked.get("characters") or [])
    }
    existing |= {
        a.lower()
        for c in (locked.get("characters") or [])
        for a in (c.get("aliases") or [])
    }
    out = []
    for term, spreads in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(spreads) < 2:
            continue
        if term in existing or term.replace(" ", "_") in existing:
            continue
        # Skip outfit tokens already covered by the body sheet.
        if term in {"wellies", "jumper", "biscuit", "biscuits"}:
            continue
        out.append(
            {
                "id": term.replace(" ", "_"),
                "name": term.title() if term != "curly shell" else "Curly shell",
                "spreads": sorted(s for s in spreads if s > 0),
                "also_title_page": 0 in spreads,
                "hit_count": len(spreads),
            }
        )
    return out


def unapproved_or_missing(title_id: str) -> list[dict]:
    """Entries that block illustration: missing file or approved is not true."""
    problems = []
    for entry in list_story_characters(title_id):
        path = resolve_sheet_path(entry, title_id)
        reason = None
        if not path.is_file():
            reason = f"sheet missing: {path}"
        elif not entry.get("approved"):
            reason = "not approved"
        if reason:
            problems.append({**entry, "reason": reason, "sheet_path": str(path)})
    return problems


def assert_characters_ready(title_id: str) -> None:
    problems = unapproved_or_missing(title_id)
    if not problems:
        return
    parts = [
        f"{p.get('name') or p.get('id')} ({p['reason']})"
        for p in problems
    ]
    raise StoryCharacterError(
        f"Story '{title_id}' has unapproved or missing character sheets: "
        + "; ".join(parts)
        + ". Generate and approve them before illustration (see /admin/characters)."
    )


def _write_locked_characters(title_id: str, characters: list[dict]) -> Path:
    path = STORIES_DIR / f"{title_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["characters"] = [link_character_to_story(c, title_id) for c in characters]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Bust catalogue cache if any
    try:
        from .catalogue import load_catalogue

        load_catalogue.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    return path


def generate_character_sheet(client, title_id: str, character_id: str) -> Path:
    """Generate a missing sheet from the character description (route style, blank bg)."""
    locked = load_locked_story(title_id)
    title = get_title(title_id)
    route = resolve_route(title)
    preset = ROUTE_PRESETS[route]
    chars = list(locked.get("characters") or [])
    entry = next((c for c in chars if c.get("id") == character_id), None)
    if not entry:
        raise CatalogueError(f"Unknown character {character_id!r} on {title_id}")
    out = resolve_sheet_path(entry, title_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    desc = (entry.get("description") or "").strip()
    name = entry.get("name") or character_id
    kind = (entry.get("kind") or "character").strip().lower()
    if kind == "prop":
        prompt = (
            f"{preset['preamble']}\n"
            f"Prop / object reference sheet for '{name}' only. Plain blank light background, "
            f"no scenery, no people, no dinosaurs unless the object itself is a shell or egg. "
            f"Show the object clearly from two angles (front/three-quarter and side), large in frame. "
            f"Design: {desc}. "
            f"No text or labels in the image. {SHARED_NEGATIVES}. {preset['negatives']}."
        )
    else:
        prompt = (
            f"{preset['preamble']}\n"
            f"Character reference sheet for '{name}' only. Plain blank light background, "
            f"no scenery, no props, no child. Front view and side/profile view, neutral pose. "
            f"Design: {desc}. "
            f"Show a faint child's outline for scale only if the description mentions size. "
            f"No text or labels in the image. {SHARED_NEGATIVES}. {preset['negatives']}."
        )
    result = image_generate(client, prompt, size=square_image_size(), description=f"Sheet {name}")
    data = getattr(result.data[0], "b64_json", None) if result.data else None
    if not data:
        raise RuntimeError(f"No image data for character sheet {character_id}")
    out.write_bytes(base64.b64decode(data))
    # New art invalidates prior approval.
    for c in chars:
        if c.get("id") == character_id:
            c["approved"] = False
            c.pop("approved_by", None)
            c.pop("approved_at", None)
            break
    _write_locked_characters(title_id, chars)
    return out


def approve_character_sheet(title_id: str, character_id: str, approved_by: str) -> dict:
    locked = load_locked_story(title_id)
    chars = list(locked.get("characters") or [])
    entry = next((c for c in chars if c.get("id") == character_id), None)
    if not entry:
        raise CatalogueError(f"Unknown character {character_id!r} on {title_id}")
    path = resolve_sheet_path(entry, title_id)
    if not path.is_file():
        raise StoryCharacterError(f"Cannot approve {character_id}: sheet file missing at {path}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in chars:
        if c.get("id") == character_id:
            c["approved"] = True
            c["approved_by"] = (approved_by or "operator").strip() or "operator"
            c["approved_at"] = now
            entry = c
            break
    _write_locked_characters(title_id, chars)
    return entry


def ref_paths_for_characters(entries: list[dict], title_id: str) -> list[tuple[dict, Path]]:
    """Return (entry, path) for entries that have a file on disk."""
    out = []
    for entry in entries:
        path = resolve_sheet_path(entry, title_id)
        if path.is_file():
            out.append((entry, path))
    return out
