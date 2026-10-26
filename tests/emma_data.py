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
    "Emma has a cup. The list waits on the fridge. Emma can choose again tomorrow.",
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
        {"n": i + 1, "text": SPREAD_TEXTS[i], "scene": SPREAD_SCENES[i]} for i in range(14)
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


def _art(path: Path, size: int, tone: tuple, dark: bool = False) -> None:
    """Flat-ish test art: light calm tone with a darker lower-left blob so text areas are testable."""
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (size, size), tone)
    draw = ImageDraw.Draw(image)
    draw.ellipse((size * 0.55, size * 0.1, size * 0.95, size * 0.45), fill=(150, 120, 90))
    if dark:
        draw.rectangle((0, size // 2, size, size), fill=(30, 40, 60))
    image.save(path, format="PNG", compress_level=1)


def write_emma_art(job_dir: Path, size: int = 1400, dark_spreads: tuple = ()) -> None:
    """Spread art (28 pages), cover art and title spot, as the fixed mocked-OpenAI PNGs would be."""
    for n in range(1, 15):
        for side in ("left", "right"):
            tone = (236 + (n % 3) * 4, 228 + (n % 4) * 3, 205 + (n % 5) * 4)
            _art(job_dir / "spreads" / f"spread-{n:02d}" / f"art-{side}.png", size, tone, dark=n in dark_spreads)
    _art(job_dir / "cover" / "front_art.png", size, (240, 224, 196))
    _art(job_dir / "cover" / "back_art.png", size, (244, 234, 214))
    _art(job_dir / "title_page" / "spot.png", 512, (250, 246, 239))


def build_emma_job(job_dir: Path, size: int = 1400, dark_spreads: tuple = ()):
    """(inputs, story, pages) with all art written; ready for build_pdf."""
    inputs, story, pages = emma_bundle(job_dir)
    write_emma_art(job_dir, size, dark_spreads)
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
