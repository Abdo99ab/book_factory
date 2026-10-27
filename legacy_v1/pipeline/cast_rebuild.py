"""Rewrite spread text/scenes after the operator changes the approved cast."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .catalogue import get_title
from .humanize import humanize_story_fields
from .job_log import append_event, read_json, write_json
from .memory import format_memory_for_prompt
from .openai_util import chat_json
from .pages import clear_spread_art, compile_pages
from .story import with_calm_zones
from .text_guards import check_spreads, ensure_catalogue_last_line

STORY_CAST_SYSTEM = """You rewrite the 14 spreads of a Raised Ready personalised picture book (UK English)
to match an APPROVED cast. The operator locked who may appear.

Hard rules:
- Keep the child as the hero; short sentences, present tense, no stated moral.
- Do NOT mention, describe, or imply anyone/anything removed from the cast.
- Keep the same 14 spreads (n = 1..14) and the same plot beats; do not touch the locked copy.
- The final spread must still end with the supplied last_line.
- No readable text in scenes.
- Each spread is ONE continuous picture. Its "scene" is one paragraph describing the left half and the
  right half of the same moment (use the words "left" and "right"); remove removed cast members from
  it too. Keep each spread's "calm_zone" ("left" or "right") unless the rewrite needs it swapped.
Return strict JSON: {"spreads": [{"n": 1, "text": "...", "scene": "...", "calm_zone": "left"},
... 14 items]}
"""

# Guard codes that cannot apply when the operator deliberately changes the cast.
_CAST_TOLERATED = {"readability", "name_frequency", "word_increase", "new_proper_noun"}


def _clear_all_page_art(job_dir: Path, pages: list | None = None) -> list[int]:
    """Delete every spread's art (+ cached halves, spread.json). Returns the page numbers it covered."""
    cleared = []
    for n in range(1, 15):
        if clear_spread_art(job_dir, n):
            cleared.extend([2 * n, 2 * n + 1])
        else:
            clear_spread_art(job_dir, n)  # still drop a stale spread.json / halves
    return cleared


def rebuild_story_for_cast(
    client,
    job_dir: Path,
    inputs: dict,
    approved_characters: list,
    removed_names: list[str],
    preserve: dict | None = None,
) -> dict:
    current = read_json(job_dir / "story" / "rebuilt_story.json") or {}
    title = get_title(inputs["title_id"])
    name = inputs["name"]
    last_line = (title.get("last_line") or "").replace("{name}", name)
    locked = current.get("locked") or {}
    user = {
        "child_name": name,
        "age": inputs["age"],
        "age_band": inputs.get("age_band") or title["age_band"],
        "approved_supporting_characters": approved_characters,
        "removed_characters": removed_names,
        "last_line": last_line,
        "must_not_show": title.get("must_not_show") or [],
        "current_spreads": [
            {
                "n": s["n"],
                "text": s.get("text"),
                "scene": s.get("scene"),
                "calm_zone": s.get("calm_zone"),
            }
            for s in current.get("spreads") or []
        ],
        "preference_memory": format_memory_for_prompt(),
    }
    append_event(
        job_dir,
        "cast_rebuild_story_start",
        f"Rewriting spreads without: {', '.join(removed_names) or '(none)'}.",
        approved=[c.get("name") for c in approved_characters],
        removed=removed_names,
    )
    raw = chat_json(
        client,
        STORY_CAST_SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Spread rebuild for cast change",
    )
    spreads = json.loads(raw).get("spreads") or []
    previous = {int(s.get("n") or 0): s for s in current.get("spreads") or []}
    for spread in spreads:
        old = previous.get(int(spread.get("n") or 0)) or {}
        if not str(spread.get("calm_zone") or "").strip() and old.get("calm_zone"):
            spread["calm_zone"] = old["calm_zone"]
    # Same defaults + exact last line as a fresh story (the model may drop or re-quote it).
    spreads = ensure_catalogue_last_line(with_calm_zones(spreads), last_line)
    story = copy.deepcopy(current)
    story["spreads"] = spreads
    story["supporting_characters"] = approved_characters
    story["locked"] = locked  # never rewritten

    issues = check_spreads(
        {"spreads": current.get("spreads") or [], "locked": locked},
        story,
        locked,
        child_name=name,
        age_band=inputs.get("age_band") or title["age_band"],
        last_line=last_line,
    )
    blocking = [i for i in issues if i["code"] not in _CAST_TOLERATED]
    if blocking:
        raise ValueError("Cast rebuild failed text guards: " + blocking[0]["message"])

    write_json(job_dir / "story" / "rebuilt_story.json", story)
    append_event(
        job_dir,
        "cast_rebuild_story_done",
        f"Spreads rewritten for approved cast: {[c.get('name') for c in approved_characters]}",
    )
    story = humanize_story_fields(client, job_dir, story)
    # Humanize may re-word the closing line: force the exact catalogue string back on spread 14.
    story["spreads"] = ensure_catalogue_last_line(story.get("spreads") or [], last_line)
    write_json(job_dir / "story" / "rebuilt_story.json", story)
    return story


def rebuild_pages_for_cast(client, job_dir: Path, inputs: dict, story: dict, removed_names: list[str]) -> list:
    """Recompile the deterministic page map from the rewritten spreads."""
    append_event(job_dir, "cast_rebuild_pages_start", "Recompiling page map for the approved cast.")
    pages = compile_pages(client, job_dir, inputs, story)
    append_event(
        job_dir,
        "cast_rebuild_pages_done",
        f"Recompiled {len(pages)} pages after cast change.",
    )
    return pages


def rebuild_content_for_cast(
    client,
    job_dir: Path,
    inputs: dict,
    approved_characters: list,
    removed_names: list[str],
    preserve: dict | None = None,
) -> dict:
    story = rebuild_story_for_cast(
        client,
        job_dir,
        inputs,
        approved_characters,
        removed_names,
        preserve,
    )
    cleared = _clear_all_page_art(job_dir)
    pages = rebuild_pages_for_cast(client, job_dir, inputs, story, removed_names)

    job = read_json(job_dir / "job.json") or {}
    job.update(
        {
            "status": "content_ready",
            "book_title": story.get("title"),
            "preview_ready": False,
            "validation_passed": None,
            "error": None,
            "spreads_done": 0,
        }
    )
    write_json(job_dir / "job.json", job)
    validation_path = job_dir / "story" / "validation_report.json"
    if validation_path.is_file():
        validation_path.unlink()

    append_event(
        job_dir,
        "cast_rebuild_done",
        "Spreads and page map rebuilt after cast change. Review, then Preview again.",
        cleared_art_pages=cleared,
    )
    return {"story": story, "pages": pages, "cleared_art_pages": cleared}
