"""Save operator edits to the 14 spreads before preview or full build.

Locked copy (title, band, back question, parent spread, dedication) is never editable here.
"""

from __future__ import annotations

import copy
from pathlib import Path

from .catalogue import get_title
from .job_log import append_event, read_json, write_json
from .pages import clear_spread_art, compile_pages
from .text_guards import check_spreads, ensure_catalogue_last_line

CALM_ZONES = {"left", "right"}


def _catalogue_last_line(inputs: dict) -> str:
    if not inputs.get("title_id"):
        return ""
    title = get_title(inputs["title_id"])
    return (title.get("last_line") or "").replace("{name}", inputs.get("name", ""))


def insert_last_line(job_dir: Path) -> dict:
    """One-click fix: put the exact catalogue last_line at the end of spread 14 (replace a near match)."""
    story_path = job_dir / "story" / "rebuilt_story.json"
    story = read_json(story_path)
    inputs = read_json(job_dir / "inputs.json") or {}
    last_line = _catalogue_last_line(inputs)
    if not story or not last_line:
        raise FileNotFoundError("Story is not ready, or the title has no last line.")
    before = [s.get("text") for s in story.get("spreads") or []]
    story["spreads"] = ensure_catalogue_last_line(story.get("spreads") or [], last_line)
    changed = [s.get("text") for s in story["spreads"]] != before
    write_json(story_path, story)
    if changed:
        compile_pages(None, job_dir, inputs, story)
        clear_spread_art(job_dir, 14)
        job = read_json(job_dir / "job.json") or {}
        job["validation_passed"] = None
        write_json(job_dir / "job.json", job)
        validation_path = job_dir / "story" / "validation_report.json"
        if validation_path.is_file():
            validation_path.unlink()
    append_event(
        job_dir, "last_line_inserted", "Catalogue last line set on spread 14.", changed=changed
    )
    return {"changed": changed, "last_line": last_line}


def edit_spread(job_dir: Path, n: int, *, text=None, scene=None, calm_zone=None) -> dict:
    """Inline edit of ONE spread (text / scene / calm zone). Clears its art when the picture brief changes."""
    story_path = job_dir / "story" / "rebuilt_story.json"
    story = read_json(story_path)
    inputs = read_json(job_dir / "inputs.json") or {}
    if not story:
        raise FileNotFoundError("Story is not ready to edit yet.")
    spread = next((s for s in story.get("spreads") or [] if int(s.get("n") or 0) == int(n)), None)
    if spread is None:
        raise ValueError(f"Unknown spread {n}.")
    before = copy.deepcopy(story)
    picture_changed = False
    if text is not None and text.strip():
        spread["text"] = text.strip()
    if scene is not None and scene.strip() and scene.strip() != spread.get("scene"):
        spread["scene"] = scene.strip()
        picture_changed = True
    zone = (calm_zone or "").strip().lower()
    if zone in CALM_ZONES and zone != spread.get("calm_zone"):
        spread["calm_zone"] = zone
        picture_changed = True
    last_line = _catalogue_last_line(inputs)
    if last_line:
        story["spreads"] = ensure_catalogue_last_line(story["spreads"], last_line)
        title = get_title(inputs["title_id"])
        issues = check_spreads(
            {"spreads": before.get("spreads") or [], "locked": before.get("locked") or {}},
            story,
            before.get("locked") or {},
            child_name=inputs.get("name", ""),
            age_band=inputs.get("age_band") or title["age_band"],
            last_line=last_line,
        )
        blocking = [i for i in issues if i["code"] not in {"readability"}]
        if blocking:
            raise ValueError("Edit rejected by text guards: " + "; ".join(i["message"] for i in blocking))
    write_json(story_path, story)
    pages = compile_pages(None, job_dir, inputs, story) if inputs.get("title_id") else []
    if picture_changed:
        clear_spread_art(job_dir, int(n))
    append_event(
        job_dir,
        "spread_edited",
        f"Spread {n} edited.",
        spread=int(n),
        picture_changed=picture_changed,
    )
    return {"story": story, "pages": pages, "picture_changed": picture_changed}


