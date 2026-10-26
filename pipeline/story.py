import json
from pathlib import Path

from .constants import GENDER_DETAILS
from .humanize import humanize_story_fields
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import chat_json


SYSTEM = """You are an expert children's picture-book editor and writer for ages 1–10.
You rebuild a drafted story into a publishable picture-book manuscript.

Rules:
- Keep the child's name as the hero.
- Match the selected THEME (Adventure, Fairy Tales, Activities, Worlds, Holidays,
  Family, Educational, Feelings) in tone, motifs, and plot beats.
- Honor PURPOSE (educational / entertaining / both).
- Age-appropriate vocabulary and sentence length.
- Warm, human, non-generic prose — avoid AI clichés.
- English only.
- Do NOT invent supporting characters, mascots, toys, pets, or sidekicks that are
  not already present in the draft story. supporting_characters must only list
  people/creatures clearly grounded in the draft (empty array is allowed).
- Do NOT add recurring props (e.g. dinosaur plush) unless the draft mentions them.
- Return strict JSON."""


def rebuild_story(client, job_dir: Path, inputs: dict) -> dict:
    gender = inputs["gender"]
    pronouns = GENDER_DETAILS[gender]
    preference_memory = format_memory_for_prompt()

    user = {
        "child_name": inputs["name"],
        "age": inputs["age"],
        "gender": gender,
        "pronouns": pronouns,
        "theme": inputs["theme"],
        "purpose": inputs["purpose"],
        "page_count": inputs["page_count"],
        "design_rules": inputs.get("design_rules") or "",
        "draft_story": inputs["draft_story"],
        "preference_memory": preference_memory,
        "output_schema": {
            "title": "string",
            "subtitle": "string or empty",
            "moral": "one sentence",
            "synopsis": "2-4 sentences",
            "recurring_outfit": "locked outfit description for the hero",
            "supporting_characters": [
                {"name": "string", "description": "visual + role"}
            ],
            "story_beats": ["ordered list of major beats for the full book"],
            "rebuilt_story": "full rebuilt prose narrative used as source for page breakdown",
        },
    }

    append_event(
        job_dir,
        "story_rebuild_start",
        f"Rebuilding draft for theme={inputs['theme']} purpose={inputs['purpose']}.",
    )
    raw = chat_json(
        client,
        SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Story rebuild",
    )
    payload = json.loads(raw)
    required = ("title", "rebuilt_story", "recurring_outfit", "story_beats")
    for field in required:
        if not payload.get(field):
            raise ValueError(f"Story rebuild missing field: {field}")

    write_json(job_dir / "story" / "rebuilt_story.json", payload)
    (job_dir / "story" / "rebuilt_story.txt").write_text(
        payload["rebuilt_story"], encoding="utf-8"
    )
    append_event(
        job_dir,
        "story_rebuild_done",
        f"Title: {payload['title']}",
        title=payload["title"],
        moral=payload.get("moral"),
    )
    payload = humanize_story_fields(client, job_dir, payload)
    return payload
