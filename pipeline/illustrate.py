import base64
from pathlib import Path

from .constants import NO_TEXT_RULE, PAGE_IMAGE_SIZE
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import image_edit, image_generate


def _page_prompt(
    page: dict,
    inputs: dict,
    story: dict,
    edit_instruction: str | None = None,
) -> str:
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

    directive = (edit_instruction or "").strip()
    directive_block = ""
    if directive:
        directive_block = f"""
OPERATOR CHANGE REQUEST (must be clearly visible in the illustration):
{directive}
Apply this even if the locked outfit or character reference image omits it
(e.g. add sunglasses, hats, or props named here).
"""

    return f"""Create a complete children's picture-book illustration for Page {page['number']},
titled '{page['title']}' (title is metadata only — do not paint the title).

Theme: {inputs['theme']}
Purpose: {inputs['purpose']}
Hero name: {inputs['name']} (age {inputs['age']})
Locked hero outfit: {outfit}
{directive_block}
Scene:
{page['scene']}

{support_text}

Design rules:
{design_rules}

{preference_memory}

Composition: square 1:1 (210×210 mm trim with bleed), full-bleed, warm polished picture-book style.
{leave_space}

{NO_TEXT_RULE}
"""


def generate_page_image(
    client,
    job_dir: Path,
    page: dict,
    inputs: dict,
    story: dict,
    character_path: Path,
    edit_instruction: str | None = None,
):
    page_dir = job_dir / "pages" / f"page-{page['number']:02d}"
    page_dir.mkdir(parents=True, exist_ok=True)

    if page.get("blank"):
        write_json(
            page_dir / "page.json",
            {**page, "status": "blank", "art": None},
        )
        append_event(job_dir, "page_blank", f"Page {page['number']} reserved blank.")
        return None

    prompt = _page_prompt(page, inputs, story, edit_instruction=edit_instruction)
    write_json(
        page_dir / "page.json",
        {
            **page,
            "prompt": prompt,
            "edit_instruction": edit_instruction,
            "status": "generating",
        },
    )
    append_event(job_dir, "page_start", f"Illustrating page {page['number']}.")

    # Personalized pages must pass the locked character sheet so the face matches.
    # Non-personalized pages are text-only generate (no face lock).
    if page.get("personalized", True):
        if not Path(character_path).is_file():
            raise FileNotFoundError(
                f"Character sheet missing for personalized page {page['number']}"
            )
        # Face lock: keep identity; allow scene/operator accessories to differ from sheet.
        prompt = (
            prompt
            + "\n\nFACE LOCK: The supplied reference image is the hero character sheet. "
            "Match this child's exact face, hair, skin tone, and proportions. "
            "Do not invent a different child. "
            "Accessories and props required by the scene or OPERATOR CHANGE REQUEST "
            "(sunglasses, hats, etc.) MUST appear even if absent from the reference sheet."
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


def _cover_cast_block(inputs: dict, story: dict) -> str:
    hero = inputs.get("name") or "the hero"
    outfit = story.get("recurring_outfit") or ""
    supporting = story.get("supporting_characters") or []
    if supporting:
        support_lines = "\n".join(
            f"- {c.get('name')}: {c.get('description')}" for c in supporting
        )
        support_block = (
            "APPROVED supporting characters only (keep consistent with the story):\n"
            f"{support_lines}\n"
            "Do NOT invent extra children, siblings, teachers, or adults not listed above."
        )
    else:
        support_block = (
            "No approved supporting characters. Show only the hero child. "
            "Do NOT invent extra children or named side characters. "
            "An unnamed adult caregiver may appear only if the scene clearly needs one."
        )
    return f"""CAST LOCK (critical):
Hero: {hero}, age {inputs.get('age')} — MUST match the supplied character-sheet reference
exactly (face, hair, skin tone, proportions). Do not replace with a different child.
Locked hero outfit: {outfit}
{support_block}
"""


def generate_cover_panels(client, job_dir: Path, inputs: dict, story: dict, character_path: Path):
    """Generate front and back cover art without typography (text overlaid later)."""
    if not Path(character_path).is_file():
        raise FileNotFoundError("Character sheet missing; cannot generate cover panels.")

    preference_memory = format_memory_for_prompt()
    design_rules = inputs.get("design_rules") or ""
    cast_block = _cover_cast_block(inputs, story)
    cover_dir = job_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)

    face_lock = (
        "\n\nFACE LOCK: The supplied reference image is the hero character sheet. "
        "Match this child's exact face, hair, skin tone, and proportions. "
        "Do not invent a different child."
    )

    front_prompt = f"""Create the FRONT COVER illustration only for a personalized children's
picture book titled "{story['title']}". Square 1:1, full bleed (print wraps beyond trim).

{cast_block}
Theme: {inputs['theme']}
Synopsis mood: {story.get('synopsis', '')}

Design rules:
{design_rules}

{preference_memory}

Leave soft clear space in the UPPER third of the image for a title overlay later.
Show the hero prominently in the lower/middle area as the clear focal character.
Keep important faces and props away from the outer edges (they wrap around the cover).
One cohesive scene only — not a collage of many small vignettes.
{NO_TEXT_RULE}
{face_lock}
"""

    back_prompt = f"""Create the BACK COVER illustration only for the children's picture book
"{story['title']}". Square 1:1, full bleed (print wraps beyond trim).

{cast_block}
Theme: {inputs['theme']}
Mood / synopsis: {story.get('synopsis', '')}

Same world, art style, palette, and cast as the front cover and interior story.
Show the hero ({inputs['name']}) clearly — a quiet epilogue or gentle story moment —
with the SAME face and outfit as the character sheet. Supporting characters only if
they are on the approved list above; never invent a different child or random adults.

Keep the BOTTOM-RIGHT corner soft and empty (about the lower-right 15% of the image):
the printer places a small QR / batch code there. No faces, props, or busy detail in
that corner. Optional quiet negative space lower-left/center for a blurb is fine.

Design rules:
{design_rules}

{preference_memory}

One cohesive scene only — not a collage of many small vignettes.
{NO_TEXT_RULE}
{face_lock}
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

    # Back cover must also use the character sheet — text-only generate invents a
    # different child every time (the left panel on the wraparound print cover).
    back_result = image_edit(
        client,
        character_path,
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
            "character_locked": True,
        },
    )
    append_event(job_dir, "cover_done", "Front and back cover art saved (both character-locked).")
    return front_path, back_path
