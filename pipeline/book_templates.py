"""Approved catalogue books saved as reusable templates (spec §12).

A template stores the approved spread text with ``{slot}`` placeholders (``{name}``,
``{carer_name}``, ``{comfort_object}``, ``{<catalogue slot id>}``) and the slot schema. It NEVER
stores the child's photo, character / set / anchor sheets, art, order id or dedication text.
Reuse re-asks every slot, rebuilds the locked copy from the catalogue, recompiles the 32-page map,
and the runner regenerates the sheets, all 28 page images and the PDF for the new child.

``{key^}`` means "capitalise the first letter" (a slot value used at the start of a sentence).
"""

from __future__ import annotations

import copy
import re
import shutil
import time
import uuid
from pathlib import Path

from .catalogue import get_title
from .constants import CONTENT_PAGE_COUNT, TEMPLATE_ROOT, pronouns_for
from .job_log import append_event, read_json, write_json
from .pages import compile_pages

PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)(\^?)\}")

# Story keys never copied into a template (rebuilt from the catalogue + the new order on reuse).
_DROP_STORY_KEYS = {"locked", "title", "_cast_lock", "carer", "humanized"}

# Standard slots asked for in every title (spec §12). ``required`` mirrors the create form.
STANDARD_SLOTS = [
    {"id": "name", "label": "Child's name", "type": "text", "required": True},
    {"id": "age", "label": "Age", "type": "text", "required": True},
    {"id": "pronouns", "label": "Pronouns", "type": "select", "options": ["she", "he", "they"], "required": True},
    {"id": "carer_name", "label": "Carer's name", "type": "text", "required": True},
    {"id": "carer_role", "label": "Carer's role (Mum, Dad, Nana...)", "type": "text", "required": True},
    {"id": "home_type", "label": "Home", "type": "select", "options": ["house", "flat"], "required": True},
    {"id": "comfort_object", "label": "Comfort object", "type": "text", "required": True},
    {"id": "dedication_text", "label": "Dedication (optional)", "type": "text", "required": False},
    {"id": "order_id", "label": "Order ID", "type": "text", "required": True},
]
_STANDARD_IDS = {s["id"] for s in STANDARD_SLOTS}


# --- slot schema -----------------------------------------------------------------------------------


def slot_schema(title_id: str) -> list[dict]:
    """Every slot a reuse must supply: standard slots + the title's own personalisation slots."""
    title = get_title(title_id)
    return [dict(s) for s in STANDARD_SLOTS] + [dict(s) for s in title.get("personalisation_slots") or [] if s.get("id")]


def missing_slots(title_id: str, values: dict) -> list[str]:
    return [
        s["id"]
        for s in slot_schema(title_id)
        if s.get("required") and not str(values.get(s["id"]) or "").strip()
    ]


def inputs_from_values(title_id: str, values: dict) -> dict:
    """Build a full inputs.json from slot values (raises ValueError listing missing slots)."""
    from .catalogue import resolve_route

    missing = missing_slots(title_id, values)
    if missing:
        raise ValueError("Missing required slot(s): " + ", ".join(missing))
    title = get_title(title_id)
    pronouns = str(values["pronouns"]).strip().lower()
    pronouns_for(pronouns)  # validates
    home = str(values.get("home_type") or "house").strip().lower()
    slots = {
        s["id"]: str(values[s["id"]]).strip()
        for s in title.get("personalisation_slots") or []
        if s.get("id") and str(values.get(s["id"]) or "").strip()
    }
    return {
        "title_id": title_id,
        "route": resolve_route(title),
        "age_band": title["age_band"],
        "name": str(values["name"]).strip(),
        "age": str(values["age"]).strip(),
        "pronouns": pronouns,
        "gender": {"she": "female", "he": "male", "they": "they"}.get(pronouns, pronouns),
        "carer": {"name": str(values["carer_name"]).strip(), "role": str(values["carer_role"]).strip()},
        "home_type": home if home in {"flat", "house"} else "house",
        "comfort_object": str(values["comfort_object"]).strip(),
        "slots": slots,
        "dedication_text": str(values.get("dedication_text") or "").strip(),
        "order_id": str(values["order_id"]).strip(),
        "start_mode": "full",
        "page_count": CONTENT_PAGE_COUNT,
    }


def _values_from_inputs(inputs: dict) -> dict[str, str]:
    """placeholder key -> the source child's value (only values that can appear in story text)."""
    values = {
        "name": inputs.get("name") or "",
        "carer_name": (inputs.get("carer") or {}).get("name") or "",
        "comfort_object": inputs.get("comfort_object") or "",
    }
    for slot_id, value in (inputs.get("slots") or {}).items():
        if slot_id not in _STANDARD_IDS:
            values[slot_id] = str(value or "")
    return {k: v.strip() for k, v in values.items() if v and v.strip()}


# --- placeholder substitution --------------------------------------------------------------------


def _to_placeholder(text: str, subs: list[tuple[re.Pattern, str, str]]) -> str:
    for pattern, key, value in subs:

        def repl(match: re.Match, key=key, value=value) -> str:
            # ^ only when the sentence capitalised a value that is normally lower case
            cap = match.group(0)[:1].isupper() and value[:1].islower()
            return "{" + key + ("^" if cap else "") + "}"

        text = pattern.sub(repl, text)
    return text


def _map_strings(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_map_strings(i, fn) for i in obj]
    if isinstance(obj, dict):
        return {k: _map_strings(v, fn) for k, v in obj.items()}
    return obj


