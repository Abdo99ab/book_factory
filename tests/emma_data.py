"""Fictional 'Emma' order shared by guard, validation, PDF, and regression tests.

Emma is a fictional child. No real photo or personal data is used anywhere in tests.
"""

from __future__ import annotations

import copy
from pathlib import Path

from pipeline.catalogue import get_title
from pipeline.pages import compile_pages
from pipeline.story import _build_locked

NAME = "Emma"
TITLE_ID = "big_kid_list"

SPREAD_TEXTS = [
    "Emma sees the list. It is on the fridge.",
    "Emma looks at the shoes. Emma puts them on.",
    "Emma gets the coat. Emma zips the coat.",
    "Emma holds the cup. Emma sips the milk.",
    "Emma washes her hands. The tap is warm.",
    "Emma sits and waits. Emma has the cup. Sitting counts.",
    "Emma spills the cup. Oops is fine.",
    "Emma has a go. Emma sits on the potty.",
    "Mum waits by the door. Mum has the book.",
    "Emma hears a plink. Emma has a smile.",
    "Emma sits and it works. Emma looks at the list.",
    "Emma ticks the list. Mum has a grin.",
    "Emma shows the list to Mum.",
    "Emma holds the pen. I'm the one who decides. And I decided.",
]

SPREAD_SCENES = [
    "Emma stands in the kitchen looking at the paper list on the fridge.",
    "Emma sits in the hallway by the door, putting on her shoes. The shoes are red.",
    "Emma zips her coat in the hallway; the coat hangs on a peg.",
    "Emma holds a cup at the kitchen table and sips milk.",
    "Emma washes her hands at the sink; the tap runs warm. Mum is beside her.",
    "Emma sits on the potty in the bathroom, waiting calmly with a cup of water.",
    "Emma spills the cup of water on the kitchen floor; Mum smiles and fetches a cloth.",
    "Emma sits on the potty again, bunny beside her.",
    "Mum waits by the bathroom door holding a picture book.",
    "Emma hears a small plink from the potty and smiles; drawn from the waist up.",
    "Emma sits and looks at the list taped to the bathroom wall.",
    "Emma ticks the potty on the paper list on the fridge; Mum grins.",
    "Emma shows the paper list on the fridge to Mum in the kitchen.",
    "Emma holds a cup in the kitchen; the paper list on the fridge has five ticks.",
]


def halves_scene(n: int) -> str:
    """One scene for the WHOLE double-page picture: what is on its left half and on its right half."""
    scene = SPREAD_SCENES[n - 1]
    calm = "left" if n % 2 == 1 else "right"
    action = "right" if calm == "left" else "left"
    return (
        f"{scene} On the {calm} side of the picture there is a wide, quiet, plain area; "
        f"on the {action} side Emma is close with her face and hands clear."
    )


def emma_inputs() -> dict:
    return {
        "title_id": TITLE_ID,
        "route": "morning_light",
        "age_band": "2-3.5",
        "name": NAME,
        "age": "3",
        "pronouns": "she",
        "gender": "female",
        "carer": {"name": "Sam", "role": "Mum"},
        "home_type": "house",
        "comfort_object": "bunny",
        "slots": {"potty_or_seat": "potty"},
        "dedication_text": "For Emma, from Mum and Dad",
        "order_id": "EMMA-TEST-001",
        "start_mode": "full",
        "page_count": 32,
    }


def emma_story(inputs: dict | None = None) -> dict:
    inputs = inputs or emma_inputs()
    title = get_title(inputs["title_id"])
    locked = _build_locked(title, inputs)
    spreads = [
        {
            "n": i + 1,
            "text": SPREAD_TEXTS[i],
            "scene": halves_scene(i + 1),
            "calm_zone": "left" if i % 2 == 0 else "right",
        }
        for i in range(14)
    ]
    return {
        "title_id": inputs["title_id"],
        "title": locked["title"],
        "locked": locked,
        "spreads": spreads,
        "blurb_draft": title["blurb"],
        "recurring_outfit": title["outfit"],
        "supporting_characters": [],
        "anchor": title.get("anchor"),
        "must_not_show": title.get("must_not_show"),
    }


def emma_pages(job_dir: Path, inputs: dict | None = None, story: dict | None = None) -> list:
    inputs = inputs or emma_inputs()
    story = story or emma_story(inputs)
    (job_dir / "story").mkdir(parents=True, exist_ok=True)
    return compile_pages(None, job_dir, inputs, story)


def emma_bundle(job_dir: Path):
    inputs = emma_inputs()
    story = emma_story(inputs)
    pages = emma_pages(job_dir, inputs, story)
    return inputs, story, pages


def clone(obj):
    return copy.deepcopy(obj)


# Test spread art: 2840 x 1440 px (the 426 x 216 mm aspect). Each page half is exactly 1440 x 1440 px,
# and the picture is 2840 px across 426 mm = ~169 ppi (above the 150 fail line, under the 300 target).
SPREAD_W_PX = 2840
SPREAD_H_PX = 1440
HALF_PX = 1440


def _art(path: Path, width: int, height: int, tone: tuple, dark: bool = False) -> None:
    """Flat-ish test art: a light calm tone, a mid-tone blob on each half, optional dark lower band.

    The fold (x = width / 2) is left clear on purpose.
    """
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), tone)
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.30, height * 0.1, width * 0.45, height * 0.45), fill=(150, 120, 90))
    draw.ellipse((width * 0.60, height * 0.1, width * 0.80, height * 0.45), fill=(110, 140, 170))
    if dark:
        draw.rectangle((0, height // 2, width, height), fill=(30, 40, 60))
    image.save(path, format="PNG", compress_level=1)


def write_emma_art(
    job_dir: Path, width: int = SPREAD_W_PX, dark_spreads: tuple = (), cover_size: int = 1400
) -> None:
    """14 spread images (one wide ``art.png`` each), cover art and the title spot."""
    height = round(width * SPREAD_H_PX / SPREAD_W_PX)
    for n in range(1, 15):
        tone = (236 + (n % 3) * 4, 228 + (n % 4) * 3, 205 + (n % 5) * 4)
        _art(
            job_dir / "spreads" / f"spread-{n:02d}" / "art.png",
            width,
            height,
            tone,
            dark=n in dark_spreads,
        )
    _art(job_dir / "cover" / "front_art.png", cover_size, cover_size, (240, 224, 196))
    _art(job_dir / "cover" / "back_art.png", cover_size, cover_size, (244, 234, 214))
    _art(job_dir / "title_page" / "spot.png", 512, 512, (250, 246, 239))


def build_emma_job(job_dir: Path, width: int = SPREAD_W_PX, dark_spreads: tuple = ()):
    """(inputs, story, pages) with all art written; ready for build_pdf."""
    inputs, story, pages = emma_bundle(job_dir)
    write_emma_art(job_dir, width, dark_spreads)
    return inputs, story, pages

def persist_emma_job(job_dir: Path, inputs: dict, story: dict, pages: list, pdf_path: Path) -> None:
    """Write the job files the QA / app layers read (inputs, job.json, story, pages)."""
    from pipeline.job_log import write_json

    write_json(job_dir / "inputs.json", inputs)
    write_json(
        job_dir / "job.json",
        {"job_id": job_dir.name, "name": inputs["name"], "status": "complete", "pdf": pdf_path.relative_to(job_dir).as_posix()},
    )
    write_json(job_dir / "story" / "rebuilt_story.json", story)
    write_json(job_dir / "story" / "pages.json", {"pages": pages})
