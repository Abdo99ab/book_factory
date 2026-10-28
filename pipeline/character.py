"""Hero character sheets from the order photo (v2): full-body outfit + face close-up."""

from __future__ import annotations

import base64
from pathlib import Path

from .brand import ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import get_title, resolve_route
from .job_log import append_event
from .openai_util import image_edit, square_image_size


def character_paths(job_dir: Path) -> dict[str, Path]:
    base = Path(job_dir) / "character"
    return {
        "body": base / "character.png",
        "face": base / "face.png",
    }


def _outfit_and_child(inputs: dict, title: dict) -> tuple[str, str, str]:
    outfit = (inputs.get("outfit") or title.get("outfit") or "").strip()
    comfort = (inputs.get("comfort_object") or "").strip()
    comfort_bit = f" Comfort object: {comfort}." if comfort else ""
    child = (
        f"Child named {inputs['name']}, age {inputs['age']}, pronouns {inputs['pronouns']}. "
        f"Match the face, hair, skin tone and marks from the reference photo exactly."
    )
    return outfit, comfort_bit, child


def _sheet_prompt(kind: str, preset: dict, inputs: dict, title: dict) -> str:
    outfit, comfort_bit, child = _outfit_and_child(inputs, title)
    if kind == "body":
        return (
            f"{preset['character_sheet']}\n"
            f"{child} Everyday outfit: {outfit}.{comfort_bit} "
            f"{SHARED_NEGATIVES}. {preset['negatives']}."
        )
    # Face: book outfit at the neckline only — never the photo's clothes (leaks into spreads).
    return (
        f"{preset.get('face_sheet') or preset['character_sheet']}\n"
        f"{child} Head-and-shoulders portrait sheet only — front view and three-quarter view, "
        f"faces filling most of the frame, plain light background, same style as the route. "
        f"Wearing {outfit} (neckline / collar only visible) — not the clothes from the photo; "
        f"replace any photo clothing with that outfit's neckline. "
        f"No full body, no scenery. {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def generate_one_sheet(
    client,
    job_dir: Path,
    reference_path: Path,
    inputs: dict,
    kind: str,
    *,
    force: bool = False,
) -> Path:
    """Generate ``face`` or ``body`` sheet. ``force`` deletes an existing file first."""
    assert kind in {"face", "body"}
    reference_path = Path(reference_path)
    if not reference_path.is_file():
        raise FileNotFoundError(f"Reference photo missing: {reference_path}")
    title = get_title(inputs["title_id"])
    route = resolve_route(title)
    preset = ROUTE_PRESETS[route]
    paths = character_paths(job_dir)
    out = paths[kind]
    out.parent.mkdir(parents=True, exist_ok=True)
    if force and out.is_file():
        out.unlink()
    if out.is_file() and not force:
        append_event(job_dir, f"{kind}_sheet_skip", f"{kind.title()} sheet exists; keeping.")
        return out
    label = "Face sheet" if kind == "face" else "Character sheet"
    prompt = _sheet_prompt(kind, preset, inputs, title)
    append_event(
        job_dir,
        f"{kind}_sheet_start",
        f"Generating {label.lower()} from {reference_path.name}.",
    )
    try:
        result = image_edit(
            client,
            [reference_path],
            prompt,
            size=square_image_size(),
            description=label,
        )
    except Exception as error:
        append_event(job_dir, f"{kind}_sheet_failed", str(error))
        raise RuntimeError(f"{label} generation failed: {error}") from error
    data = getattr(result.data[0], "b64_json", None) if result.data else None
    if not data:
        raise RuntimeError(f"{label} generation returned no image data.")
    out.write_bytes(base64.b64decode(data))
    append_event(job_dir, f"{kind}_sheet_done", f"{label} saved.")
    return out


def generate_character(client, job_dir: Path, reference_path: Path, inputs: dict) -> dict[str, Path]:
    """Generate full-body outfit sheet and a large face sheet. Returns ``{body, face}`` paths."""
    body = generate_one_sheet(client, job_dir, reference_path, inputs, "body")
    face = generate_one_sheet(client, job_dir, reference_path, inputs, "face")
    append_event(job_dir, "character_done", "Character body + face sheets ready.")
    return {"body": body, "face": face}
