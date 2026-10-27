"""Post-generation QA: vision-review the composed SPREADS, then apply operator-approved fixes."""

from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path

from PIL import Image

from .changes import SPREAD_ACTIONS, action_spread, apply_changes, canonical_action_type
from .job_log import append_event, read_json, write_json
from .openai_util import chat_json_vision
from .pages import spread_art_path, spread_halves, spread_text


BATCH_SIZE = 3
MAX_ACTIONS = 14
VISION_MAX_WIDTH = 900  # a spread is ~2:1, so it gets more width than a single page did

# Canonical names; the old per-page names are accepted as aliases by ``canonical_action_type``.
ACTION_TYPES = set(SPREAD_ACTIONS)

CALM_ZONES = {"left", "right"}

REVIEW_SYSTEM = """You are a strict QA art director for personalized children's picture books.
You review COMPOSED SPREADS (one wide double-page illustration + the live text layer, rendered from
the print PDF). The fold runs down the exact centre of each image. You are also given the child's
reference photo and the approved character sheet: compare each spread's child against them.

Find real issues that hurt print quality, especially:
- Text panel covering faces, heads, eyes, or important body parts
- Text panel covering critical story props the spread is about
- Hero looks like a different child / face badly wrong
- A face, hands or a key prop crossing the centre fold, or sitting right on an outer edge
- Composition so crowded the text band has no safe empty space
- Missing accessories the spread brief clearly requires (if brief is provided)
- Artefacts: extra or missing fingers, melted hands, garbled glyphs inside the art, duplicated limbs
- The anchor object (if the brief names one) shows the wrong state (e.g. wrong number of ticks)

Return JSON:
{
  "summary": "one sentence",
  "issues": [
    {
      "spread": 1,
      "severity": "error" | "warning",
      "problem": "what is wrong",
      "fix": "what to do"
    }
  ],
  "records": [
    {
      "spread": 1,
      "faces": [{"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}],
      "anchor_state_seen": null,
      "likeness": "good" | "drift" | "wrong",
      "artefacts": ["short description"]
    }
  ],
  "actions": [
    {
      "type": "rewrite_spread_text" | "regen_spread_art" | "update_spread_scene" | "noop",
      "spread": 1,
      "text": "only if rewriting text; else null",
      "scene": "full updated scene (left half AND right half) if recomposing; else null",
      "calm_zone": "left|right or null",
      "edit_instruction": "short must-show line for illustrator or null",
      "force_character_lock": false,
      "reason": "why"
    }
  ]
}

Records: one entry for EVERY spread shown. faces are normalised 0-1 boxes (x,y = top-left corner,
fraction of the WHOLE spread's width/height, so the fold is at x = 0.5). anchor_state_seen is the
integer state of the anchor object (e.g. number of ticks) or null when it is not visible. artefacts
is [] when clean.

Action rules:
- Prefer the smallest fix. For text-over-face/body: FIRST try rewrite_spread_text with the
  other calm_zone only if the picture already has a quiet area there; otherwise update_spread_scene
  with clear negative space in the calm half + regen_spread_art, and set edit_instruction.
- Face wrong: regen_spread_art with force_character_lock=true.
- Face on the fold: regen_spread_art with an edit_instruction that moves it clear of the centre.
- Only emit actions for spreads that truly need a fix. Skip clean spreads.
- Do not invent story changes. Do not change title/cover unless faces on cover are wrong
  (omit cover here — spreads only).
- At most one action per spread. Prefer error-severity issues.
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


def _reference_content(job_dir: Path) -> list[dict]:
    """Reference photo + approved character sheet, once per batch, so likeness can be judged."""
    parts: list[dict] = []
    candidates = sorted((job_dir / "character").glob("reference.*"))[:1] + [job_dir / "character" / "character.png"]
    labels = ["Child's reference photo:", "Approved character sheet:"]
    for label, path in zip(labels, candidates):
        if path.is_file():
            parts.append({"type": "text", "text": label})
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
    return parts


def _spread_image_path(job_dir: Path, n: int) -> Path | None:
    """The composed spread (text layer included) if the PDF was built, else the raw art."""
    composed = job_dir / "print" / "spreads" / f"spread-{int(n):02d}.jpg"
    if composed.is_file():
        return composed
    art = spread_art_path(job_dir, n)
    return art if art.is_file() else None


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
                        "Review each composed spread image below. "
                        "Return issues + precise actions only for spreads that need fixes."
                    ),
                    "spreads": [
                        {
                            "spread": p["spread"],
                            "pages": [2 * int(p["spread"]), 2 * int(p["spread"]) + 1],
                            "text": p.get("text"),
                            "calm_zone": p.get("calm_zone"),
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
    content.extend(_reference_content(job_dir))
    for item in batch:
        image_path = _spread_image_path(job_dir, item["spread"])
        if image_path is None:
            continue
        content.append({"type": "text", "text": f"Spread {item['spread']} composed preview:"})
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(image_path)}})

    raw = chat_json_vision(
        client,
        REVIEW_SYSTEM,
        content,
        description=f"Self-revise batch spreads {[p['spread'] for p in batch]}",
    )
    return json.loads(raw)


def _spread_key(item: dict) -> int | None:
    """Spread an issue / record / action refers to (``spread``, else the spread of ``page``)."""
    try:
        return action_spread(item)
    except Exception:  # noqa: BLE001
        return None


def _merge_plans(parts: list[dict]) -> dict:
    issues = []
    records: dict[str, dict] = {}
    actions_by_spread: dict[int, dict] = {}
    summaries = []
    for part in parts:
        if part.get("summary"):
            summaries.append(part["summary"])
        for issue in part.get("issues") or []:
            issue = dict(issue)
            spread = _spread_key(issue)
            if spread is not None:
                issue["spread"] = spread
            issues.append(issue)
        for record in part.get("records") or []:
            spread = _spread_key(record)
            if spread is None:
                continue
            records[str(spread)] = {**record, "spread": spread}
        for action in part.get("actions") or []:
            action = dict(action)
            action["type"] = canonical_action_type(action.get("type"))
            if action.get("type") in {None, "noop"}:
                continue
            spread = _spread_key(action)
            if spread is None:
                continue
            action["spread"] = spread
            previous = actions_by_spread.get(spread)
            if previous is None:
                actions_by_spread[spread] = action
                continue
            rank = {
                "update_spread_scene": 3,
                "regen_spread_art": 2,
                "rewrite_spread_text": 1,
            }
            if rank.get(action.get("type"), 0) >= rank.get(previous.get("type"), 0):
                actions_by_spread[spread] = action

    actions = list(actions_by_spread.values())
    actions.sort(key=lambda item: int(item.get("spread") or 0))
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
    return {"summary": summary, "issues": issues, "actions": actions, "records": records}


def _add_fold_actions(job_dir: Path, plan: dict) -> None:
    """Deterministic: a face still on the fold / edge after the one in-pipeline retry -> propose a regen."""
    from .qa import spread_layout_issues

    taken = {int(a.get("spread") or 0) for a in plan.get("actions") or []}
    for issue in spread_layout_issues(job_dir):
        if issue["code"] != "fold_warning":
            continue
        n = int(issue["spread"])
        plan.setdefault("issues", []).append(
            {
                "spread": n,
                "severity": "warning",
                "problem": issue["message"],
                "fix": "Regenerate the spread with every face well clear of the fold.",
                "code": "fold_warning",
            }
        )
        if n in taken:
            continue
        taken.add(n)
        plan.setdefault("actions", []).append(
            {
                "type": "regen_spread_art",
                "spread": n,
                "reason": f"fold_warning: {issue['message']}",
                "edit_instruction": (
                    "Keep every face, hand and key prop well clear of the centre fold and the outer "
                    "edges: " + ", ".join(issue.get("zones") or [])
                ),
                "force_character_lock": False,
                "scene": None,
                "text": None,
            }
        )
    plan["actions"] = sorted(plan.get("actions") or [], key=lambda a: int(a.get("spread") or 0))[:MAX_ACTIONS]


def review_book_pages(client, job_dir: Path, inputs: dict, story: dict, pages: list) -> dict:
    """Vision-review every composed spread; return a change-compatible plan."""
    reviewable = []
    for page in pages:
        if page.get("kind") != "spread_half" or page.get("side") != "left" or page.get("blank"):
            continue
        n = int(page["spread"])
        if _spread_image_path(job_dir, n) is None:
            continue
        reviewable.append({**page, "text": spread_text(pages, n)})
    append_event(
        job_dir,
        "self_revise_review_start",
        f"Reviewing {len(reviewable)} composed spreads for QA issues.",
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
                f"Batch {[p['spread'] for p in batch]} failed: {error}",
            )
            raise

    plan = _merge_plans(parts)
    _add_fold_actions(job_dir, plan)
    # review_record feeds qa.anchor_state / qa.face_position (and the human-check panel).
    (job_dir / "qa").mkdir(parents=True, exist_ok=True)
    write_json(job_dir / "qa" / "review_record.json", {"spreads": plan.get("records") or {}})
    for spread, record in (plan.get("records") or {}).items():
        for artefact in record.get("artefacts") or []:
            plan["issues"].append(
                {"spread": int(spread), "severity": "error", "problem": f"Artefact: {artefact}", "fix": "Regenerate the spread art."}
            )
        if record.get("likeness") in {"drift", "wrong"}:
            plan["issues"].append(
                {
                    "spread": int(spread),
                    "severity": "error" if record["likeness"] == "wrong" else "warning",
                    "problem": f"Likeness {record['likeness']} against the reference photo.",
                    "fix": "Regenerate with the character lock.",
                }
            )
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
    """Validate/normalize one operator-edited action. Returns None if invalid/empty.

    Old per-page names (``rewrite_page_text`` / ``regen_page_art`` / ``update_page_scene``) are accepted
    as aliases, and a ``page`` (2-29) is mapped to its spread.
    """
    action_type = canonical_action_type((action.get("type") or "").strip())
    if action_type not in ACTION_TYPES:
        return None
    spread = action_spread(action)
    if spread is None:
        return None

    text = (action.get("text") or "").strip() or None
    scene = (action.get("scene") or "").strip() or None
    zone = (action.get("calm_zone") or action.get("text_placement") or "").strip().lower() or None
    calm_zone = zone if zone in CALM_ZONES else None
    edit_instruction = (action.get("edit_instruction") or "").strip() or None
    reason = (action.get("reason") or "").strip() or "Operator-approved self-revise action"
    force_lock = bool(action.get("force_character_lock"))

    if action_type == "rewrite_spread_text" and not text and not calm_zone:
        return None
    if action_type == "update_spread_scene" and not scene:
        return None

    result = {
        "type": action_type,
        "spread": spread,
        "text": text,
        "scene": scene,
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
    spreads = form.getlist("action_spread") or form.getlist("action_page")
    texts = form.getlist("action_text")
    scenes = form.getlist("action_scene")
    zones = form.getlist("action_calm_zone") or form.getlist("action_text_placement")
    instructions = form.getlist("action_edit_instruction")
    reasons = form.getlist("action_reason")
    locks = form.getlist("action_force_character_lock")

    actions = []
    for index, action_type in enumerate(types):
        lock_raw = locks[index] if index < len(locks) else "false"
        raw = {
            "type": action_type,
            "spread": spreads[index] if index < len(spreads) else None,
            "text": texts[index] if index < len(texts) else None,
            "scene": scenes[index] if index < len(scenes) else None,
            "calm_zone": zones[index] if index < len(zones) else None,
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
                "summary": "AI is reviewing every spread for layout and face issues…",
            },
            "change_progress": {
                "change_id": change_id,
                "demand": "Self-revise: review all spreads and propose fixes",
                "phase": "reviewing",
                "summary": "AI is reviewing every spread for layout and face issues…",
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
                        "spread": a.get("spread"),
                        "label": f"{a.get('type')} · spread {a.get('spread')}",
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
