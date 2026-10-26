"""Post-generation QA: vision-review composited pages, then apply operator-approved fixes."""

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

ACTION_TYPES = {
    "rewrite_page_text",
    "regen_page_art",
    "update_page_scene",
}

PLACEMENTS = {
    "top",
    "upper_third",
    "middle",
    "lower_third",
    "bottom",
    "left",
    "right",
}

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
            f"Self-revise found {len(issues)} issue(s); proposed {len(actions)} fix(es)."
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


def normalize_action(action: dict) -> dict | None:
    """Validate/normalize one operator-edited action. Returns None if invalid/empty."""
    action_type = (action.get("type") or "").strip()
    if action_type not in ACTION_TYPES:
        return None
    try:
        page = int(action.get("page"))
    except (TypeError, ValueError):
        return None
    if page < 1:
        return None

    text = (action.get("text") or "").strip() or None
    scene = (action.get("scene") or "").strip() or None
    placement = (
        (action.get("calm_zone") or action.get("text_placement") or "")
        .strip()
        .lower()
        or None
    )
    if placement in {"left", "right"}:
        calm_zone = placement
    else:
        calm_zone = None
    if placement and placement not in PLACEMENTS:
        placement = None
    edit_instruction = (action.get("edit_instruction") or "").strip() or None
    reason = (action.get("reason") or "").strip() or "Operator-approved self-revise action"
    force_lock = bool(action.get("force_character_lock"))

    if action_type == "rewrite_page_text" and not text and not placement:
        return None
    if action_type == "update_page_scene" and not scene:
        return None

    result = {
        "type": action_type,
        "page": page,
        "text": text,
        "scene": scene,
        "text_placement": placement,
        "edit_instruction": edit_instruction,
        "force_character_lock": force_lock,
        "reason": reason,
    }
    if calm_zone:
        result["calm_zone"] = calm_zone
    return result


def parse_actions_from_form(form) -> list[dict]:
    """Parse editable action rows from the approval form."""
    types = form.getlist("action_type")
    pages = form.getlist("action_page")
    texts = form.getlist("action_text")
    scenes = form.getlist("action_scene")
    placements = form.getlist("action_text_placement")
    instructions = form.getlist("action_edit_instruction")
    reasons = form.getlist("action_reason")
    locks = form.getlist("action_force_character_lock")

    actions = []
    for index, action_type in enumerate(types):
        lock_raw = locks[index] if index < len(locks) else "false"
        raw = {
            "type": action_type,
            "page": pages[index] if index < len(pages) else None,
            "text": texts[index] if index < len(texts) else None,
            "scene": scenes[index] if index < len(scenes) else None,
            "text_placement": placements[index] if index < len(placements) else None,
            "edit_instruction": instructions[index] if index < len(instructions) else None,
            "reason": reasons[index] if index < len(reasons) else None,
            "force_character_lock": str(lock_raw).lower() in {"on", "true", "1", "yes"},
        }
        normalized = normalize_action(raw)
        if normalized:
            actions.append(normalized)
    return actions


def run_self_revise(client, job_dir: Path, inputs: dict, *, force: bool = False) -> dict:
    """
    Review pages and stop with an editable plan for operator approval.
    Does NOT apply fixes until apply_self_revise_plan is called.
    """
    job = read_json(job_dir / "job.json") or {}
    if not force and (job.get("self_revise_reviewed") or job.get("status") == "self_revise_ready"):
        append_event(
            job_dir,
            "self_revise_skip",
            "Self-revise plan already awaiting approval (or already reviewed once).",
        )
        return {"skipped": True, "reason": "already_reviewed"}

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
                "demand": "Self-revise: review all pages and propose fixes",
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

    job = read_json(job_dir / "job.json") or {}
    job.update(
        {
            "status": "self_revise_ready",
            "self_revise_reviewed": True,
            "self_revise_done": False,
            "error": None,
            "self_revise": {
                "change_id": change_id,
                "phase": "awaiting_approval",
                "summary": plan.get("summary")
                or ("No fixes proposed." if not actions else "Review and approve the plan."),
                "issue_count": len(plan.get("issues") or []),
                "action_count": len(actions),
                "issues": plan.get("issues") or [],
                "actions": actions,
                "demand": demand,
            },
            "change_progress": {
                "change_id": change_id,
                "demand": demand,
                "phase": "awaiting_approval",
                "summary": "Self-revise plan ready — edit actions, then apply.",
                "total_actions": len(actions),
                "completed_actions": 0,
                "current_action": None,
                "queue_position": 0,
                "queue_length": 0,
                "actions": [
                    {
                        "index": i,
                        "type": a.get("type"),
                        "page": a.get("page"),
                        "label": f"{a.get('type')} · page {a.get('page')}",
                        "status": "proposed",
                        "reason": a.get("reason") or "",
                    }
                    for i, a in enumerate(actions)
                ],
            },
        }
    )
    write_json(job_dir / "job.json", job)
    append_event(
        job_dir,
        "self_revise_awaiting_approval",
        plan.get("summary") or "Self-revise plan ready for operator approval.",
        action_count=len(actions),
    )
    return {"plan": plan, "awaiting_approval": True}


def apply_self_revise_plan(
    client,
    job_dir: Path,
    inputs: dict,
    actions: list[dict],
    summary: str | None = None,
) -> dict:
    """Apply operator-approved (possibly edited) self-revise actions."""
    job = read_json(job_dir / "job.json") or {}
    revise = job.get("self_revise") or {}
    change_id = revise.get("change_id") or uuid.uuid4().hex[:12]
    normalized = []
    for action in actions:
        item = normalize_action(action)
        if item:
            normalized.append(item)

    demand = summary or revise.get("demand") or "Self-revise: apply approved fixes"
    if not normalized:
        job.update(
            {
                "status": "complete",
                "self_revise_done": True,
                "self_revise": {
                    **revise,
                    "phase": "complete",
                    "summary": "Operator dismissed the plan — no fixes applied.",
                    "action_count": 0,
                    "actions": [],
                },
                "change_progress": {
                    "change_id": change_id,
                    "demand": demand,
                    "phase": "complete",
                    "summary": "No self-revise actions applied.",
                    "total_actions": 0,
                    "completed_actions": 0,
                    "current_action": None,
                    "actions": [],
                },
            }
        )
        write_json(job_dir / "job.json", job)
        write_json(
            job_dir / "logs" / "self_revise_plan.json",
            {"summary": job["self_revise"]["summary"], "issues": revise.get("issues") or [], "actions": []},
        )
        append_event(job_dir, "self_revise_dismissed", "Operator applied zero actions.")
        return {"plan": {"actions": []}, "applied": [], "dismissed": True}

    plan = {
        "summary": summary or revise.get("summary") or f"Applying {len(normalized)} approved fix(es).",
        "issues": revise.get("issues") or [],
        "actions": normalized,
    }
    write_json(job_dir / "logs" / "self_revise_plan.json", plan)
    write_json(
        job_dir / "logs" / "self_revise_approved_plan.json",
        plan,
    )

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
                "summary": plan["summary"],
                "issue_count": len(plan.get("issues") or []),
                "action_count": len(normalized),
                "issues": plan.get("issues") or [],
                "actions": normalized,
                "demand": demand,
            },
        }
    )
    write_json(job_dir / "job.json", job)
    append_event(
        job_dir,
        "self_revise_complete",
        plan["summary"],
        action_count=len(normalized),
    )
    return result
