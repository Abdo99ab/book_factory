import base64
from pathlib import Path

from .brand import ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import get_title, load_story_beats
from .constants import CHARACTER_IDENTITY, IMAGE_QUALITY, IMAGE_SIZE, pronouns_for
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import image_edit


def _image_size() -> str:
    """IMAGE_SIZE (default auto: largest square the model accepts; see openai_util)."""
    return IMAGE_SIZE


def _character_prompt(inputs: dict, title: dict, route_key: str) -> str:
    preset = ROUTE_PRESETS[route_key]
    pronouns = pronouns_for(inputs.get("pronouns") or inputs.get("gender"))
    anchor_desc = (title.get("anchor") or {}).get("description") or "anchor object"
    outfit = title.get("outfit") or "everyday outfit"
    art_notes = inputs.get("art_notes") or ""
    return (
        f"{preset['character_sheet']}\n"
        f"{CHARACTER_IDENTITY}\n"
        f"Child: {inputs['name']}, age {inputs['age']} ({pronouns['character_type']}). "
        f"Pronouns: {pronouns['subject_pronoun']}/{pronouns['object_pronoun']}.\n"
        f"Locked outfit for this book: {outfit}.\n"
        f"Draw the anchor object beside the child: {anchor_desc}.\n"
        f"Show front, three-quarter and profile views on one sheet.\n"
        f"{SHARED_NEGATIVES}\n"
        f"{preset['negatives']}\n"
        f"{art_notes}\n"
        f"{format_memory_for_prompt()}\n"
        "NO TEXT, letters, watermarks, or logos in the image."
    )


def generate_character(client, job_dir: Path, reference_path: Path, inputs: dict) -> Path:
    title = get_title(inputs["title_id"])
    route_key = inputs.get("route") or "morning_light"
    prompt = _character_prompt(inputs, title, route_key)

    out_dir = job_dir / "character"
    out_dir.mkdir(parents=True, exist_ok=True)
    append_event(job_dir, "character_start", "Generating hero character sheet from photo.")

    quality = IMAGE_QUALITY if IMAGE_QUALITY in {"low", "medium", "high", "auto"} else "high"
    result = image_edit(
        client,
        reference_path,
        prompt,
        size=_image_size(),
        quality=quality,
        description="Character sheet",
    )
    character_path = out_dir / "character.png"
    character_path.write_bytes(base64.b64decode(result.data[0].b64_json))

    meta = {
        "prompt": prompt,
        "username": inputs["name"],
        "age": inputs["age"],
        "pronouns": inputs.get("pronouns"),
        "route": route_key,
        "outfit": title.get("outfit"),
        "source_image": reference_path.name,
        "character_image": "character.png",
        "size": _image_size(),
        "status": "awaiting_approval",
    }
    write_json(out_dir / "character.json", meta)
    append_event(job_dir, "character_done", f"Character saved to {character_path.name}.")
    return character_path


def generate_set_sheets(client, job_dir: Path, inputs: dict) -> list[Path]:
    beats_meta = load_story_beats(inputs["title_id"])["meta"]
    set_ids = beats_meta.get("sets") or []
    if not set_ids:
        return []
    route_key = inputs.get("route") or "morning_light"
    preset = ROUTE_PRESETS[route_key]
    out_dir = job_dir / "sets"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    append_event(job_dir, "set_sheets_start", f"Generating {len(set_ids)} set sheet(s).")
    for set_id in set_ids:
        prompt = (
            f"{preset['preamble']}\n"
            f"Reference set sheet for '{set_id}' — empty of people, reusable background.\n"
            f"Home type: {inputs.get('home_type', 'house')}.\n"
            f"{SHARED_NEGATIVES}\n"
            "NO TEXT in the image."
        )
        result = image_edit(
            client,
            job_dir / "character" / "character.png",
            prompt,
            size=_image_size(),
            quality=IMAGE_QUALITY,
            description=f"Set sheet {set_id}",
        )
        path = out_dir / f"{set_id}.png"
        path.write_bytes(base64.b64decode(result.data[0].b64_json))
        paths.append(path)
    append_event(job_dir, "set_sheets_done", f"Saved {len(paths)} set sheet(s).")
    return paths


def generate_anchor_sheet(client, job_dir: Path, inputs: dict) -> Path | None:
    title = get_title(inputs["title_id"])
    anchor = title.get("anchor") or {}
    description = anchor.get("description")
    if not description:
        return None
    route_key = inputs.get("route") or "morning_light"
    preset = ROUTE_PRESETS[route_key]
    out_dir = job_dir / "anchor"
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"{preset['preamble']}\n"
        f"Draw the title anchor object once, exactly as specified:\n{description}\n"
        f"Comfort object for colour reference: {inputs.get('comfort_object', '')}.\n"
        f"{SHARED_NEGATIVES}\n"
        "NO TEXT in the image."
    )
    append_event(job_dir, "anchor_sheet_start", "Generating anchor object sheet.")
    result = image_edit(
        client,
        job_dir / "character" / "character.png",
        prompt,
        size=_image_size(),
        quality=IMAGE_QUALITY,
        description="Anchor sheet",
    )
    path = out_dir / "anchor.png"
    path.write_bytes(base64.b64decode(result.data[0].b64_json))
    write_json(out_dir / "anchor.json", {"description": description, "image": "anchor.png"})
    append_event(job_dir, "anchor_sheet_done", "Anchor sheet saved.")
    return path
