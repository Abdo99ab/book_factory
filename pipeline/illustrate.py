"""Cover + spread art (v2). No layout validation / regen loops — generate once and save."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from .brand import ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import get_title, resolve_route
from .constants import BACK_QR_SAFE_CORNER, BACK_QR_SAFE_MM, SPREAD_COUNT
from .content import calm_zone_for
from .job_log import append_event, read_json, write_json
from .openai_util import image_edit, image_generate, largest_image_size, square_image_size


def _decode(result) -> bytes:
    return base64.b64decode(result.data[0].b64_json)


def _route_bits(inputs: dict) -> tuple[dict, dict]:
    title = get_title(inputs["title_id"])
    route = resolve_route(title)
    return title, ROUTE_PRESETS[route]


def spread_art_path(job_dir: Path, n: int) -> Path:
    return job_dir / "spreads" / f"spread-{int(n):02d}" / "art.png"


def split_spread(art_path: Path) -> tuple[Image.Image, Image.Image]:
    with Image.open(art_path) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        mid = w // 2
        return rgb.crop((0, 0, mid, h)), rgb.crop((mid, 0, w, h))


def _spread_prompt(inputs: dict, story: dict, spread: dict, character_note: str) -> str:
    title, preset = _route_bits(inputs)
    n = int(spread["n"])
    calm = spread.get("calm_zone") or calm_zone_for(n)
    action = "right" if calm == "left" else "left"
    anchor = title.get("anchor") or {}
    schedule = anchor.get("state_schedule") or []
    state = schedule[n - 1] if n - 1 < len(schedule) else schedule[-1] if schedule else 0
    must_not = "; ".join(title.get("must_not_show") or [])
    return (
        f"{preset['preamble']}\n"
        f"ONE CONTINUOUS double-page picture for spread {n} of '{story['title']}'. "
        f"Landscape, full bleed, child's eye level.\n"
        f"CALM HALF ({calm.upper()}): empty soft background / wall / sky / floor texture ONLY — "
        f"no people, no objects, no furniture details. Story text will be printed there.\n"
        f"ACTION HALF ({action.upper()}): {spread.get('scene') or 'the beat in action'}. "
        f"Child ({inputs['name']}) in {title.get('outfit')}, comfort object {inputs.get('comfort_object')}. "
        f"Carer {inputs['carer']['name']} only if the scene needs them. "
        f"Anchor object: {anchor.get('description')} at state {state}.\n"
        f"No face or important object on the centre fold. "
        f"{character_note}\n"
        f"Must not show: {must_not}. {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def generate_spread(client, job_dir: Path, inputs: dict, story: dict, character_path: Path, n: int) -> Path:
    path = spread_art_path(job_dir, n)
    if path.is_file():
        append_event(job_dir, "spread_skip", f"Spread {n} art exists; keeping.", spread=n)
        return path
    spreads = {int(s["n"]): s for s in story.get("spreads") or []}
    spread = spreads.get(int(n)) or {"n": n, "scene": "", "calm_zone": calm_zone_for(n)}
    path.parent.mkdir(parents=True, exist_ok=True)
    prompt = _spread_prompt(
        inputs,
        story,
        spread,
        "FACE LOCK: match the character sheet exactly.",
    )
    append_event(job_dir, "spread_start", f"Generating spread {n}.", spread=n)
    result = image_edit(
        client,
        [character_path],
        prompt,
        size=largest_image_size(aspect="landscape"),
        description=f"Spread {n}",
    )
    path.write_bytes(_decode(result))
    write_json(
        path.parent / "spread.json",
        {
            "n": int(n),
            "calm_zone": spread.get("calm_zone") or calm_zone_for(n),
            "scene": spread.get("scene"),
            "text": spread.get("text"),
        },
    )
    append_event(job_dir, "spread_done", f"Spread {n} saved.", spread=n)
    return path


def regenerate_spread(
    client, job_dir: Path, inputs: dict, story: dict, character_path: Path, n: int, instruction: str
) -> Path:
    path = spread_art_path(job_dir, n)
    spreads = {int(s["n"]): s for s in story.get("spreads") or []}
    spread = spreads.get(int(n)) or {"n": n, "scene": "", "calm_zone": calm_zone_for(n)}
    path.parent.mkdir(parents=True, exist_ok=True)
    base = _spread_prompt(inputs, story, spread, "FACE LOCK: match the character sheet exactly.")
    prompt = base + f"\nEDIT: {instruction.strip()}"
    refs = [character_path]
    if path.is_file():
        refs.append(path)
    append_event(job_dir, "spread_edit", f"Editing spread {n}: {instruction[:120]}", spread=n)
    result = image_edit(
        client,
        refs,
        prompt,
        size=largest_image_size(aspect="landscape"),
        description=f"Spread {n} edit",
    )
    path.write_bytes(_decode(result))
    append_event(job_dir, "spread_done", f"Spread {n} replaced.", spread=n)
    return path


def _cover_prompt(kind: str, inputs: dict, story: dict) -> str:
    title, preset = _route_bits(inputs)
    if kind == "front":
        return (
            f"{preset['preamble']}\n"
            f"FRONT COVER for '{story['title']}'. Square. Child alone, facing viewer, largest element. "
            f"Head top around 40% down. TOP 35%: empty plain wall for the title. "
            f"BOTTOM centre: plain floor for the logo. No carer. No text in art. "
            f"Outfit: {title.get('outfit')}. Comfort: {inputs.get('comfort_object')}. "
            f"{SHARED_NEGATIVES}. {preset['negatives']}."
        )
    corner = BACK_QR_SAFE_CORNER.replace("_", "-")
    return (
        f"{preset['preamble']}\n"
        f"BACK COVER for '{story['title']}'. Square. Quiet epilogue with carer and child in the middle. "
        f"BOTTOM 40%: calm open area for a white text plate (question, blurb, logo, QR). "
        f"Leave the {corner} of that band plain (~{BACK_QR_SAFE_MM:g} mm). "
        f"Outfit: {title.get('outfit')}. {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def generate_cover_panels(client, job_dir: Path, inputs: dict, story: dict, character_path: Path, *, front_only: bool = False):
    cover_dir = job_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    kinds = ("front",) if front_only else ("front", "back")
    out = {}
    for kind in kinds:
        path = cover_dir / f"{kind}_art.png"
        if path.is_file():
            append_event(job_dir, "cover_skip", f"{kind.title()} cover exists; keeping.")
            out[kind] = path
            continue
        append_event(job_dir, "cover_start", f"Generating {kind} cover.")
        result = image_edit(
            client,
            [character_path],
            _cover_prompt(kind, inputs, story),
            size=square_image_size(),
            description=f"{kind.title()} cover",
        )
        path.write_bytes(_decode(result))
        out[kind] = path
        append_event(job_dir, "cover_done", f"{kind.title()} cover saved.")
    write_json(
        cover_dir / "cover.json",
        {
            "title": story.get("title"),
            "front_art": "front_art.png",
            "back_art": "back_art.png" if (cover_dir / "back_art.png").is_file() else None,
        },
    )
    return out


def regenerate_cover(
    client, job_dir: Path, inputs: dict, story: dict, character_path: Path, kind: str, instruction: str
) -> Path:
    assert kind in {"front", "back"}
    cover_dir = job_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    path = cover_dir / f"{kind}_art.png"
    prompt = _cover_prompt(kind, inputs, story) + f"\nEDIT: {instruction.strip()}"
    refs = [character_path]
    if path.is_file():
        refs.append(path)
    append_event(job_dir, "cover_edit", f"Editing {kind} cover: {instruction[:120]}")
    result = image_edit(
        client,
        refs,
        prompt,
        size=square_image_size(),
        description=f"{kind.title()} cover edit",
    )
    path.write_bytes(_decode(result))
    append_event(job_dir, "cover_done", f"{kind.title()} cover replaced.")
    return path


def generate_title_spot(client, job_dir: Path, inputs: dict, story: dict) -> Path | None:
    path = job_dir / "title_page" / "spot.png"
    if path.is_file():
        return path
    title, preset = _route_bits(inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"{preset['preamble']}\n"
        f"Small spot illustration of the story's anchor object only: "
        f"{(title.get('anchor') or {}).get('description')}. No people, no text. "
        f"{SHARED_NEGATIVES}."
    )
    try:
        result = image_generate(client, prompt, size="1024x1024", description="Title spot")
        path.write_bytes(_decode(result))
        return path
    except Exception as error:  # noqa: BLE001
        append_event(job_dir, "title_spot_failed", str(error))
        return None


def ensure_spreads(
    client,
    job_dir: Path,
    inputs: dict,
    story: dict,
    character_path: Path,
    numbers: list[int] | None = None,
) -> None:
    for n in numbers or list(range(1, SPREAD_COUNT + 1)):
        generate_spread(client, job_dir, inputs, story, character_path, n)
