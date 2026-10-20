import base64
from pathlib import Path

from .constants import NO_TEXT_RULE, PAGE_IMAGE_SIZE
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import image_edit, image_generate


def _page_prompt(page: dict, inputs: dict, story: dict) -> str:
    preference_memory = format_memory_for_prompt()
    outfit = story.get("recurring_outfit", "")
    design_rules = inputs.get("design_rules") or ""
    supporting = story.get("supporting_characters") or []
    if supporting:
        support_text = (
            "APPROVED supporting characters only (keep consistent):\n"
            + "\n".join(f"- {c.get('name')}: {c.get('description')}" for c in supporting)
            + "\nDo NOT invent or include any other named people, siblings, pets, toys, "
            "mascots, or plush animals. If the scene text mentions someone not listed "
            "above, ignore that mention and keep only the hero plus approved supports."
        )
    else:
        support_text = (
            "No approved supporting characters. Show only the hero child. "
            "Do NOT include siblings, pets, toys, mascots, or plush animals. "
            "An unnamed adult caregiver may appear only if the scene clearly needs one, "
            "with no named side characters."
        )

    leave_space = {
        "top": "Leave soft clear space in the TOP 25% for later text overlay.",
        "upper_third": "Leave soft clear space in the UPPER THIRD for later text overlay.",
        "middle": "Leave soft clear space in the MIDDLE band for later text overlay.",
        "lower_third": "Leave soft clear space in the LOWER THIRD for later text overlay.",
        "bottom": "Leave soft clear space in the BOTTOM 28% for later text overlay.",
    }[page["text_placement"]]

    return f"""Create a complete children's picture-book illustration for Page {page['number']},
titled '{page['title']}' (title is metadata only — do not paint the title).

Theme: {inputs['theme']}
Purpose: {inputs['purpose']}
Hero name: {inputs['name']} (age {inputs['age']})
Locked hero outfit: {outfit}

Scene:
{page['scene']}

{support_text}

Design rules:
{design_rules}

{preference_memory}

Composition: portrait 3:4, full-bleed, warm polished picture-book style.
{leave_space}

{NO_TEXT_RULE}
"""


def generate_page_image(client, job_dir: Path, page: dict, inputs: dict, story: dict, character_path: Path):
    page_dir = job_dir / "pages" / f"page-{page['number']:02d}"
    page_dir.mkdir(parents=True, exist_ok=True)

    if page.get("blank"):
        write_json(
            page_dir / "page.json",
            {**page, "status": "blank", "art": None},
        )
        append_event(job_dir, "page_blank", f"Page {page['number']} reserved blank.")
        return None

    prompt = _page_prompt(page, inputs, story)
    write_json(
        page_dir / "page.json",
        {**page, "prompt": prompt, "status": "generating"},
    )
    append_event(job_dir, "page_start", f"Illustrating page {page['number']}.")

    # Personalized pages must pass the locked character sheet so the face matches.
    # Non-personalized pages are text-only generate (no face lock).
    if page.get("personalized", True):
        if not Path(character_path).is_file():
            raise FileNotFoundError(
                f"Character sheet missing for personalized page {page['number']}"
            )
        # Strengthen face lock in the edit prompt
        prompt = (
            prompt
            + "\n\nFACE LOCK: The supplied reference image is the hero character sheet. "
            "Match this child's exact face, hair, skin tone, and proportions. "
            "Do not invent a different child."
        )
        result = image_edit(
            client,
            character_path,
            prompt,
            size=PAGE_IMAGE_SIZE,
            description=f"Page {page['number']}",
        )
    else:
        result = image_generate(
            client,
            prompt,
            size=PAGE_IMAGE_SIZE,
            description=f"Page {page['number']}",
        )

    art_path = page_dir / "art.png"
    art_path.write_bytes(base64.b64decode(result.data[0].b64_json))
    write_json(
        page_dir / "page.json",
        {**page, "prompt": prompt, "status": "generated", "art": "art.png"},
    )
    append_event(job_dir, "page_done", f"Page {page['number']} art saved.")
    return art_path


def generate_cover_panels(client, job_dir: Path, inputs: dict, story: dict, character_path: Path):
    """Generate front and back cover art without typography (text overlaid later)."""
    preference_memory = format_memory_for_prompt()
    design_rules = inputs.get("design_rules") or ""
    cover_dir = job_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)

    front_prompt = f"""Create the FRONT COVER illustration only for a personalized children's
picture book titled "{story['title']}". Portrait 3:4, full bleed.

Hero: {inputs['name']}, age {inputs['age']}, is the clear focal character.
Locked outfit: {story.get('recurring_outfit', '')}
Theme: {inputs['theme']}
Synopsis mood: {story.get('synopsis', '')}

Design rules:
{design_rules}

{preference_memory}

Leave soft clear space in the UPPER third of the image for a title overlay later.
Show the hero prominently in the lower/middle area.
{NO_TEXT_RULE}
"""

    back_prompt = f"""Create the BACK COVER illustration only for the children's picture book
"{story['title']}". Portrait 3:4, full bleed. Same world and art style as the story.
Theme: {inputs['theme']}. Mood: {story.get('synopsis', '')}.
Optional subtle scene without the hero, or a quiet epilogue atmosphere.
Leave a soft clear lower area for an optional blurb overlay.
Design rules:
{design_rules}
{preference_memory}
{NO_TEXT_RULE}
"""

    append_event(job_dir, "cover_start", "Generating wraparound cover panels (front + back).")

    front_result = image_edit(
        client,
        character_path,
        front_prompt,
        size=PAGE_IMAGE_SIZE,
        description="Front cover art",
    )
    front_path = cover_dir / "front_art.png"
    front_path.write_bytes(base64.b64decode(front_result.data[0].b64_json))

    back_result = image_generate(
        client,
        back_prompt,
        size=PAGE_IMAGE_SIZE,
        description="Back cover art",
    )
    back_path = cover_dir / "back_art.png"
    back_path.write_bytes(base64.b64decode(back_result.data[0].b64_json))

    write_json(
        cover_dir / "cover.json",
        {
            "title": story["title"],
            "subtitle": story.get("subtitle") or "",
            "front_prompt": front_prompt,
            "back_prompt": back_prompt,
            "front_art": "front_art.png",
            "back_art": "back_art.png",
        },
    )
    append_event(job_dir, "cover_done", "Front and back cover art saved.")
    return front_path, back_path
