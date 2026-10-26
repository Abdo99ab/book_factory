import json
from pathlib import Path

from .constants import CONTENT_PAGE_COUNT, GENDER_DETAILS
from .humanize import humanize_page_texts
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import chat_json


SYSTEM = """You compile a children's picture book into a page-by-page production brief.

Each page must include:
- number (1..N)
- title (short)
- scene: vivid illustrator description (setting, action, mood, colors, camera).
  The hero child must match the locked outfit. Do NOT describe any text in the scene.
- text: story text for that page (spoken/read aloud). Concise for a picture book.
- text_placement: one of top, upper_third, middle, lower_third, bottom
- personalized: boolean — true if the hero child appears on the page
- blank: boolean — false for content pages (plain endpapers are added in print, not here)

Page 1 should be a title/opening page. Page 2 may be a dedication.
Remaining pages tell the story. Exactly N pages (TJ print: 32 content pages;
endpapers are separate and must NOT be included in this list).
Illustrations are square 1:1 (210×210 mm). Leave clear negative space where
text_placement is set so overlay text will fit (cream panel area can be soft empty
ground, sky, or wall — but NO letters in the art). Keep important faces away from
the outer edges (3 mm bleed is trimmed).

Cast lock (critical):
- Only the hero and story.supporting_characters may appear in scene or text.
- If story._cast_lock is present, obey it exactly. Never mention or depict forbidden
  names, pets, plush toys, mascots, or extra siblings.

Return JSON: { "pages": [ ... ] }"""


def compile_pages(client, job_dir: Path, inputs: dict, story: dict) -> list:
    page_count = int(inputs["page_count"])
    if page_count != CONTENT_PAGE_COUNT:
        raise ValueError(
            f"page_count must be {CONTENT_PAGE_COUNT} (TJ 170 gsm spine lock)"
        )

    preference_memory = format_memory_for_prompt()
    cast_lock = story.get("_cast_lock") or {}
    user = {
        "page_count": page_count,
        "child_name": inputs["name"],
        "age": inputs["age"],
        "gender": inputs["gender"],
        "pronouns": GENDER_DETAILS[inputs["gender"]],
        "theme": inputs["theme"],
        "purpose": inputs["purpose"],
        "design_rules": inputs.get("design_rules") or "",
        "story": {
            "title": story.get("title"),
            "subtitle": story.get("subtitle"),
            "moral": story.get("moral"),
            "synopsis": story.get("synopsis"),
            "recurring_outfit": story.get("recurring_outfit"),
            "supporting_characters": story.get("supporting_characters") or [],
            "story_beats": story.get("story_beats") or [],
            "rebuilt_story": story.get("rebuilt_story"),
            "_cast_lock": cast_lock,
        },
        "cast_lock": cast_lock,
        "preference_memory": preference_memory,
    }

    append_event(
        job_dir,
        "pages_compile_start",
        f"Compiling {page_count} production pages.",
    )
    raw = chat_json(
        client,
        SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Page compilation",
    )
    payload = json.loads(raw)
    pages = payload.get("pages")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise ValueError(
            f"Expected {page_count} pages, got {len(pages) if isinstance(pages, list) else 0}"
        )

    allowed_placements = {
        "top",
        "upper_third",
        "middle",
        "lower_third",
        "bottom",
    }
    normalized = []
    for index, page in enumerate(pages, start=1):
        number = int(page.get("number", index))
        placement = str(page.get("text_placement", "bottom")).strip().lower()
        if placement not in allowed_placements:
            placement = "bottom"
        item = {
            "number": number,
            "title": str(page.get("title") or f"Page {number}"),
            "scene": str(page.get("scene") or "").strip(),
            "text": str(page.get("text") or "").strip(),
            "text_placement": placement,
            "personalized": bool(page.get("personalized", True)),
            "blank": bool(page.get("blank", False)),
        }
        if not item["blank"] and not item["scene"]:
            raise ValueError(f"Page {number} is missing a scene description")
        normalized.append(item)

    normalized.sort(key=lambda item: item["number"])
    write_json(job_dir / "story" / "pages.json", {"pages": normalized})
    append_event(
        job_dir,
        "pages_compile_done",
        f"Compiled {len(normalized)} pages.",
        page_count=len(normalized),
    )
    normalized = humanize_page_texts(client, job_dir, normalized)
    return normalized
