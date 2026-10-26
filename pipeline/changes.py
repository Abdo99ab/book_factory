"""Natural-language change requests against a completed (or partial) book job."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .illustrate import generate_cover_panels, generate_page_image
from .job_log import append_event, read_json, write_json
from .memory import record_change_and_learn
from .openai_util import chat_json
from .pdf_build import build_pdf


SYSTEM = """You are an editor for a personalized children's picture-book production tool.
Given the operator's natural-language request and the current book state, decide concrete actions.

Return JSON:
{
  "summary": "short summary of what you will do",
  "actions": [
    {
      "type": "rewrite_page_text" | "regen_page_art" | "update_page_scene" |
              "regen_cover" | "rewrite_story_title" | "noop",
      "page": number or null,
      "text": "new page text if rewriting",
      "scene": "full updated scene brief when visuals change (REQUIRED for props/outfit)",
      "edit_instruction": "short must-show visual directive for the illustrator",
      "title": "new book title if rewriting title",
      "text_placement": "optional new placement",
      "force_character_lock": false,
      "reason": "why"
    }
  ]
}

Rules:
- Prefer the smallest change that satisfies the request.
- Face/likeness/photo match ONLY (same scene, fix the child's face): regen_page_art
  with force_character_lock=true. Cover too if needed.
- ANY visible prop, accessory, clothing, sunglasses, hat, object, or "what's in the
  picture" change: you MUST use update_page_scene OR regen_page_art WITH a new
  scene string that includes the change (e.g. "Aicha wears kid-sized dark sunglasses").
  Also set edit_instruction to a short must-show line. Bare regen_page_art with
  null scene is NOT enough — the image model never sees the operator request otherwise.
- If the target page is personalized=false but the operator wants the hero face,
  update scene to include the hero and set force_character_lock=true.
- If only wording/spelling, rewrite_page_text (no art regen).
- Composition/background/mood: update_page_scene with full new scene + regen via that type.
- Multiple actions allowed.
"""


def _mentions_face_lock(text: str) -> bool:
    lowered = (text or "").lower()
    needles = (
        "face",
        "likeness",
        "character sheet",
        "person picture",
        "person photo",
        "reference photo",
        "reference image",
        "hero photo",
        "same face",
        "real person",
        "photo as input",
        "picture as input",
    )
    return any(n in lowered for n in needles)


def _edit_instruction_for_action(action: dict, demand: str) -> str | None:
    """Directive that must reach the image model (planner field or full demand)."""
    for key in ("edit_instruction", "visual_note", "instruction"):
        value = (action.get(key) or "").strip()
        if value:
            return value
    demand = (demand or "").strip()
    return demand or None


def _apply_calm_zone(page: dict, action: dict) -> None:
    zone = (action.get("calm_zone") or "").strip().lower()
    if zone in {"left", "right"}:
        page["calm_zone"] = zone
    placement = (action.get("text_placement") or "").strip().lower()
    if placement:
        page["text_placement"] = placement


def _merge_visual_into_scene(scene: str, instruction: str) -> str:
    """Append operator visual change so later regenerations keep it."""
    scene = (scene or "").rstrip()
    instruction = (instruction or "").strip()
    if not instruction:
        return scene
    marker = "REQUIRED VISUAL CHANGE:"
    if marker.lower() in scene.lower() and instruction.lower() in scene.lower():
        return scene
    addition = f"{marker} {instruction}"
    if not scene:
        return addition
    return f"{scene} {addition}"


def _update_job(job_dir: Path, **updates):
    path = job_dir / "job.json"
    job = read_json(path, default={})
    job.update(updates)
    job["updated_at"] = time.time()
    write_json(path, job)
    return job


def _action_label(action: dict) -> str:
    action_type = action.get("type") or "unknown"
    page = action.get("page")
    if page is not None:
        return f"{action_type} · page {page}"
    return str(action_type)


def _set_progress(job_dir: Path, progress: dict):
    write_json(job_dir / "logs" / "change_progress.json", progress)
    _update_job(job_dir, change_progress=progress)


def plan_changes(client, job_dir: Path, demand: str, inputs: dict, story: dict, pages: list) -> dict:
    page_summaries = [
        {
            "number": p["number"],
            "title": p["title"],
            "text": p.get("text"),
            "text_placement": p.get("text_placement"),
            "personalized": p.get("personalized"),
            "scene_excerpt": (p.get("scene") or "")[:280],
        }
        for p in pages
    ]
    user = {
        "demand": demand,
        "book_title": story.get("title"),
        "theme": inputs.get("theme"),
        "child_name": inputs.get("name"),
        "pages": page_summaries,
    }
    append_event(job_dir, "change_plan_start", demand)
    raw = chat_json(
        client,
        SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Change planning",
    )
    plan = json.loads(raw)
    write_json(job_dir / "logs" / "last_change_plan.json", plan)
    append_event(
        job_dir,
        "change_plan_done",
        plan.get("summary") or "Change plan ready.",
        actions=plan.get("actions") or [],
    )
    return plan


def enqueue_change(job_dir: Path, demand: str) -> dict:
    job = read_json(job_dir / "job.json") or {}
    queue = list(job.get("change_queue") or [])
    item = {
        "id": uuid.uuid4().hex[:12],
        "demand": demand,
        "status": "queued",
        "created_at": time.time(),
    }
    queue.append(item)
    _update_job(
        job_dir,
        change_queue=queue,
        change_progress={
            "change_id": item["id"],
            "demand": demand,
            "phase": "queued",
            "summary": "Waiting in queue…",
            "total_actions": 0,
            "completed_actions": 0,
            "current_action": None,
            "queue_position": len(queue),
            "queue_length": len(queue),
            "actions": [],
        },
    )
    append_event(
        job_dir,
        "change_queued",
        demand,
        change_id=item["id"],
        queue_position=len(queue),
    )
    return item


def apply_changes(
    client,
    job_dir: Path,
    demand: str,
    inputs: dict,
    change_id: str | None = None,
    prebuilt_plan: dict | None = None,
):
    story = read_json(job_dir / "story" / "rebuilt_story.json")
    pages_payload = read_json(job_dir / "story" / "pages.json")
    pages = pages_payload["pages"]
    character_path = job_dir / "character" / "character.png"
    if not character_path.is_file():
        raise FileNotFoundError("Character sheet missing; cannot apply changes.")

    change_id = change_id or uuid.uuid4().hex[:12]
    progress = {
        "change_id": change_id,
        "demand": demand,
        "phase": "planning",
        "summary": "Planning changes…",
        "total_actions": 0,
        "completed_actions": 0,
        "current_action": "Planning with AI…" if not prebuilt_plan else "Using self-revise plan…",
        "queue_position": 1,
        "queue_length": 1,
        "actions": [],
    }
    _set_progress(job_dir, progress)
    _update_job(job_dir, status="applying_changes", error=None)

    if prebuilt_plan is not None:
        plan = prebuilt_plan
        append_event(
            job_dir,
            "change_plan_done",
            plan.get("summary") or "Using prebuilt self-revise plan.",
            actions=plan.get("actions") or [],
            prebuilt=True,
        )
    else:
        plan = plan_changes(client, job_dir, demand, inputs, story, pages)
    raw_actions = plan.get("actions") or []
    tracked = []
    for index, action in enumerate(raw_actions):
        tracked.append(
            {
                "index": index,
                "type": action.get("type"),
                "page": action.get("page"),
                "label": _action_label(action),
                "status": "pending",
                "reason": action.get("reason") or "",
            }
        )

    progress.update(
        {
            "phase": "applying",
            "summary": plan.get("summary") or "Applying changes…",
            "total_actions": len(tracked),
            "completed_actions": 0,
            "current_action": tracked[0]["label"] if tracked else "No actions",
            "actions": tracked,
        }
    )
    _set_progress(job_dir, progress)

    applied = []
    need_pdf = False
    need_cover = False

    for index, action in enumerate(raw_actions):
        tracked[index]["status"] = "running"
        progress["current_action"] = tracked[index]["label"]
        progress["completed_actions"] = index
        progress["actions"] = tracked
        _set_progress(job_dir, progress)
        append_event(
            job_dir,
            "change_action_start",
            tracked[index]["label"],
            change_id=change_id,
            index=index,
        )

        action_type = action.get("type")
        page_number = action.get("page")
        reason = action.get("reason") or ""

        try:
            if action_type == "noop":
                applied.append({"type": "noop", "reason": reason})
                tracked[index]["status"] = "done"
            elif action_type == "rewrite_story_title" and action.get("title"):
                story["title"] = action["title"]
                write_json(job_dir / "story" / "rebuilt_story.json", story)
                applied.append(
                    {"type": action_type, "title": action["title"], "reason": reason}
                )
                need_cover = True
                need_pdf = True
                tracked[index]["status"] = "done"
            elif page_number is not None:
                page = next((p for p in pages if p["number"] == int(page_number)), None)
                if page is None:
                    applied.append(
                        {
                            "type": action_type,
                            "page": page_number,
                            "error": "page not found",
                        }
                    )
                    tracked[index]["status"] = "failed"
                elif action_type == "rewrite_page_text" and action.get("text") is not None:
                    page["text"] = action["text"]
                    _apply_calm_zone(page, action)
                    applied.append(
                        {
                            "type": action_type,
                            "page": page["number"],
                            "text": page["text"],
                            "reason": reason,
                        }
                    )
                    need_pdf = True
                    tracked[index]["status"] = "done"
                elif action_type == "update_page_scene" and action.get("scene"):
                    page["scene"] = action["scene"]
                    if action.get("text") is not None:
                        page["text"] = action["text"]
                    _apply_calm_zone(page, action)
                    lock_face = bool(action.get("force_character_lock")) or _mentions_face_lock(
                        f"{demand} {reason}"
                    )
                    if lock_face:
                        page["personalized"] = True
                    edit_instruction = _edit_instruction_for_action(action, demand)
                    generate_page_image(
                        client,
                        job_dir,
                        page,
                        inputs,
                        story,
                        character_path,
                        edit_instruction=edit_instruction,
                    )
                    applied.append(
                        {
                            "type": action_type,
                            "page": page["number"],
                            "scene": page["scene"],
                            "edit_instruction": edit_instruction,
                            "personalized": page.get("personalized"),
                            "reason": reason,
                        }
                    )
                    need_pdf = True
                    tracked[index]["status"] = "done"
                elif action_type == "regen_page_art":
                    if action.get("scene"):
                        page["scene"] = action["scene"]
                    if action.get("text") is not None:
                        page["text"] = action["text"]
                    _apply_calm_zone(page, action)
                    lock_face = bool(action.get("force_character_lock")) or _mentions_face_lock(
                        f"{demand} {reason}"
                    )
                    if lock_face:
                        page["personalized"] = True
                        hero = str(inputs.get("name") or "").strip()
                        scene = page.get("scene") or ""
                        if hero and hero.lower() not in scene.lower():
                            page["scene"] = (
                                f"{scene.rstrip()} "
                                f"Show {hero} clearly in the scene; face, hair, and skin "
                                "must match the locked hero character reference exactly."
                            ).strip()
                    edit_instruction = _edit_instruction_for_action(action, demand)
                    # Persist visual demand into the scene when the planner forgot to.
                    if edit_instruction and not action.get("scene"):
                        page["scene"] = _merge_visual_into_scene(
                            page.get("scene") or "", edit_instruction
                        )
                    generate_page_image(
                        client,
                        job_dir,
                        page,
                        inputs,
                        story,
                        character_path,
                        edit_instruction=edit_instruction,
                    )
                    applied.append(
                        {
                            "type": action_type,
                            "page": page["number"],
                            "scene": page.get("scene"),
                            "edit_instruction": edit_instruction,
                            "personalized": page.get("personalized"),
                            "character_lock": lock_face,
                            "reason": reason,
                        }
                    )
                    need_pdf = True
                    tracked[index]["status"] = "done"
                else:
                    applied.append(
                        {
                            "type": action_type,
                            "page": page_number,
                            "skipped": True,
                            "reason": reason,
                        }
                    )
                    tracked[index]["status"] = "skipped"
            elif action_type == "regen_cover":
                need_cover = True
                applied.append({"type": action_type, "reason": reason})
                need_pdf = True
                tracked[index]["status"] = "done"
            else:
                applied.append(
                    {"type": action_type, "skipped": True, "reason": reason}
                )
                tracked[index]["status"] = "skipped"
        except Exception as error:
            tracked[index]["status"] = "failed"
            tracked[index]["error"] = str(error)
            applied.append(
                {
                    "type": action_type,
                    "page": page_number,
                    "error": str(error),
                }
            )
            append_event(
                job_dir,
                "change_action_failed",
                f"{tracked[index]['label']}: {error}",
                change_id=change_id,
            )
            progress["actions"] = tracked
            progress["completed_actions"] = index + 1
            _set_progress(job_dir, progress)
            raise

        progress["completed_actions"] = index + 1
        progress["actions"] = tracked
        _set_progress(job_dir, progress)
        append_event(
            job_dir,
            "change_action_done",
            tracked[index]["label"],
            change_id=change_id,
            status=tracked[index]["status"],
        )

    write_json(job_dir / "story" / "pages.json", {"pages": pages})

    if need_cover:
        progress["phase"] = "regenerating_cover"
        progress["current_action"] = "Regenerating cover panels…"
        _set_progress(job_dir, progress)
        generate_cover_panels(client, job_dir, inputs, story, character_path)

    if need_pdf or need_cover:
        progress["phase"] = "rebuilding_pdf"
        progress["current_action"] = "Rebuilding PDF…"
        _set_progress(job_dir, progress)
        build_pdf(job_dir, story, pages, inputs)

    progress["phase"] = "learning"
    progress["current_action"] = "Updating preference memory…"
    _set_progress(job_dir, progress)
    record_change_and_learn(client, job_dir, demand, applied, outcome="applied")

    progress.update(
        {
            "phase": "complete",
            "summary": plan.get("summary") or "Changes applied.",
            "current_action": None,
            "completed_actions": len(tracked),
            "actions": tracked,
        }
    )
    _set_progress(job_dir, progress)
    append_event(
        job_dir,
        "change_applied",
        plan.get("summary") or "Changes applied.",
        changes=applied,
        change_id=change_id,
    )
    return {"plan": plan, "applied": applied, "change_id": change_id}


def process_change_queue(client, job_dir: Path, inputs: dict):
    """Drain queued change requests one by one with live progress."""
    while True:
        job = read_json(job_dir / "job.json") or {}
        queue = list(job.get("change_queue") or [])
        pending = [item for item in queue if item.get("status") == "queued"]
        if not pending:
            break

        item = pending[0]
        for entry in queue:
            if entry["id"] == item["id"]:
                entry["status"] = "running"
        remaining = len(pending)
        _update_job(
            job_dir,
            change_queue=queue,
            status="applying_changes",
            change_progress={
                "change_id": item["id"],
                "demand": item["demand"],
                "phase": "queued",
                "summary": f"Starting queued change ({remaining} in queue)…",
                "total_actions": 0,
                "completed_actions": 0,
                "current_action": None,
                "queue_position": 1,
                "queue_length": remaining,
                "actions": [],
            },
        )

        try:
            apply_changes(
                client,
                job_dir,
                item["demand"],
                inputs,
                change_id=item["id"],
            )
            job = read_json(job_dir / "job.json") or {}
            queue = list(job.get("change_queue") or [])
            for entry in queue:
                if entry["id"] == item["id"]:
                    entry["status"] = "complete"
                    entry["finished_at"] = time.time()
            _update_job(job_dir, change_queue=queue, pdf="final_book.pdf", error=None)
        except Exception as error:
            job = read_json(job_dir / "job.json") or {}
            queue = list(job.get("change_queue") or [])
            for entry in queue:
                if entry["id"] == item["id"]:
                    entry["status"] = "failed"
                    entry["error"] = str(error)
                    entry["finished_at"] = time.time()
            progress = job.get("change_progress") or {}
            progress.update(
                {
                    "phase": "failed",
                    "summary": str(error),
                    "current_action": None,
                }
            )
            _update_job(
                job_dir,
                change_queue=queue,
                status="failed",
                error=str(error),
                change_progress=progress,
            )
            append_event(job_dir, "change_failed", str(error), change_id=item["id"])
            raise

    job = read_json(job_dir / "job.json") or {}
    progress = job.get("change_progress") or {}
    if progress.get("phase") != "failed":
        progress = {
            **progress,
            "phase": "complete",
            "queue_length": 0,
            "queue_position": 0,
        }
        _update_job(
            job_dir,
            status="complete",
            pdf="final_book.pdf",
            error=None,
            change_progress=progress,
        )
