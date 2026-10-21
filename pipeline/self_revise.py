"""Post-generation QA: vision-review composited pages and apply precise fixes."""

from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path

from PIL import Image

from .changes import apply_changes
from .job_log import append_event, read_json, write_json
from .openai_util import chat_json_vision


BATCH_SIZE = 4
MAX_ACTIONS = 14
VISION_MAX_WIDTH = 540

REVIEW_SYSTEM = """You are a strict QA art director for personalized children's picture books.
You review COMPOSITED pages (illustration + cream text panel already overlaid).

Find real issues that hurt print quality, especially:
- Text panel covering faces, heads, eyes, or important body parts
- Text panel covering critical story props the page is about
- Hero looks like a different child / face badly wrong
- Composition so crowded the text band has no safe empty space
- Missing accessories the page brief clearly requires (if brief is provided)

Return JSON:
{
  "summary": "one sentence",
  "issues": [
    {
      "page": 1,
      "severity": "error" | "warning",
      "problem": "what is wrong",
      "fix": "what to do"
    }
  ],
  "actions": [
    {
      "type": "rewrite_page_text" | "regen_page_art" | "update_page_scene" | "noop",
      "page": 1,
      "text": "only if rewriting text; else null",
      "scene": "full updated scene if recomposing; else null",
      "text_placement": "top|upper_third|middle|lower_third|bottom or null",
      "edit_instruction": "short must-show line for illustrator or null",
      "force_character_lock": false,
      "reason": "why"
    }
  ]
}

Action rules:
- Prefer the smallest fix. For text-over-face/body: FIRST try rewrite_page_text with a
  better text_placement (keep same text) so the panel moves off faces. Only regen art
  if placement cannot clear faces (then update_page_scene with clear negative space in
  the chosen text band + regen_page_art, and set edit_instruction).
- Face wrong: regen_page_art with force_character_lock=true.
- Only emit actions for pages that truly need a fix. Skip clean pages.
- Do not invent story changes. Do not change title/cover unless faces on cover are wrong
  (omit cover here — pages only).
- At most one action per page. Prefer error-severity issues.
"""


def _image_data_url(path: Path, max_width: int = VISION_MAX_WIDTH) -> str:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        if width > max_width:
            rgb = rgb.resize(
                (max_width, max(1, int(height * max_width / width))),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=72, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _page_image_path(job_dir: Path, page: dict) -> Path | None:
    number = int(page["number"])
    composed = job_dir / "print" / "pages" / f"page-{number:02d}.jpg"
    if composed.is_file():
        return composed
    art = job_dir / "pages" / f"page-{number:02d}" / "art.png"
    if art.is_file():
        return art
    return None


def _review_batch(client, job_dir: Path, inputs: dict, story: dict, batch: list[dict]) -> dict:
    content = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "book_title": story.get("title"),
                    "child_name": inputs.get("name"),
                    "theme": inputs.get("theme"),
                    "instruction": (
                        "Review each composited page image below. "
                        "Return issues + precise actions only for pages that need fixes."
                    ),
                    "pages": [
                        {
                            "number": p["number"],
                            "title": p.get("title"),
                            "text": p.get("text"),
                            "text_placement": p.get("text_placement"),
                            "scene": (p.get("scene") or "")[:400],
                            "personalized": p.get("personalized"),
                        }
                        for p in batch
                    ],
                },
                ensure_ascii=True,
            ),
        }
    ]
    for page in batch:
        image_path = _page_image_path(job_dir, page)
        if image_path is None:
            continue
        content.append(
            {
                "type": "text",
                "text": f"Page {page['number']} composited preview:",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(image_path)},
            }
        )

    raw = chat_json_vision(
        client,
        REVIEW_SYSTEM,
        content,
        description=f"Self-revise batch pages {[p['number'] for p in batch]}",
    )
    return json.loads(raw)


def _merge_plans(parts: list[dict]) -> dict:
    issues = []
    actions_by_page: dict[int, dict] = {}
    summaries = []
    for part in parts:
        if part.get("summary"):
            summaries.append(part["summary"])
        issues.extend(part.get("issues") or [])
        for action in part.get("actions") or []:
            if action.get("type") in {None, "noop"}:
                continue
            page = action.get("page")
            if page is None:
                continue
            page = int(page)
            # Prefer regen/update over placement-only if both appear
            previous = actions_by_page.get(page)
            if previous is None:
                actions_by_page[page] = action
                continue
            rank = {
                "update_page_scene": 3,
                "regen_page_art": 2,
                "rewrite_page_text": 1,
            }
            if rank.get(action.get("type"), 0) >= rank.get(previous.get("type"), 0):
                actions_by_page[page] = action

    actions = list(actions_by_page.values())
    actions.sort(key=lambda item: int(item.get("page") or 0))
    actions = actions[:MAX_ACTIONS]
    summary = (
        "; ".join(summaries[:3])
        if summaries
        else (
            f"Self-revise found {len(issues)} issue(s); applying {len(actions)} fix(es)."
            if actions
            else "Self-revise found no material issues."
        )
    )
    return {"summary": summary, "issues": issues, "actions": actions}


