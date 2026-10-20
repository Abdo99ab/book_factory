"""Preference memory learned from operator change requests."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .constants import PREFERENCES_DIR
from .job_log import append_event, write_json
from .openai_util import chat_json


GLOBAL_MEMORY_PATH = PREFERENCES_DIR / "global.json"


def _empty_memory():
    return {
        "version": 1,
        "preferences": [],
        "history": [],
    }


def load_memory(path: Path | None = None) -> dict:
    path = path or GLOBAL_MEMORY_PATH
    if not path.is_file():
        return _empty_memory()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return _empty_memory()
    data.setdefault("preferences", [])
    data.setdefault("history", [])
    return data


def save_memory(memory: dict, path: Path | None = None):
    path = path or GLOBAL_MEMORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, memory)


def format_memory_for_prompt(memory: dict | None = None, limit: int = 20) -> str:
    memory = memory or load_memory()
    prefs = memory.get("preferences") or []
    if not prefs:
        return "No learned operator preferences yet."
    lines = []
    for item in prefs[-limit:]:
        text = item.get("text") if isinstance(item, dict) else str(item)
        if text:
            lines.append(f"- {text}")
    if not lines:
        return "No learned operator preferences yet."
    return "LEARNED OPERATOR PREFERENCES (apply unless the current brief contradicts them):\n" + "\n".join(
        lines
    )


def record_change_and_learn(
    client,
    job_dir: Path,
    demand: str,
    changes_applied: list[dict],
    outcome: str = "applied",
):
    """Log the demand/changes and extract durable preferences into global memory."""
    append_event(
        job_dir,
        "change_request",
        demand,
        changes=changes_applied,
        outcome=outcome,
    )

    memory = load_memory()
    memory["history"].append(
        {
            "job_id": job_dir.name,
            "demand": demand,
            "changes": changes_applied,
            "outcome": outcome,
        }
    )
    # Keep history bounded
    memory["history"] = memory["history"][-200:]

    system = (
        "You extract durable illustration and storytelling preferences from an "
        "operator's book edit request. Return JSON with key preferences: an array "
        "of short, reusable preference strings (max 5). Only include preferences "
        "that should apply to FUTURE books (style, typography placement, color, "
        "tone, character consistency). Skip one-off story plot fixes."
    )
    user = json.dumps(
        {
            "demand": demand,
            "changes_applied": changes_applied,
            "existing_preferences": [p.get("text") for p in memory.get("preferences", [])],
        },
        ensure_ascii=True,
    )
    try:
        raw = chat_json(client, system, user, description="Preference extraction")
        payload = json.loads(raw)
        new_prefs = payload.get("preferences") or []
    except Exception as error:
        append_event(job_dir, "memory_error", f"Could not extract preferences: {error}")
        save_memory(memory)
        return memory

    existing_texts = {
        re.sub(r"\s+", " ", (p.get("text") or "").strip().lower())
        for p in memory["preferences"]
        if isinstance(p, dict)
    }
    for item in new_prefs:
        text = str(item).strip()
        if not text:
            continue
        key = re.sub(r"\s+", " ", text.lower())
        if key in existing_texts:
            continue
        memory["preferences"].append({"text": text, "source_job": job_dir.name})
        existing_texts.add(key)
        append_event(job_dir, "memory_learned", text)

    memory["preferences"] = memory["preferences"][-50:]
    save_memory(memory)
    append_event(
        job_dir,
        "memory_saved",
        f"Global preference memory now has {len(memory['preferences'])} items.",
    )
    return memory
