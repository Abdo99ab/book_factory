"""Approved books saved as reusable templates (new child photo + name only)."""

from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path

from .constants import TEMPLATE_ROOT
from .job_log import append_event, read_json, write_json


def _match_case(sample: str, replacement: str) -> str:
    if sample.isupper():
        return replacement.upper()
    if sample[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def replace_hero_name(text: str, old_name: str, new_name: str) -> str:
    """Replace hero name (and possessives) while preserving capitalization style."""
    if not isinstance(text, str) or not old_name or not new_name:
        return text
    if old_name.lower() == new_name.lower():
        return text

    poss = re.compile(rf"\b{re.escape(old_name)}'s\b", re.IGNORECASE)

    def poss_repl(match: re.Match) -> str:
        stem = match.group(0)[:-2]
        return f"{_match_case(stem, new_name)}'s"

    text = poss.sub(poss_repl, text)
    plain = re.compile(rf"\b{re.escape(old_name)}\b", re.IGNORECASE)
    return plain.sub(lambda m: _match_case(m.group(0), new_name), text)


def _replace_in_obj(obj, old_name: str, new_name: str):
    if isinstance(obj, str):
        return replace_hero_name(obj, old_name, new_name)
    if isinstance(obj, list):
        return [_replace_in_obj(item, old_name, new_name) for item in obj]
    if isinstance(obj, dict):
        return {key: _replace_in_obj(value, old_name, new_name) for key, value in obj.items()}
    return obj


def personalize_story_and_pages(
    story: dict,
    pages: list,
    old_name: str,
    new_name: str,
) -> tuple[dict, list]:
    return (
        _replace_in_obj(story, old_name, new_name),
        _replace_in_obj(pages, old_name, new_name),
    )


def list_templates(limit: int = 80) -> list[dict]:
    TEMPLATE_ROOT.mkdir(parents=True, exist_ok=True)
    templates = []
    for path in sorted(TEMPLATE_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir():
            continue
        meta = read_json(path / "template.json")
        if meta:
            templates.append(meta)
        if len(templates) >= limit:
            break
    return templates


def template_dir(template_id: str) -> Path:
    return TEMPLATE_ROOT / template_id


def get_template(template_id: str) -> dict | None:
    path = template_dir(template_id) / "template.json"
    if not path.is_file():
        return None
    return read_json(path)


def save_job_as_template(job_dir: Path, *, label: str | None = None) -> dict:
    """Snapshot approved story/pages (no art) so a new child can reuse the book."""
    inputs = read_json(job_dir / "inputs.json") or {}
    job = read_json(job_dir / "job.json") or {}
    story = read_json(job_dir / "story" / "rebuilt_story.json")
    pages_payload = read_json(job_dir / "story" / "pages.json")
    if not story or not pages_payload or not pages_payload.get("pages"):
        raise ValueError("Job has no story/pages to template.")
    if job.get("status") not in {"complete", "self_revise_ready"} and not job.get("pdf"):
        raise ValueError("Only finished books can be saved as templates.")

    source_name = (inputs.get("name") or job.get("name") or "").strip()
    if not source_name:
        raise ValueError("Job is missing the hero name needed for personalization.")

    template_id = uuid.uuid4().hex
    dest = template_dir(template_id)
    if dest.exists():
        raise RuntimeError(f"Template path already exists: {template_id}")

    story_dest = dest / "story"
    story_dest.mkdir(parents=True, exist_ok=True)
    write_json(story_dest / "rebuilt_story.json", story)
    write_json(story_dest / "pages.json", pages_payload)

    title = story.get("title") or job.get("book_title") or "Untitled template"
    meta = {
        "template_id": template_id,
        "label": (label or title).strip() or title,
        "title": title,
        "theme": inputs.get("theme") or job.get("theme"),
        "purpose": inputs.get("purpose") or job.get("purpose"),
        "page_count": inputs.get("page_count") or job.get("page_count"),
        "source_job_id": job.get("job_id") or job_dir.name,
        "source_name": source_name,
        "source_age": inputs.get("age"),
        "source_gender": inputs.get("gender"),
        "design_rules": inputs.get("design_rules") or "",
        "font_id": inputs.get("font_id"),
        "draft_story": inputs.get("draft_story") or "",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    write_json(dest / "template.json", meta)
    write_json(
        dest / "inputs_base.json",
        {
            "theme": meta["theme"],
            "purpose": meta["purpose"],
            "page_count": meta["page_count"],
            "design_rules": meta["design_rules"],
            "font_id": meta["font_id"],
            "draft_story": meta["draft_story"],
            "source_name": source_name,
        },
    )

    job["template_id"] = template_id
    job["is_template_source"] = True
    job["updated_at"] = time.time()
    write_json(job_dir / "job.json", job)
    append_event(
        job_dir,
        "template_saved",
        f"Saved as reusable template {template_id} ({meta['label']}).",
        template_id=template_id,
    )
    return meta


def delete_template(template_id: str) -> None:
    dest = template_dir(template_id)
    if not dest.is_dir():
        raise FileNotFoundError("Template not found.")
    shutil.rmtree(dest)


def materialize_template_into_job(
    template_id: str,
    job_dir: Path,
    *,
    name: str,
    age: str,
    gender: str,
    font_id: str | None = None,
) -> dict:
    """Copy template content into a new job dir with the hero name swapped."""
    meta = get_template(template_id)
    if not meta:
        raise FileNotFoundError("Template not found.")

    src = template_dir(template_id)
    story = read_json(src / "story" / "rebuilt_story.json")
    pages_payload = read_json(src / "story" / "pages.json")
    if not story or not pages_payload:
        raise ValueError("Template content is incomplete.")

    old_name = meta.get("source_name") or ""
    new_name = name.strip()
    story, pages = personalize_story_and_pages(
        story, pages_payload.get("pages") or [], old_name, new_name
    )
    pages_payload = {**pages_payload, "pages": pages}

    base = read_json(src / "inputs_base.json") or {}
    inputs = {
        "name": new_name,
        "age": str(age).strip(),
        "gender": gender,
        "theme": base.get("theme") or meta.get("theme"),
        "purpose": base.get("purpose") or meta.get("purpose"),
        "page_count": int(base.get("page_count") or meta.get("page_count") or len(pages)),
        "draft_story": base.get("draft_story") or meta.get("draft_story") or "",
        "design_rules": base.get("design_rules") or meta.get("design_rules") or "",
        "font_id": font_id or base.get("font_id") or meta.get("font_id"),
        "start_mode": "from_template",
        "from_template_id": template_id,
        "template_source_name": old_name,
    }

    story_dir = job_dir / "story"
    story_dir.mkdir(parents=True, exist_ok=True)
    write_json(story_dir / "rebuilt_story.json", story)
    write_json(story_dir / "pages.json", pages_payload)
    write_json(job_dir / "inputs.json", inputs)
    return {"meta": meta, "inputs": inputs, "story": story, "pages": pages}
