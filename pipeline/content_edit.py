"""Save operator edits to story/pages before preview or full build."""

from __future__ import annotations

import re
from pathlib import Path

from .job_log import append_event, read_json, write_json


ALLOWED_PLACEMENTS = {
    "top",
    "upper_third",
    "middle",
    "lower_third",
    "bottom",
}


def _parse_supporting_characters(form) -> list:
    names = form.getlist("support_name")
    descriptions = form.getlist("support_description")
    characters = []
    for name, description in zip(names, descriptions):
        name = (name or "").strip()
        description = (description or "").strip()
        if not name and not description:
            continue
        if not name:
            continue
        characters.append({"name": name, "description": description or name})
    return characters


def _parse_story_beats(raw: str) -> list:
    beats = []
    for line in (raw or "").splitlines():
        line = line.strip()
        line = re.sub(r"^[-*\d\.\)\s]+", "", line).strip()
        if line:
            beats.append(line)
    return beats


def _clear_page_art(job_dir: Path, page_number: int) -> bool:
    art = job_dir / "pages" / f"page-{page_number:02d}" / "art.png"
    composed = job_dir / "print" / "pages" / f"page-{page_number:02d}.jpg"
    removed = False
    if art.is_file():
        art.unlink()
        removed = True
    if composed.is_file():
        composed.unlink(missing_ok=True)
    return removed


def apply_content_edits(job_dir: Path, form) -> dict:
    story_path = job_dir / "story" / "rebuilt_story.json"
    pages_path = job_dir / "story" / "pages.json"
    story = read_json(story_path)
    pages_payload = read_json(pages_path)
    if not story or not pages_payload:
        raise FileNotFoundError("Story/pages are not ready to edit yet.")

    pages = pages_payload.get("pages") or []
    before_pages = {p["number"]: dict(p) for p in pages}
    previous_names = {
        (c.get("name") or "").strip()
        for c in (story.get("supporting_characters") or [])
        if (c.get("name") or "").strip()
    }

    approved_characters = _parse_supporting_characters(form)
    new_names = {
        (c.get("name") or "").strip()
        for c in approved_characters
        if (c.get("name") or "").strip()
    }
    removed_names = sorted(previous_names - new_names)
    added_names = sorted(new_names - previous_names)
    cast_changed = bool(removed_names or added_names)

    preserve = {
        "title": (form.get("title") or story.get("title") or "").strip(),
        "subtitle": (form.get("subtitle") or "").strip(),
        "moral": (form.get("moral") or "").strip(),
        "recurring_outfit": (form.get("recurring_outfit") or story.get("recurring_outfit") or "").strip(),
    }
    if not preserve["title"]:
        raise ValueError("Title is required.")
    if not preserve["recurring_outfit"]:
        raise ValueError("Locked outfit is required.")

    # Cast change: lock the new cast and rebuild story + pages via AI (async caller).
    if cast_changed:
        story["supporting_characters"] = approved_characters
        story["title"] = preserve["title"]
        story["subtitle"] = preserve["subtitle"]
        story["moral"] = preserve["moral"]
        story["recurring_outfit"] = preserve["recurring_outfit"]
        write_json(story_path, story)

        job = read_json(job_dir / "job.json") or {}
        job.update(
            {
                "status": "rebuilding_content",
                "book_title": story.get("title"),
                "preview_ready": False,
                "validation_passed": None,
                "error": None,
                "cast_rebuild": {
                    "approved": list(new_names),
                    "removed": removed_names,
                    "added": added_names,
                },
            }
        )
        write_json(job_dir / "job.json", job)
        append_event(
            job_dir,
            "cast_change_saved",
            f"Cast changed (removed: {removed_names or 'none'}; added: {added_names or 'none'}). "
            "Queueing full story + page rebuild.",
            removed=removed_names,
            added=added_names,
            approved=list(new_names),
        )
        return {
            "cast_changed": True,
            "needs_rebuild": True,
            "story": story,
            "removed_characters": removed_names,
            "added_characters": added_names,
            "approved_characters": approved_characters,
            "preserve": preserve,
            "invalidated_pages": [],
        }

    # No cast change: normal field/page edits.
    story["title"] = preserve["title"]
    story["subtitle"] = preserve["subtitle"]
    story["moral"] = preserve["moral"]
    story["synopsis"] = (form.get("synopsis") or "").strip()
    story["recurring_outfit"] = preserve["recurring_outfit"]
    story["rebuilt_story"] = (form.get("rebuilt_story") or story.get("rebuilt_story") or "").strip()
    story["supporting_characters"] = approved_characters
    story["story_beats"] = _parse_story_beats(form.get("story_beats") or "")

    if not story["rebuilt_story"]:
        raise ValueError("Rebuilt story text is required.")

    updated_pages = []
    invalidated = []
    for page in pages:
        number = int(page["number"])
        prefix = f"page_{number}_"
        page["title"] = (form.get(prefix + "title") or page.get("title") or "").strip()
        page["scene"] = (form.get(prefix + "scene") or page.get("scene") or "").strip()
        page["text"] = (form.get(prefix + "text") or page.get("text") or "").strip()
        placement = (
            form.get(prefix + "text_placement") or page.get("text_placement") or "bottom"
        ).strip().lower()
        if placement not in ALLOWED_PLACEMENTS:
            placement = "bottom"
        page["text_placement"] = placement
        page["personalized"] = form.get(prefix + "personalized") == "on"
        page["blank"] = form.get(prefix + "blank") == "on"

        if not page["blank"]:
            if not page["scene"]:
                raise ValueError(f"Page {number} scene cannot be empty.")
            if not page["text"]:
                raise ValueError(f"Page {number} text cannot be empty.")

        prior = before_pages.get(number) or {}
        changed = (
            prior.get("scene") != page["scene"]
            or prior.get("text") != page["text"]
            or prior.get("title") != page["title"]
            or prior.get("text_placement") != page["text_placement"]
            or bool(prior.get("personalized")) != bool(page["personalized"])
            or bool(prior.get("blank")) != bool(page["blank"])
        )
        if changed and _clear_page_art(job_dir, number):
            invalidated.append(number)
        updated_pages.append(page)

    write_json(story_path, story)
    write_json(pages_path, {"pages": updated_pages})
    (job_dir / "story" / "rebuilt_story.txt").write_text(
        story.get("rebuilt_story") or "", encoding="utf-8"
    )

    job = read_json(job_dir / "job.json") or {}
    job["book_title"] = story.get("title")
    job["validation_passed"] = None
    validation_path = job_dir / "story" / "validation_report.json"
    if validation_path.is_file():
        validation_path.unlink()
    if invalidated or job.get("preview_ready"):
        job["preview_ready"] = False
        if job.get("status") == "preview_ready":
            job["status"] = "content_ready"
    write_json(job_dir / "job.json", job)

    append_event(
        job_dir,
        "content_edited",
        "Operator saved pre-build content edits.",
        supporting_characters=list(new_names),
        invalidated_pages=invalidated,
    )
    return {
        "cast_changed": False,
        "needs_rebuild": False,
        "story": story,
        "pages": updated_pages,
        "invalidated_pages": invalidated,
        "removed_characters": [],
        "added_characters": [],
        "approved_characters": approved_characters,
    }
