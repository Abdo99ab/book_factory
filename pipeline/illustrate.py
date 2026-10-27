"""Native-resolution true-spread illustration (spec §8.3).

One spread = ONE continuous landscape picture (``spreads/spread-NN/art.png``, about 2:1). It is cut
into the left and right pages only at PDF time, so the room, light and anchor are the same picture
by construction (no pair-matching, no duplicate-pair check). The model's frame is centre-cropped to
``SPREAD_ASPECT`` (``pages.fit_to_spread``) and NEVER resized up; ``effective_ppi`` over the 426 mm
spread is recorded so QA can warn / fail.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from .brand import ROUTE_PRESETS, SAFE_MM, SHARED_NEGATIVES
from . import imagecheck, qa
from .catalogue import get_title, load_story_beats, resolve_carer_text
from .constants import (
    BACK_QR_SAFE_CORNER,
    BACK_QR_SAFE_MM,
    COVER_HEIGHT_MM,
    CROP_BAND_FRACTION,
    GUTTER_BAND_MM,
    IMAGE_QUALITY,
    IMAGE_SIZE,
    NO_TEXT_RULE,
    PPI_FAIL,
    PPI_WARN,
    SPREAD_ASPECT,
    SPREAD_HEIGHT_MM,
    SPREAD_WIDTH_MM,
    effective_ppi,
    resolution_status,
)
from .job_log import append_event, read_json, write_json
from .memory import format_memory_for_prompt
from .openai_util import image_edit, image_quality
from .pages import clear_spread_derivatives, fit_crop_box, spread_art_path, spread_dir

SIDES = ("left", "right")


class PrintResolutionError(RuntimeError):
    """Raised when native art is below the fail threshold (< PPI_FAIL ppi)."""


# --- prompt building ---------------------------------------------------------------------------


def _route_preset(inputs: dict) -> dict:
    return ROUTE_PRESETS[inputs.get("route") or "morning_light"]


def _carer_signature(inputs: dict, story: dict) -> str:
    """Catalogue-locked carer detail (e.g. a yellow scarf), from the story or the beats file header."""
    signature = (story.get("carer_signature") or "").strip()
    if not signature:
        try:
            signature = load_story_beats(inputs["title_id"])["meta"].get("carer_signature") or ""
        except Exception:  # noqa: BLE001 - legacy jobs / unknown title: no signature
            signature = ""
    return resolve_carer_text(signature, inputs).strip()


def _cast_block(inputs: dict, story: dict, include_carer: bool = True) -> str:
    hero = inputs.get("name") or "the hero"
    outfit = story.get("recurring_outfit") or ""
    carer = inputs.get("carer") or {}
    carer_line = ""
    if not include_carer:
        carer_line = (
            "The carer is never on the front cover: show the child alone, with no adult, "
            "no hand, no arm and no shadow of an adult."
        )
    elif carer.get("name") or carer.get("role"):
        carer_line = (
            f"The carer ({carer.get('role', 'carer')}, {carer.get('name', '')}) may appear, "
            "drawn identically on every page, never with an unhappy face."
        )
        signature = _carer_signature(inputs, story)
        if signature:
            carer_line += (
                f"\nThe carer always wears/carries: {signature} \u2014 identical on every page."
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


def _composition_block(calm_zone: str) -> str:
    """Brief for ONE continuous double-page picture: fold safety, crop bands and the calm half."""
    calm = calm_zone.upper()
    action = ("RIGHT" if calm_zone == "left" else "LEFT")
    trim = SAFE_MM["trim"]
    gutter = f"{GUTTER_BAND_MM:g}"
    band = round(100 * 2 * GUTTER_BAND_MM / SPREAD_WIDTH_MM)
    crop = round(100 * CROP_BAND_FRACTION)
    return "\n".join(
        [
            f"COMPOSITION (binding): ONE continuous double-page illustration: a single wide picture, "
            f"about 2:1 landscape ({SPREAD_WIDTH_MM:g} x {SPREAD_HEIGHT_MM:g} mm including bleed), "
            "NOT two separate pictures. A vertical fold runs down the exact centre of the frame; the "
            "picture is cut there at print time, so it must read as one room and one moment, with the "
            "floor, wall and light running straight across the fold.",
            f"FOLD AND EDGE SAFETY: no face, hands or key prop within {gutter} mm of the centre fold "
            f"(a band about {band}% of the picture width, centred on the fold). Nothing important "
            f"within {trim:g} mm of the outer left and right edges. The top and bottom {crop}% of the "
            f"frame may be cropped away: keep heads, faces and the anchor object out of those bands.",
            f"CALM ZONE: the {calm} half carries the story text. Keep it quiet: a large, plain, "
            "uncluttered area (soft background only, no faces, no small details) filling the lower two "
            "thirds of that half, where the text is set in code. "
            f"The {action} half carries the action: the child's face clearly visible, centred in that "
            "half, with the hands or the key prop in view. It carries no story text.",
        ]
    )


def _spread_prompt(
    spread: dict,
    inputs: dict,
    story: dict,
    edit_instruction: str | None = None,
    references: list[str] | None = None,
    fix_zones: list[str] | None = None,
) -> str:
    """Prompt for ONE spread (one wide image). Brand route + catalogue only: no Theme/Purpose lines."""
    title = get_title(inputs["title_id"])
    preset = _route_preset(inputs)
    n = spread.get("spread") or spread.get("n")
    calm_zone = spread.get("calm_zone") or ("left" if int(n) % 2 == 1 else "right")
    must_not_show = title.get("must_not_show") or story.get("must_not_show") or []

    parts = [
        preset["preamble"],
        f"Illustration for spread {n} of 14 (pages {2 * int(n)}-{2 * int(n) + 1}), one double-page picture.",
        _composition_block(calm_zone),
        f"Hero: {inputs['name']} (age {inputs['age']}).",
        _cast_block(inputs, story),
    ]
    anchor = _anchor_block(title, spread.get("anchor_state"))
    if anchor:
        parts.append(anchor)
    scene = (spread.get("scene") or "").strip()
    parts.append(
        "The moment (ONE scene across the whole frame; what is on its left half and its right half "
        f"is described together):\n{scene}"
    )
    if references:
        parts.append("Reference images, in order:\n" + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(references)))
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
    if fix_zones:
        parts.append(
            "RETRY: the previous attempt failed the layout check. Fix exactly these: "
            + "; ".join(fix_zones)
            + "."
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
    return spread_art_path(job_dir, n).is_file()


def min_effective_ppi(job_dir: Path, spread_numbers=range(1, 15)) -> float | None:
    """Lowest effective ppi over the spread art (measured from the files, and the recorded value)."""
    values = []
    for n in spread_numbers:
        meta = read_spread_meta(job_dir, n)
        if meta and meta.get("effective_ppi") is not None:
            values.append(float(meta["effective_ppi"]))
        path = spread_art_path(job_dir, n)
        if path.is_file():
            with Image.open(path) as image:
                values.append(float(effective_ppi(image.size[0], SPREAD_WIDTH_MM)))
    return min(values) if values else None


# --- spreads -------------------------------------------------------------------------------------


def _reference_images(job_dir: Path, inputs: dict, spread: dict):
    """(paths, labels) for images.edit: hero sheet, anchor sheet, set sheet. Nothing else."""
    paths: list[Path] = []
    labels: list[str] = []
    character = job_dir / "character" / "character.png"
    paths.append(character)
    labels.append("hero character sheet (face, hair, skin, outfit — identical on every page)")
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


def _store_frame(client, job_dir: Path, n: int, data: bytes) -> dict:
    """Write ``art.png`` (the model frame centre-cropped to the spread aspect, never resized).

    Returns the frame facts plus the fold / edge / crop-band failures found by the face detector
    (``failures`` is empty when no face was found or the detector could not run).
    """
    art_path = spread_art_path(job_dir, n)
    raw_size = _png_size(data)
    box = fit_crop_box(raw_size)
    full = box == (0, 0, raw_size[0], raw_size[1])
    frame_path = art_path.with_name("_frame.png")
    if full:
        art_path.write_bytes(data)  # native bytes untouched
        detect_path = art_path
    else:
        frame_path.write_bytes(data)
        with Image.open(io.BytesIO(data)) as frame:
            frame.crop(box).save(art_path, format="PNG")
        detect_path = frame_path
    try:
        faces = qa.detect_faces(client, detect_path)
    finally:
        frame_path.unlink(missing_ok=True)
    failures = (
        imagecheck.spread_layout_failures(faces, raw_size, box) if faces is not None else []
    )
    return {
        "raw_size": raw_size,
        "crop_box": list(box),
        "width": box[2] - box[0],
        "height": box[3] - box[1],
        "faces": faces,
        "fold_checked": faces is not None,
        "failures": failures,
    }


def generate_spread_art(
    client,
    job_dir: Path,
    spread: dict,
    inputs: dict,
    story: dict,
    character_path: Path,
    edit_instruction: str | None = None,
) -> dict:
    """Generate ``art.png`` for one spread: ONE image call, a fold check and a calm-zone check.

    A frame whose faces cross the fold, the outer edge or the top / bottom crop bands is
    regenerated ONCE with the failing zone named; if it still fails the art is kept and
    ``fold_warning`` is emitted. A busy calm half only emits ``calm_zone_warning`` (no regen).
    """
    if not Path(character_path).is_file():
        raise FileNotFoundError(f"Character sheet missing for spread {spread.get('spread')}")
    n = int(spread.get("spread") or spread.get("n"))
    calm_zone = spread.get("calm_zone") or ("left" if n % 2 == 1 else "right")
    sdir = spread_dir(job_dir, n)
    sdir.mkdir(parents=True, exist_ok=True)
    clear_spread_derivatives(job_dir, n)  # cached UI halves belong to the OLD picture

    meta = {
        "spread": n,
        "calm_zone": calm_zone,
        "anchor_state": spread.get("anchor_state"),
        "scene": spread.get("scene"),
        "text": spread.get("text"),
        "edit_instruction": edit_instruction,
        "size_requested": IMAGE_SIZE,
        "image_quality": image_quality(),
        "status": "generating",
        "file": "art.png",
    }
    write_json(sdir / "spread.json", meta)
    append_event(job_dir, "spread_start", f"Illustrating spread {n} (pages {2 * n}–{2 * n + 1}).", spread=n)

    paths, labels = _reference_images(job_dir, inputs, spread)
    working = {**spread, "spread": n, "calm_zone": calm_zone}
    fix_zones: list[str] | None = None
    attempts = 0
    frame: dict = {}
    prompt = ""
    for attempt in (1, 2):
        attempts = attempt
        prompt = _spread_prompt(
            working,
            inputs,
            story,
            edit_instruction=edit_instruction,
            references=labels,
            fix_zones=fix_zones,
        )
        result = image_edit(client, paths, prompt, size=IMAGE_SIZE, description=f"Spread {n}")
        frame = _store_frame(client, job_dir, n, _decode(result))
        if not frame["failures"]:
            break
        if attempt == 1:
            fix_zones = [f["message"] for f in frame["failures"]]
            append_event(
                job_dir,
                "fold_retry",
                f"Spread {n} fold check failed; regenerating once: " + "; ".join(f["zone"] for f in frame["failures"]),
                spread=n,
                zones=[f["zone"] for f in frame["failures"]],
            )
    if frame["failures"]:
        append_event(
            job_dir,
            "fold_warning",
            f"Spread {n} still breaks the fold / edge rules after one retry (kept): "
            + "; ".join(f["message"] for f in frame["failures"]),
            spread=n,
            zones=[f["zone"] for f in frame["failures"]],
        )

    art_path = spread_art_path(job_dir, n)
    calm_stats = imagecheck.calm_zone_stats(art_path, calm_zone)
    calm_busy = imagecheck.is_busy(calm_stats)
    if calm_busy:
        append_event(
            job_dir,
            "calm_zone_warning",
            f"Spread {n}: the {calm_zone} half is busy where the text will sit "
            f"(std {calm_stats['std']:.2f}, p10 {calm_stats['p10']:.2f}).",
            spread=n,
        )

    info = _record_resolution(
        job_dir,
        f"Spread {n}",
        frame["width"],
        SPREAD_WIDTH_MM,
        spread=n,
    )
    meta.update(
        {
            "size_received": f"{frame['raw_size'][0]}x{frame['raw_size'][1]}",
            "crop_box": frame["crop_box"],
            "width": frame["width"],
            "height": frame["height"],
            "aspect": round(frame["width"] / frame["height"], 4),
            "target_aspect": round(SPREAD_ASPECT, 4),
            "references": [p.name for p in paths],
            "prompt": prompt,
            "attempts": attempts,
            "fold_checked": frame["fold_checked"],
            "fold_failures": [f["zone"] for f in frame["failures"]],
            "faces": frame["faces"],
            "calm_zone_busy": calm_busy,
            "calm_zone_stats": calm_stats,
            "status": "generated",
            **info,
        }
    )
    write_json(sdir / "spread.json", meta)
    append_event(
        job_dir,
        "spread_done",
        f"Spread {n} art saved ({frame['width']}px, {meta['effective_ppi']} ppi).",
        spread=n,
        effective_ppi=meta["effective_ppi"],
    )
    return meta


def assert_print_resolution(job_dir: Path, spread_numbers=range(1, 15)) -> float | None:
    """Return the minimum ppi. Raise only when ENFORCE_PRINT_PPI is on and below PPI_FAIL."""
    from .constants import ENFORCE_PRINT_PPI

    lowest = min_effective_ppi(job_dir, spread_numbers)
    if (
        ENFORCE_PRINT_PPI
        and lowest is not None
        and lowest < PPI_FAIL
    ):
        raise PrintResolutionError(
            f"Interior art is {lowest} ppi (< {PPI_FAIL}) across the {SPREAD_WIDTH_MM:g} mm spread. "
            "The image model returned too few pixels; there is no upscaler. "
            "Raise IMAGE_SIZE / use a model with larger output and regenerate."
        )
    if lowest is not None and lowest < PPI_FAIL:
        append_event(
            job_dir,
            "print_resolution_ignored",
            f"Lowest art is {lowest} ppi (< {PPI_FAIL}); continuing because ENFORCE_PRINT_PPI is off.",
            effective_ppi=lowest,
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


def _cover_prompt(kind: str, inputs: dict, story: dict, fix_zones: list[str] | None = None) -> str:
    preset = _route_preset(inputs)
    title = get_title(inputs["title_id"])
    cast = _cast_block(inputs, story, include_carer=(kind != "front"))
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
            f"Create the FRONT COVER illustration only for '{story['title']}'.\n"
            "COMPOSITION (binding): the child alone, centred left-to-right, facing the viewer with the "
            "face fully visible. The child's head and face sit at 50-62% of the picture height, "
            "measured from the top, with shoulders and upper body below. The child is the largest "
            "element. The carer is never on the front cover.\n"
            "BACKGROUND: calm and soft, low contrast, a gentle wash with at most one or two large "
            "simple shapes. No clutter, no pattern, no small details.\n"
            "KEEP PLAIN: the TOP 30% of the picture is a plain, soft, empty background (a family "
            "colour band and then the title are printed over it). The BOTTOM 15%, in the centre 40% "
            "of the width, is plain and empty too (the logo is printed there).\n"
            "NO EDGE CUTS: nothing may be cut off by the left, right or top edge. The whole head, "
            "hair and any prop stay inside the picture with clear margin; no face within 10 mm of "
            "any trim edge."
        )
    else:
        corner = BACK_QR_SAFE_CORNER.replace("_", "-")
        body = (
            f"Create the BACK COVER illustration only for '{story['title']}'.\n"
            "Same world, palette and cast as the front.\n"
            "KEEP PLAIN: the UPPER TWO THIRDS of the picture is a plain, calm, soft wash with no "
            "figures, objects or detail (the question, blurb and honesty line are printed there). "
            "A quiet epilogue moment showing the anchor object sits in the LOWER THIRD only.\n"
            f"QR KEEP-CLEAR: the {corner} corner (about {BACK_QR_SAFE_MM:g} mm square) stays "
            "completely plain and empty.\n"
            "NO EDGE CUTS: nothing important touches or is cut by the edges."
        )
    if fix_zones:
        body += (
            "\nRETRY: the previous attempt failed the layout check. Fix exactly these: "
            + "; ".join(fix_zones)
            + "."
        )
    face_lock = (
        "FACE LOCK: the supplied reference image is the hero character sheet. Match the "
        "child's exact face, hair, skin tone and proportions. Do not invent a different child."
    )
    return f"{body}\n\n{common}\n{NO_TEXT_RULE}\n{face_lock}\n"


def _generate_cover_image(client, kind: str, prompt: str, ref_paths: list[Path], path: Path):
    result = image_edit(
        client, ref_paths, prompt, size="square", description=f"{kind.title()} cover art"
    )
    data = _decode(result)
    path.write_bytes(data)
    return data


def _front_layout_check(client, path: Path) -> dict:
    faces = qa.detect_faces(client, path)
    return imagecheck.check_front_cover_layout(path, faces)


def generate_cover_panels(client, job_dir: Path, inputs: dict, story: dict, character_path: Path):
    """Front and back cover art, text-free, character-locked. Typography is added in pdf_build.

    The front art is layout-checked (face position, calm top band/title zone, calm logo zone). A
    failing front is regenerated ONCE with the failing zone named; if it still fails the art is kept,
    the soft plate is forced under the title/logo and ``cover_layout_warning`` is emitted.
    """
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
    layout: dict = {}
    for kind, prompt, ref_paths in (("front", front_prompt, refs), ("back", back_prompt, back_refs)):
        path = cover_dir / f"{kind}_art.png"
        data = _generate_cover_image(client, kind, prompt, ref_paths, path)
        if kind == "front":
            check = _front_layout_check(client, path)
            layout = {"first_attempt": check["failures"], "regenerated": False}
            if not check["passed"]:
                append_event(
                    job_dir,
                    "cover_layout_retry",
                    "Front cover layout check failed; regenerating once: " + "; ".join(check["failures"]),
                    failures=check["failures"],
                )
                front_prompt = _cover_prompt("front", inputs, story, fix_zones=check["failures"])
                data = _generate_cover_image(client, kind, front_prompt, ref_paths, path)
                check = _front_layout_check(client, path)
                layout["regenerated"] = True
            layout.update(
                {
                    "failures": check["failures"],
                    "face_checked": check["face_checked"],
                    "force_soft_plate": bool(check["failures"]),
                }
            )
            if check["failures"]:
                append_event(
                    job_dir,
                    "cover_layout_warning",
                    "Front cover still breaks the layout rules after one retry; keeping the art and "
                    "forcing the soft plate: " + "; ".join(check["failures"]),
                    failures=check["failures"],
                )
        width, height = _png_size(data)
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
            "front_preview": "front_preview.png",
            "character_locked": True,
            "art": out,
            "layout": layout,
            "effective_ppi": min(v["effective_ppi"] for v in out.values()),
        },
    )
    append_event(job_dir, "cover_done", "Front and back cover art saved (both character-locked).")
    render_front_preview(job_dir, inputs, story)
    return cover_dir / "front_art.png", cover_dir / "back_art.png"


def render_front_preview(job_dir: Path, inputs: dict, story: dict) -> Path | None:
    """Composed front cover (band + title + logo over the art, exactly as the PDF draws it).

    Saved to ``cover/front_preview.png`` so the operator sees what will print, not just the raw art.
    Never raises: a preview failure must not fail the build.
    """
    try:
        from .pdf_build import render_front_preview as _render

        return _render(job_dir, inputs, story)
    except Exception as error:  # noqa: BLE001
        append_event(job_dir, "cover_preview_failed", f"Front cover preview not rendered: {error}")
        return None
