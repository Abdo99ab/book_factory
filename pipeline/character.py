import base64
from pathlib import Path

from .constants import CHARACTER_PROMPT, GENDER_DETAILS, PAGE_IMAGE_SIZE
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import image_edit


def generate_character(client, job_dir: Path, reference_path: Path, inputs: dict):
    gender = inputs["gender"]
    age = inputs["age"]
    design_rules = inputs.get("design_rules") or "Warm, polished children's picture-book illustration."
    preference_memory = format_memory_for_prompt()

    prompt = CHARACTER_PROMPT.format(
        character_type=GENDER_DETAILS[gender]["character_type"],
        age=age,
        design_rules=design_rules,
        preference_memory=preference_memory,
    )

    out_dir = job_dir / "character"
    out_dir.mkdir(parents=True, exist_ok=True)
    append_event(job_dir, "character_start", "Generating hero character sheet from photo.")

    result = image_edit(
        client,
        reference_path,
        prompt,
        size=PAGE_IMAGE_SIZE,
        description="Character sheet",
    )
    character_path = out_dir / "character.png"
    character_path.write_bytes(base64.b64decode(result.data[0].b64_json))

    write_json(
        out_dir / "character.json",
        {
            "prompt": prompt,
            "username": inputs["name"],
            "age": age,
            "gender": gender,
            "source_image": reference_path.name,
            "character_image": "character.png",
            "size": PAGE_IMAGE_SIZE,
        },
    )
    append_event(job_dir, "character_done", f"Character saved to {character_path.name}.")
    return character_path