def review_book_pages(client, job_dir: Path, inputs: dict, story: dict, pages: list) -> dict:
    """Vision-review all composited pages; return a change-compatible plan."""
    reviewable = [
        page
        for page in pages
        if not page.get("blank") and _page_image_path(job_dir, page) is not None
    ]
    append_event(
        job_dir,
        "self_revise_review_start",
        f"Reviewing {len(reviewable)} composited pages for QA issues.",
    )
    parts = []
    for index in range(0, len(reviewable), BATCH_SIZE):
        batch = reviewable[index : index + BATCH_SIZE]
        try:
            parts.append(_review_batch(client, job_dir, inputs, story, batch))
        except Exception as error:
            append_event(
                job_dir,
                "self_revise_batch_error",
                f"Batch {[p['number'] for p in batch]} failed: {error}",
            )
            raise

    plan = _merge_plans(parts)
    write_json(job_dir / "logs" / "self_revise_plan.json", plan)
    append_event(
        job_dir,
        "self_revise_review_done",
        plan.get("summary") or "Self-revise review finished.",
        issue_count=len(plan.get("issues") or []),
        action_count=len(plan.get("actions") or []),
    )
    return plan


def run_self_revise(client, job_dir: Path, inputs: dict, *, force: bool = False) -> dict:
    """
    Review pages and apply precise fixes.
    Skips if already auto-run once unless force=True (manual button).
    """
    job = read_json(job_dir / "job.json") or {}
    if not force and job.get("self_revise_done"):
        append_event(job_dir, "self_revise_skip", "Self-revise already completed once.")
        return {"skipped": True, "reason": "already_done"}

    story = read_json(job_dir / "story" / "rebuilt_story.json")
    pages_payload = read_json(job_dir / "story" / "pages.json")
    if not story or not pages_payload:
        raise FileNotFoundError("Story/pages missing; cannot self-revise.")

    pages = pages_payload.get("pages") or []
    change_id = uuid.uuid4().hex[:12]
    job.update(
        {
            "status": "self_revising",
            "error": None,
            "self_revise": {
                "change_id": change_id,
                "phase": "reviewing",
                "summary": "AI is reviewing every page for layout and face issues…",
            },
            "change_progress": {
                "change_id": change_id,
                "demand": "Self-revise: review all pages and fix issues",
                "phase": "reviewing",
                "summary": "AI is reviewing every page for layout and face issues…",
                "total_actions": 0,
                "completed_actions": 0,
                "current_action": "Vision QA review…",
                "queue_position": 1,
                "queue_length": 1,
                "actions": [],
            },
        }
    )
    write_json(job_dir / "job.json", job)

    plan = review_book_pages(client, job_dir, inputs, story, pages)
    actions = plan.get("actions") or []
    demand = f"Self-revise: {plan.get('summary') or 'fix page quality issues'}"

    if not actions:
        job = read_json(job_dir / "job.json") or {}
        job.update(
            {
                "status": "complete",
                "self_revise_done": True,
                "self_revise": {
                    "change_id": change_id,
                    "phase": "complete",
                    "summary": plan.get("summary") or "No fixes needed.",
                    "issue_count": len(plan.get("issues") or []),
                    "action_count": 0,
                },
                "change_progress": {
                    "change_id": change_id,
                    "demand": demand,
                    "phase": "complete",
                    "summary": plan.get("summary") or "No fixes needed.",
                    "total_actions": 0,
                    "completed_actions": 0,
                    "current_action": None,
                    "queue_position": 0,
                    "queue_length": 0,
                    "actions": [],
                },
            }
        )
        write_json(job_dir / "job.json", job)
        append_event(job_dir, "self_revise_complete", "No material issues; book left as-is.")
        return {"plan": plan, "applied": [], "skipped_fixes": True}

    result = apply_changes(
        client,
        job_dir,
        demand,
        inputs,
        change_id=change_id,
        prebuilt_plan=plan,
    )

    job = read_json(job_dir / "job.json") or {}
    job.update(
        {
            "status": "complete",
            "self_revise_done": True,
            "self_revise": {
                "change_id": change_id,
                "phase": "complete",
                "summary": plan.get("summary"),
                "issue_count": len(plan.get("issues") or []),
                "action_count": len(actions),
            },
        }
    )
    write_json(job_dir / "job.json", job)
    append_event(
        job_dir,
        "self_revise_complete",
        plan.get("summary") or "Self-revise finished.",
        action_count=len(actions),
    )
    return result
