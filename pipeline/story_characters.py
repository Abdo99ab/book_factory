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


def list_story_characters(title_id: str) -> list[dict]:
    locked = load_locked_story(title_id)
    return list(locked.get("characters") or [])


def characters_for_spread(title_id: str | None, story: dict, n: int) -> list[dict]:
    """Characters whose ``spreads`` list includes ``n`` (approved sheets only when gating later)."""
    chars = story.get("characters")
    if chars is None and title_id:
        chars = list_story_characters(title_id)
    out = []
    for entry in chars or []:
        spreads = entry.get("spreads") or []
        if int(n) in {int(s) for s in spreads}:
            out.append(entry)
    return out


def characters_named_in_text(story: dict, text: str) -> list[dict]:
    """Characters whose name/id appears in cover_art (or other brief text)."""
    hay = (text or "").lower()
    out = []
    for entry in story.get("characters") or []:
        names = {
            str(entry.get("id") or "").lower().replace("_", " "),
            str(entry.get("name") or "").lower(),
        }
        names.discard("")
        if any(name and name in hay for name in names):
            out.append(entry)
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
    data["characters"] = characters
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
