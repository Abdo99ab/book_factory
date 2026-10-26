"""Native-resolution page-pair illustration (spec §8.3).

One spread = two square page images (``art-left.png`` then ``art-right.png``). The right
page is generated with the left page as a reference so the room, light and anchor match
across the page turn. Nothing here ever resizes up: the model's native pixels are placed
in the 216 mm box later, and ``effective_ppi`` is recorded so QA can warn / fail.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from .brand import ROUTE_PRESETS, SAFE_MM, SHARED_NEGATIVES
from .catalogue import get_title
from .constants import (
    BACK_QR_SAFE_CORNER,
    BACK_QR_SAFE_MM,
    COVER_HEIGHT_MM,
    IMAGE_QUALITY,
    IMAGE_SIZE,
    NO_TEXT_RULE,
    PAGE_WITH_BLEED_MM,
    PPI_FAIL,
    PPI_WARN,
    effective_ppi,
    resolution_status,
)
from .job_log import append_event, read_json, write_json
from .memory import format_memory_for_prompt
from .openai_util import image_edit
from .pages import spread_art_path, spread_dir

SIDES = ("left", "right")


class PrintResolutionError(RuntimeError):
    """Raised when native art is below the fail threshold (< PPI_FAIL ppi)."""


# --- prompt building ---------------------------------------------------------------------------


def _route_preset(inputs: dict) -> dict:
    return ROUTE_PRESETS[inputs.get("route") or "morning_light"]


def _cast_block(inputs: dict, story: dict) -> str:
    hero = inputs.get("name") or "the hero"
    outfit = story.get("recurring_outfit") or ""
    carer = inputs.get("carer") or {}
    carer_line = ""
    if carer.get("name") or carer.get("role"):
        carer_line = (
            f"The carer ({carer.get('role', 'carer')}, {carer.get('name', '')}) may appear, "
            "drawn identically on every page, never with an unhappy face."
        )
    comfort = inputs.get("comfort_object") or ""
    supporting = story.get("supporting_characters") or []
    if supporting:
        support = "APPROVED supporting characters only:\n" + "\n".join(
            f"- {c.get('name')}: {c.get('description')}" for c in supporting
        )
    else:
        support = (
            "No other named characters: no siblings, pets, mascots or plush animals beyond "
            "the comfort object."
        )
    lines = [
        "CAST LOCK (critical):",
        f"Hero: {hero}, age {inputs.get('age')} — match the character sheet exactly "
        "(face, skin tone, hair, proportions). Never a different child; never older or younger.",
        f"Locked hero outfit (same on every page): {outfit}",
    ]
    if comfort:
        lines.append(f"Comfort object (same on every page it appears): {comfort}")
    if carer_line:
        lines.append(carer_line)
    lines.append(support)
    return "\n".join(lines)


def _anchor_block(title: dict, anchor_state) -> str:
    anchor = title.get("anchor") or {}
    description = anchor.get("description")
    if not description:
        return ""
    lines = [f"ANCHOR OBJECT (drawn identically on every spread it belongs to): {description}."]
    if anchor_state is not None:
        if anchor.get("state_kind") == "ticks":
            lines.append(
                f"State on this spread: exactly {anchor_state} tick(s) on the list — not more, "
                "not fewer; ticks never reset."
            )
        else:
            lines.append(f"State on this spread: {anchor_state}.")
    return "\n".join(lines)


def _composition_block(side: str, calm_zone: str) -> str:
    gutter_edge = "RIGHT" if side == "left" else "LEFT"
    trim = SAFE_MM["trim"]
    gutter = SAFE_MM["gutter"]
    base = (
        f"Composition: ONE square 1:1 page, full-bleed ({PAGE_WITH_BLEED_MM:g} mm including bleed). "
        f"This is the {side.upper()} page of a facing-page spread. "
        f"Its {gutter_edge} edge is the gutter: no face, hands or key props within "
        f"{gutter} mm of the {gutter_edge} edge, and nothing important within {trim} mm "
        "of the other edges. No face may cross the gutter."
    )
    if side == calm_zone:
        zone = (
            f"Calm zone: the {calm_zone.upper()} page carries the story text. Keep a large, quiet, "
            "uncluttered area (soft background only, no faces, no small details) in the lower "
            "half of this page where the text will be set in code."
        )
    else:
        zone = (
            f"Calm zone: the {calm_zone.upper()} page carries the text, so this {side.upper()} page "
            "carries the main action and the child's face, clearly visible."
        )
    return f"{base}\n{zone}"


def _spread_prompt(
    spread: dict,
    side: str,
    inputs: dict,
    story: dict,
    edit_instruction: str | None = None,
    references: list[str] | None = None,
) -> str:
    """Prompt for one page of a spread. Brand route + catalogue only: no Theme/Purpose lines."""
    title = get_title(inputs["title_id"])
    preset = _route_preset(inputs)
    n = spread.get("spread") or spread.get("n")
    calm_zone = spread.get("calm_zone") or ("left" if int(n) % 2 == 1 else "right")
    must_not_show = title.get("must_not_show") or story.get("must_not_show") or []

    parts = [
        preset["preamble"],
        f"Illustration for spread {n} of 14, {side.upper()} page.",
        f"Hero: {inputs['name']} (age {inputs['age']}).",
        _cast_block(inputs, story),
    ]
    anchor = _anchor_block(title, spread.get("anchor_state"))
    if anchor:
        parts.append(anchor)
    parts.append(f"Scene:\n{spread.get('scene') or ''}")
    if references:
        parts.append("Reference images, in order:\n" + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(references)))
    parts.append(_composition_block(side, calm_zone))
    if must_not_show:
        parts.append("MUST NOT SHOW (binding): " + "; ".join(must_not_show) + ".")
    parts.append(f"Style negatives: {preset['negatives']}. {SHARED_NEGATIVES}.")
    if inputs.get("art_notes"):
        parts.append(f"Operator art notes: {inputs['art_notes']}")
    directive = (edit_instruction or "").strip()
    if directive:
        parts.append(
            "OPERATOR CHANGE REQUEST (must be clearly visible in the illustration):\n"
            f"{directive}\n"
            "Apply this even if the locked outfit or character reference omits it."
        )
    memory = format_memory_for_prompt()
    if memory:
        parts.append(memory)
    parts.append(NO_TEXT_RULE)
    return "\n\n".join(p for p in parts if p)


# --- native pixels + resolution events ---------------------------------------------------------


def _decode(result) -> bytes:
    return base64.b64decode(result.data[0].b64_json)


def _png_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def _record_resolution(
    job_dir: Path, label: str, width: int, box_mm: float, **extra
) -> dict:
    ppi = effective_ppi(width, box_mm)
    status = resolution_status(ppi)
    info = {"effective_ppi": ppi, "resolution_status": status, "pixel_width": width}
    if status == "warning":
        append_event(
            job_dir,
            "print_resolution_warning",
            f"{label}: {width}px across {box_mm:g} mm = {ppi} ppi (below {PPI_WARN}).",
            effective_ppi=ppi,
            **extra,
        )
    elif status == "fail":
        append_event(
            job_dir,
            "print_resolution_fail",
            f"{label}: {width}px across {box_mm:g} mm = {ppi} ppi (below {PPI_FAIL}).",
            effective_ppi=ppi,
            **extra,
        )
    return info


def read_spread_meta(job_dir: Path, n: int) -> dict | None:
    return read_json(spread_dir(job_dir, n) / "spread.json")


def spread_art_exists(job_dir: Path, n: int) -> bool:
    return all(spread_art_path(job_dir, n, side).is_file() for side in SIDES)


def min_effective_ppi(job_dir: Path, spread_numbers=range(1, 15)) -> float | None:
    values = []
    for n in spread_numbers:
        meta = read_spread_meta(job_dir, n)
        if meta and meta.get("effective_ppi") is not None:
            values.append(float(meta["effective_ppi"]))
    return min(values) if values else None


# --- spreads -------------------------------------------------------------------------------------


def _reference_images(job_dir: Path, inputs: dict, spread: dict, side: str, left_path: Path | None):
    """(paths, labels) for images.edit: hero sheet, [left page], anchor, set sheet."""
    paths: list[Path] = []
    labels: list[str] = []
    character = job_dir / "character" / "character.png"
    paths.append(character)
    labels.append("hero character sheet (face, hair, skin, outfit — identical on every page)")
    if side == "right" and left_path is not None and left_path.is_file():
        paths.append(left_path)
        labels.append(
            "the LEFT page of this same spread, already drawn — continue the same room, light, "
            "palette and anchor object across the page turn; do not copy its composition"
        )
    anchor = job_dir / "anchor" / "anchor.png"
    if anchor.is_file():
        paths.append(anchor)
        labels.append("anchor object sheet (draw the anchor exactly like this)")
    sets = sorted((job_dir / "sets").glob("*.png")) if (job_dir / "sets").is_dir() else []
    if sets:
        n = int(spread.get("spread") or spread.get("n") or 1)
        chosen = sets[min(len(sets) - 1, (n - 1) * len(sets) // 14)]
        paths.append(chosen)
        labels.append(f"set sheet '{chosen.stem}' (reuse this location's look)")
    return paths, labels


def generate_spread_art(
    client,
    job_dir: Path,
    spread: dict,
    inputs: dict,
    story: dict,
    character_path: Path,
    edit_instruction: str | None = None,
) -> dict:
    """Generate ``art-left`` then ``art-right`` (left as reference). Returns the spread meta."""
    if not Path(character_path).is_file():
        raise FileNotFoundError(f"Character sheet missing for spread {spread.get('spread')}")
    n = int(spread.get("spread") or spread.get("n"))
    calm_zone = spread.get("calm_zone") or ("left" if n % 2 == 1 else "right")
    sdir = spread_dir(job_dir, n)
    sdir.mkdir(parents=True, exist_ok=True)

    meta = {
        "spread": n,
        "calm_zone": calm_zone,
        "anchor_state": spread.get("anchor_state"),
        "scene": spread.get("scene"),
        "text": spread.get("text"),
        "edit_instruction": edit_instruction,
        "image_size_requested": IMAGE_SIZE,
        "image_quality": IMAGE_QUALITY,
        "status": "generating",
        "pages": {},
    }
    write_json(sdir / "spread.json", meta)
    append_event(job_dir, "spread_start", f"Illustrating spread {n} (pages {2 * n}–{2 * n + 1}).", spread=n)

    left_path: Path | None = None
    for side in SIDES:
        page_number = 2 * n + (0 if side == "left" else 1)
        paths, labels = _reference_images(job_dir, inputs, spread, side, left_path)
        prompt = _spread_prompt(
            {**spread, "spread": n, "calm_zone": calm_zone},
            side,
            inputs,
            story,
            edit_instruction=edit_instruction,
            references=labels,
        )
        append_event(job_dir, "page_start", f"Illustrating page {page_number} ({side}).", page=page_number)
        result = image_edit(
            client,
            paths,
            prompt,
            size=IMAGE_SIZE,
            description=f"Spread {n} {side}",
        )
        data = _decode(result)
        width, height = _png_size(data)
        art_path = spread_art_path(job_dir, n, side)
        art_path.write_bytes(data)  # native pixels, never resized
        info = _record_resolution(
            job_dir,
            f"Page {page_number}",
            width,
            PAGE_WITH_BLEED_MM,
            page=page_number,
            spread=n,
        )
        meta["pages"][side] = {
            "number": page_number,
            "file": art_path.name,
            "width": width,
            "height": height,
            "references": [p.name for p in paths],
            "prompt": prompt,
            **info,
        }
        append_event(job_dir, "page_done", f"Page {page_number} art saved ({width}px, {info['effective_ppi']} ppi).", page=page_number)
        if side == "left":
            left_path = art_path
        write_json(sdir / "spread.json", meta)

    ppis = [p["effective_ppi"] for p in meta["pages"].values()]
    meta["effective_ppi"] = min(ppis)
    meta["resolution_status"] = resolution_status(meta["effective_ppi"])
    meta["status"] = "generated"
    write_json(sdir / "spread.json", meta)
    append_event(
        job_dir,
        "spread_done",
        f"Spread {n} art saved (min {meta['effective_ppi']} ppi).",
        spread=n,
        effective_ppi=meta["effective_ppi"],
    )
    return meta


def assert_print_resolution(job_dir: Path, spread_numbers=range(1, 15)) -> float | None:
    """Raise PrintResolutionError if any spread is below PPI_FAIL; return the minimum ppi."""
    lowest = min_effective_ppi(job_dir, spread_numbers)
    if lowest is not None and lowest < PPI_FAIL:
        raise PrintResolutionError(
            f"Interior art is {lowest} ppi (< {PPI_FAIL}) in the {PAGE_WITH_BLEED_MM:g} mm box. "
            "The image model returned too few pixels; there is no upscaler. "
            "Raise IMAGE_SIZE / use a model with larger output and regenerate."
        )
    return lowest


# --- title spot + covers -------------------------------------------------------------------------


def generate_title_spot(client, job_dir: Path, inputs: dict, story: dict) -> Path:
    """Small spot illustration for page 1 (the anchor object, else the child)."""
    preset = _route_preset(inputs)
    title = get_title(inputs["title_id"])
    anchor = job_dir / "anchor" / "anchor.png"
    ref = anchor if anchor.is_file() else job_dir / "character" / "character.png"
    description = (title.get("anchor") or {}).get("description") or "the child"
    prompt = (
        f"{preset['preamble']}\n"
        f"ONE small spot illustration of: {description}.\n"
        "Centred on a plain pale paper ground (#FAF6EF) with generous empty margin on all sides; "
        "no background scene, no frame.\n"
        f"{SHARED_NEGATIVES}.\n{NO_TEXT_RULE}"
    )
    out_dir = job_dir / "title_page"
    out_dir.mkdir(parents=True, exist_ok=True)
    append_event(job_dir, "title_spot_start", "Generating title-page spot illustration.")
    result = image_edit(client, ref, prompt, size="1024x1024", description="Title spot")
    path = out_dir / "spot.png"
    path.write_bytes(_decode(result))
    write_json(out_dir / "spot.json", {"prompt": prompt, "reference": ref.name})
    append_event(job_dir, "title_spot_done", "Title-page spot saved.")
    return path


def _cover_prompt(kind: str, inputs: dict, story: dict) -> str:
    preset = _route_preset(inputs)
    title = get_title(inputs["title_id"])
    cast = _cast_block(inputs, story)
    must_not_show = title.get("must_not_show") or []
    common = (
        f"{preset['preamble']}\n{cast}\n"
        "Square 1:1, full bleed (print wraps beyond trim). One cohesive scene, not a collage.\n"
        f"MUST NOT SHOW (binding): {'; '.join(must_not_show)}.\n"
        f"{SHARED_NEGATIVES}.\n"
    )
    memory = format_memory_for_prompt()
    if memory:
        common += memory + "\n"
    if kind == "front":
        body = (
            f"Create the FRONT COVER illustration only for '{story['title']}'. "
            "The child's face is the largest element and clearly visible. "
            "Keep the TOP 15% soft and plain (a family colour band sits there) and a calm, "
            "quiet area in the upper third for the title. Keep the lower centre calm for a "
            "logo. No faces within 10 mm of the trim edges."
        )
    else:
        corner = BACK_QR_SAFE_CORNER.replace("_", "-")
        body = (
            f"Create the BACK COVER illustration only for '{story['title']}'. "
            "Same world, palette and cast as the front. A quiet epilogue moment showing the "
            "anchor object. Keep the upper two thirds a calm, soft area for the question, "
            "blurb and honesty line. "
            f"Keep the {corner} corner (about {BACK_QR_SAFE_MM:g} mm square) soft and empty."
        )
    face_lock = (
        "FACE LOCK: the supplied reference image is the hero character sheet. Match the "
        "child's exact face, hair, skin tone and proportions. Do not invent a different child."
    )
    return f"{body}\n\n{common}\n{NO_TEXT_RULE}\n{face_lock}\n"


def generate_cover_panels(client, job_dir: Path, inputs: dict, story: dict, character_path: Path):
    """Front and back cover art, text-free, character-locked. Typography is added in pdf_build."""
    if not Path(character_path).is_file():
        raise FileNotFoundError("Character sheet missing; cannot generate cover panels.")
    cover_dir = job_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)

    refs = [Path(character_path)]
    anchor = job_dir / "anchor" / "anchor.png"
    back_refs = refs + ([anchor] if anchor.is_file() else [])

    front_prompt = _cover_prompt("front", inputs, story)
    back_prompt = _cover_prompt("back", inputs, story)
    append_event(job_dir, "cover_start", "Generating cover art (front + back).")

    out = {}
    for kind, prompt, ref_paths in (("front", front_prompt, refs), ("back", back_prompt, back_refs)):
        result = image_edit(
            client, ref_paths, prompt, size=IMAGE_SIZE, description=f"{kind.title()} cover art"
        )
        data = _decode(result)
        width, height = _png_size(data)
        path = cover_dir / f"{kind}_art.png"
        path.write_bytes(data)
        # Cover art is scaled to fill the 248 mm cover height (art stays square), never upscaled in pixels.
        info = _record_resolution(job_dir, f"{kind.title()} cover", width, COVER_HEIGHT_MM, cover=kind)
        out[kind] = {"file": path.name, "width": width, "height": height, **info}

    write_json(
        cover_dir / "cover.json",
        {
            "title": story["title"],
            "front_prompt": front_prompt,
            "back_prompt": back_prompt,
            "front_art": "front_art.png",
            "back_art": "back_art.png",
            "character_locked": True,
            "art": out,
            "effective_ppi": min(v["effective_ppi"] for v in out.values()),
        },
    )
    append_event(job_dir, "cover_done", "Front and back cover art saved (both character-locked).")
    return cover_dir / "front_art.png", cover_dir / "back_art.png"
