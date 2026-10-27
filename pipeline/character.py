"""Hero character sheet from the order photo (v2)."""

from __future__ import annotations

from pathlib import Path

from .brand import ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import get_title, resolve_route
from .job_log import append_event
from .openai_util import image_edit, square_image_size


def generate_character(client, job_dir: Path, reference_path: Path, inputs: dict) -> Path:
    title = get_title(inputs["title_id"])
    route = resolve_route(title)
    preset = ROUTE_PRESETS[route]
    out = job_dir / "character" / "character.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"{preset['character_sheet']}\n"
        f"Child named {inputs['name']}, age {inputs['age']}, pronouns {inputs['pronouns']}. "
        f"Everyday outfit: {title.get('outfit')}. Comfort object: {inputs.get('comfort_object')}. "
        f"Match the face, hair, skin tone and marks from the reference photo exactly. "
        f"{SHARED_NEGATIVES}. {preset['negatives']}."
    )
    append_event(job_dir, "character_start", "Generating hero character sheet.")
    result = image_edit(
        client,
        [reference_path],
        prompt,
        size=square_image_size(),
        description="Character sheet",
    )
    data = result.data[0].b64_json
    import base64

    out.write_bytes(base64.b64decode(data))
    append_event(job_dir, "character_done", "Character sheet saved.")
    return out