def to_placeholders(obj, values: dict[str, str]):
    """Replace each personal value with its ``{key}`` placeholder (longest value first)."""
    ordered = sorted(values.items(), key=lambda kv: len(kv[1]), reverse=True)
    subs = [
        (re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", re.IGNORECASE), key, value)
        for key, value in ordered
    ]
    return _map_strings(obj, lambda s: _to_placeholder(s, subs))


def fill_placeholders(obj, values: dict[str, str]):
    """Fill ``{key}`` / ``{key^}``; a placeholder with no supplied value raises ValueError."""

    def fill(text: str) -> str:
        def repl(match: re.Match) -> str:
            key, cap = match.group(1), match.group(2)
            if key not in values or not str(values[key]).strip():
                raise ValueError(f"Template slot '{{{key}}}' has no value.")
            value = str(values[key]).strip()
            return value[:1].upper() + value[1:] if cap else value

        return PLACEHOLDER_RE.sub(repl, text)

    return _map_strings(obj, fill)


def placeholder_keys(obj) -> set[str]:
    found: set[str] = set()
    _map_strings(obj, lambda s: found.update(m.group(1) for m in PLACEHOLDER_RE.finditer(s)) or s)
    return found


def _assert_no_personal_data(obj, inputs: dict) -> None:
    """Fail closed if any token of the source name/carer name survived placeholdering."""
    import json

    blob = json.dumps(obj, ensure_ascii=False).lower()
    sources = [inputs.get("name") or "", (inputs.get("carer") or {}).get("name") or ""]
    for source in sources:
        for token in re.split(r"\s+", source.strip()):
            if len(token) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(token.lower()) + r"(?![a-z0-9])", blob):
                raise ValueError(
                    "Cannot save template: personal data (a child/carer name) is still in the text. "
                    "Edit the story so every mention can be replaced, then try again."
                )


# --- storage ----------------------------------------------------------------------------------------


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
    """Snapshot the approved spread text (placeholders only). No photo, sheets, art or order data."""
    inputs = read_json(job_dir / "inputs.json") or {}
    job = read_json(job_dir / "job.json") or {}
    story = read_json(job_dir / "story" / "rebuilt_story.json")
    if not story or not story.get("spreads"):
        raise ValueError("Job has no story to template.")
    if job.get("status") not in {"complete", "self_revise_ready"} and not job.get("pdf"):
        raise ValueError("Only finished books can be saved as templates.")
    title_id = inputs.get("title_id") or story.get("title_id")
    if not title_id:
        raise ValueError("Job has no catalogue title_id.")
    title = get_title(title_id)
    if not (inputs.get("name") or "").strip():
        raise ValueError("Job is missing the child's name needed for placeholders.")

    values = _values_from_inputs(inputs)
    body = {k: copy.deepcopy(v) for k, v in story.items() if k not in _DROP_STORY_KEYS}
    body = to_placeholders(body, values)
    _assert_no_personal_data(body, inputs)

    schema = slot_schema(title_id)
    unknown = placeholder_keys(body) - {"name", "carer_name", "comfort_object"} - {s["id"] for s in schema}
    if unknown:
        raise ValueError(f"Unknown placeholder(s) in template: {sorted(unknown)}")

    template_id = uuid.uuid4().hex
    dest = template_dir(template_id)
    if dest.exists():
        raise RuntimeError(f"Template path already exists: {template_id}")
    (dest / "story").mkdir(parents=True, exist_ok=True)
    write_json(dest / "story" / "template_story.json", body)

    label_text = (label or title["title_template"]).strip() or title["title_template"]
    meta = {
        "template_id": template_id,
        "label": label_text,
        "title": title["title_template"],  # still contains {name}
        "title_id": title_id,
        "family": title.get("family"),
        "band": title.get("band"),
        "route": inputs.get("route") or title.get("route"),
        # The text itself is written for these pronouns (not stored about any person).
        "text_pronouns": (inputs.get("pronouns") or "").strip().lower(),
        "age_band": title.get("age_band"),
        "page_count": CONTENT_PAGE_COUNT,
        "slots": schema,
        "placeholders": sorted(placeholder_keys(body)),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    write_json(dest / "template.json", meta)

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


def materialize_template_into_job(template_id: str, job_dir: Path, *, values: dict) -> dict:
    """Fill the template for a new child: new inputs, rebuilt locked copy, recompiled 32-page map."""
    from .story import _build_locked, _cast_lock

    meta = get_template(template_id)
    if not meta:
        raise FileNotFoundError("Template not found.")
    body = read_json(template_dir(template_id) / "story" / "template_story.json")
    if not body or not body.get("spreads"):
        raise ValueError("Template content is incomplete.")

    inputs = inputs_from_values(meta["title_id"], values)
    if meta.get("text_pronouns") and inputs["pronouns"] != meta["text_pronouns"]:
        raise ValueError(
            f"This template's text is written with '{meta['text_pronouns']}' pronouns; "
            "for other pronouns create a new book from the catalogue."
        )
    inputs["start_mode"] = "from_template"
    inputs["from_template_id"] = template_id

    fill_values = {"name": inputs["name"], "carer_name": inputs["carer"]["name"], "comfort_object": inputs["comfort_object"]}
    fill_values.update(inputs["slots"])
    story = fill_placeholders(body, fill_values)

    title = get_title(meta["title_id"])
    locked = _build_locked(title, inputs)
    story.update(
        {
            "title_id": meta["title_id"],
            "title": locked["title"],
            "locked": locked,
            "carer": inputs["carer"],
            "_cast_lock": _cast_lock(inputs),
        }
    )

    story_dir = job_dir / "story"
    story_dir.mkdir(parents=True, exist_ok=True)
    write_json(story_dir / "rebuilt_story.json", story)
    write_json(job_dir / "inputs.json", inputs)
    pages = compile_pages(None, job_dir, inputs, story)  # deterministic map; writes pages.json
    return {"meta": meta, "inputs": inputs, "story": story, "pages": pages}