def _parse_supporting_characters(form) -> list:
    names = form.getlist("support_name")
    descriptions = form.getlist("support_description")
    characters = []
    for name, description in zip(names, descriptions):
        name = (name or "").strip()
        description = (description or "").strip()
        if not name:
            continue
        characters.append({"name": name, "description": description or name})
    return characters


def apply_content_edits(job_dir: Path, form) -> dict:
    story_path = job_dir / "story" / "rebuilt_story.json"
    pages_path = job_dir / "story" / "pages.json"
    story = read_json(story_path)
    pages_payload = read_json(pages_path)
    if not story or not pages_payload:
        raise FileNotFoundError("Story/pages are not ready to edit yet.")
    inputs = read_json(job_dir / "inputs.json") or {}

    before = copy.deepcopy(story)
    previous_names = {
        (c.get("name") or "").strip()
        for c in (story.get("supporting_characters") or [])
        if (c.get("name") or "").strip()
    }
    approved_characters = _parse_supporting_characters(form)
    new_names = {c["name"] for c in approved_characters}
    removed_names = sorted(previous_names - new_names)
    added_names = sorted(new_names - previous_names)
    cast_changed = bool(removed_names or added_names)

    outfit = (form.get("recurring_outfit") or story.get("recurring_outfit") or "").strip()
    if not outfit:
        raise ValueError("Locked outfit is required.")

    invalidated = []
    for spread in story.get("spreads") or []:
        n = int(spread["n"])
        text = (form.get(f"spread_{n}_text") or spread.get("text") or "").strip()
        scene = (form.get(f"spread_{n}_scene") or spread.get("scene") or "").strip()
        if not text:
            raise ValueError(f"Spread {n} text cannot be empty.")
        if not scene:
            raise ValueError(f"Spread {n} scene cannot be empty.")
        calm = (form.get(f"spread_{n}_calm_zone") or "").strip().lower()
        prior_calm = spread.get("calm_zone")
        calm = calm if calm in CALM_ZONES else prior_calm
        changed = (
            spread.get("text") != text
            or spread.get("scene") != scene
            or (calm and calm != prior_calm)
            or cast_changed
        )
        spread["text"] = text
        spread["scene"] = scene
        if calm:
            spread["calm_zone"] = calm
        if changed and clear_spread_art(job_dir, n):
            invalidated.append(n)

    story["recurring_outfit"] = outfit
    story["supporting_characters"] = approved_characters
    if inputs.get("title_id"):
        story["spreads"] = ensure_catalogue_last_line(
            story.get("spreads") or [], _catalogue_last_line(inputs)
        )

    # Operator edits go through the same deterministic text guards as generated copy.
    if inputs.get("title_id"):
        title = get_title(inputs["title_id"])
        name = inputs.get("name", "")
        issues = check_spreads(
            {"spreads": before.get("spreads") or [], "locked": before.get("locked") or {}},
            story,
            before.get("locked") or {},
            child_name=name,
            age_band=inputs.get("age_band") or title["age_band"],
            last_line=(title.get("last_line") or "").replace("{name}", name),
        )
        blocking = [i for i in issues if i["code"] not in {"readability"}]
        if blocking:
            raise ValueError("Edit rejected by text guards: " + "; ".join(i["message"] for i in blocking))

    write_json(story_path, story)
    # Recompile the deterministic page map from the edited spreads (no LLM).
    pages = compile_pages(None, job_dir, inputs, story) if inputs.get("title_id") else pages_payload["pages"]

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
    needs_rebuild = bool(removed_names)  # someone left the cast: rewrite the spreads to match
    if needs_rebuild:
        job["status"] = "rebuilding_content"
        job["error"] = None
    write_json(job_dir / "job.json", job)

    append_event(
        job_dir,
        "content_edited",
        "Operator saved pre-build content edits.",
        supporting_characters=list(new_names),
        removed=removed_names,
        added=added_names,
        invalidated_spreads=invalidated,
    )
    return {
        "cast_changed": cast_changed,
        "needs_rebuild": needs_rebuild,
        "story": story,
        "pages": pages,
        "invalidated_pages": [p for n in invalidated for p in (2 * n, 2 * n + 1)],
        "invalidated_spreads": invalidated,
        "removed_characters": removed_names,
        "added_characters": added_names,
        "approved_characters": approved_characters,
    }
