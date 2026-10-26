"""Rebuild story prose and page briefs after the operator changes the cast."""

from __future__ import annotations

import json
from pathlib import Path

from .constants import GENDER_DETAILS
from .job_log import append_event, read_json, write_json
from .memory import format_memory_for_prompt
from .openai_util import chat_json
from .humanize import humanize_story_fields
from .pages import compile_pages


STORY_CAST_SYSTEM = """You rewrite a personalized children's picture-book manuscript to match an
APPROVED supporting cast. The operator locked who may appear.

Hard rules:
- Keep the child hero as the main character.
- supporting_characters in your output MUST be exactly the approved list (same names).
- Do NOT mention, describe, or imply anyone/anything removed from the cast
  (no siblings, pets, plush toys, mascots, rabbits, dinosaurs, etc. unless approved).
- Rewrite synopsis, story_beats, and rebuilt_story so the plot still works without
  removed characters. Replace their story roles with the hero and approved supports only.
- Keep English, age-appropriate, warm picture-book prose.
- Preserve the supplied title / subtitle / moral / recurring_outfit unless a tiny
  wording fix is required for grammar after cast removal.
- Return strict JSON with keys:
  title, subtitle, moral, synopsis, recurring_outfit, supporting_characters,
  story_beats, rebuilt_story
"""


def _clear_all_page_art(job_dir: Path, pages: list) -> list[int]:
    cleared = []
    for page in pages:
        number = int(page["number"])
        art = job_dir / "pages" / f"page-{number:02d}" / "art.png"
        composed = job_dir / "print" / "pages" / f"page-{number:02d}.jpg"
        if art.is_file():
            art.unlink()
            cleared.append(number)
        if composed.is_file():
            composed.unlink(missing_ok=True)
    return cleared


def rebuild_story_for_cast(
    client,
    job_dir: Path,
    inputs: dict,
    approved_characters: list,
    removed_names: list[str],
    preserve: dict,
) -> dict:
    current = read_json(job_dir / "story" / "rebuilt_story.json") or {}
    preference_memory = format_memory_for_prompt()
    user = {
        "child_name": inputs["name"],
        "age": inputs["age"],
        "gender": inputs["gender"],
        "pronouns": GENDER_DETAILS[inputs["gender"]],
        "theme": inputs["theme"],
        "purpose": inputs["purpose"],
        "design_rules": inputs.get("design_rules") or "",
        "draft_story": inputs.get("draft_story") or "",
        "approved_supporting_characters": approved_characters,
        "removed_characters": removed_names,
        "preserve": {
            "title": preserve.get("title") or current.get("title"),
            "subtitle": preserve.get("subtitle") or current.get("subtitle") or "",
            "moral": preserve.get("moral") or current.get("moral") or "",
            "recurring_outfit": preserve.get("recurring_outfit")
            or current.get("recurring_outfit")
            or "",
        },
        "previous_story": {
            "synopsis": current.get("synopsis"),
            "story_beats": current.get("story_beats"),
            "rebuilt_story": current.get("rebuilt_story"),
        },
        "preference_memory": preference_memory,
    }
    append_event(
        job_dir,
        "cast_rebuild_story_start",
        f"Rewriting story without: {', '.join(removed_names) or '(none)'}.",
        approved=[c.get("name") for c in approved_characters],
        removed=removed_names,
    )
    raw = chat_json(
        client,
        STORY_CAST_SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Story rebuild for cast change",
    )
    payload = json.loads(raw)
    # Lock cast exactly to operator list
    payload["supporting_characters"] = approved_characters
    for field in ("title", "rebuilt_story", "recurring_outfit", "story_beats"):
        if not payload.get(field):
            raise ValueError(f"Cast story rebuild missing field: {field}")

    write_json(job_dir / "story" / "rebuilt_story.json", payload)
    (job_dir / "story" / "rebuilt_story.txt").write_text(
        payload["rebuilt_story"], encoding="utf-8"
    )
    append_event(
        job_dir,
        "cast_rebuild_story_done",
        f"Story rewritten for approved cast: {[c.get('name') for c in approved_characters]}",
    )
    return humanize_story_fields(client, job_dir, payload)


def rebuild_pages_for_cast(client, job_dir: Path, inputs: dict, story: dict, removed_names: list[str]) -> list:
    """Compile pages with an explicit forbidden-cast instruction injected into story."""
    story_for_pages = {
        **story,
        "_cast_lock": {
            "approved": [c.get("name") for c in (story.get("supporting_characters") or [])],
            "forbidden": removed_names,
            "instruction": (
                "ONLY the hero and approved supporting characters may appear in scene or text. "
                f"Forbidden — do not mention or depict: {', '.join(removed_names) or 'none'}. "
                "No unnamed extra siblings, pets, plush toys, or mascots."
            ),
        },
    }
    append_event(
        job_dir,
        "cast_rebuild_pages_start",
        "Recompiling page briefs for the approved cast.",
    )
    pages = compile_pages(client, job_dir, inputs, story_for_pages)
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
    preserve: dict,
) -> dict:
    story = rebuild_story_for_cast(
        client,
        job_dir,
        inputs,
        approved_characters,
        removed_names,
        preserve,
    )
    old_pages = (read_json(job_dir / "story" / "pages.json") or {}).get("pages") or []
    cleared = _clear_all_page_art(job_dir, old_pages)
    pages = rebuild_pages_for_cast(client, job_dir, inputs, story, removed_names)

    job = read_json(job_dir / "job.json") or {}
    job.update(
        {
            "status": "content_ready",
            "book_title": story.get("title"),
            "preview_ready": False,
            "validation_passed": None,
            "error": None,
            "current_page": 0,
            "total_pages": len(pages),
        }
    )
    write_json(job_dir / "job.json", job)
    validation_path = job_dir / "story" / "validation_report.json"
    if validation_path.is_file():
        validation_path.unlink()

    append_event(
        job_dir,
        "cast_rebuild_done",
        "Story and page briefs rebuilt after cast change. Review, then Preview again.",
        cleared_art_pages=cleared,
    )
    return {"story": story, "pages": pages, "cleared_art_pages": cleared}
