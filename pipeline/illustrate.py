"""Cover + spread art (v2). No layout validation / regen loops — generate once and save."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from .brand import ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import get_title, resolve_route
from .constants import BACK_QR_SAFE_CORNER, BACK_QR_SAFE_MM, SPREAD_COUNT
from .content import calm_zone_for
from .cover_layout import (
    back_cover_zones,
    enforce_cover_layout,
    front_cover_zones,
    layout_prompt_rules,
    write_cover_layout,
)
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


def _normalise_setting(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def settings_match(a: dict | None, b: dict | None) -> bool:
    """True when two spreads share the same binding ``setting`` string."""
    if not a or not b:
        return False
    sa, sb = _normalise_setting(a.get("setting")), _normalise_setting(b.get("setting"))
    return bool(sa and sb and sa == sb)


def _spread_prompt(
    inputs: dict,
    story: dict,
    spread: dict,
    character_note: str,
    *,
    prev_setting_ref: bool = False,
) -> str:
    title, preset = _route_bits(inputs)
    n = int(spread["n"])
    calm = (spread.get("calm_zone") or calm_zone_for(n)).strip().lower()
    calm_note = (spread.get("calm_zone_note") or "").strip()
    setting = (spread.get("setting") or "").strip()
    art = (spread.get("art") or spread.get("scene") or "the beat in action").strip()
    outfit = story.get("outfit") or title.get("outfit")
    story_anchor = story.get("anchor") or title.get("anchor") or {}
    if isinstance(story_anchor, dict):
        anchor_desc = story_anchor.get("description") or ""
    else:
        anchor_desc = str(story_anchor)
    spread_anchor = (spread.get("anchor") or "").strip() or anchor_desc
    must_not = "; ".join(story.get("must_not_show") or title.get("must_not_show") or [])
    # Calm zone is NOT a separate image — one continuous spread; keep that area low-detail for text.
    calm_rule = (
        f"CALM ZONE ({calm.upper()}): keep this area LOW-DETAIL (soft sky / wall / lawn / canopy) "
        f"so printed story text can sit there. Do not put faces, Pip, or busy objects in that zone. "
        f"{calm_note}".strip()
    )
    setting_line = (
        f"SETTING (binding — place, light and weather come ONLY from this line, not from the style): "
        f"{setting or 'Follow SCENE only; no default interior.'}\n"
    )
    continuity = ""
    if prev_setting_ref:
        continuity = (
            "CONTINUITY: keep the same background and location as the second reference image "
            "(previous spread); match shed/swing/washing line or fern/river props already shown. "
            "First reference is the character sheet for the child's face.\n"
        )
    return (
        f"{preset['preamble']}\n"
        f"ONE CONTINUOUS double-page picture for spread {n} of '{story['title']}'. "
        f"Landscape, full bleed, child's eye level. Not two separate images.\n"
        f"{setting_line}"
        f"SCENE: {art}\n"
        f"{continuity}"
        f"{calm_rule}\n"
        f"Child ({inputs['name']}) in {outfit}. "
        f"Face never covered or turned away. "
        f"Spread anchor: {spread_anchor}. Story anchor lock: {anchor_desc}\n"
        f"No face or important object on the centre fold. No text, letters or logos in the art. "
        f"{character_note}\n"
        f"Must not show: {must_not}. {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def _spread_image_refs(
    job_dir: Path,
    story: dict,
    character_path: Path,
    n: int,
    *,
    include_current_if_present: bool = False,
) -> tuple[list[Path], bool]:
    """Character sheet first; previous spread art when settings match; optional current for edits."""
    spreads = {int(s["n"]): s for s in story.get("spreads") or []}
    current = spreads.get(int(n)) or {"n": n}
    prev = spreads.get(int(n) - 1)
    refs: list[Path] = [character_path]
    used_prev = False
    if settings_match(current, prev):
        prev_path = spread_art_path(job_dir, int(n) - 1)
        if prev_path.is_file():
            refs.append(prev_path)
            used_prev = True
    if include_current_if_present:
        current_path = spread_art_path(job_dir, int(n))
        if current_path.is_file() and current_path not in refs:
            refs.append(current_path)
    return refs, used_prev


def generate_spread(client, job_dir: Path, inputs: dict, story: dict, character_path: Path, n: int) -> Path:
    path = spread_art_path(job_dir, n)
    if path.is_file():
        append_event(job_dir, "spread_skip", f"Spread {n} art exists; keeping.", spread=n)
        return path
    spreads = {int(s["n"]): s for s in story.get("spreads") or []}
    spread = spreads.get(int(n)) or {"n": n, "scene": "", "calm_zone": calm_zone_for(n)}
    path.parent.mkdir(parents=True, exist_ok=True)
    refs, used_prev = _spread_image_refs(job_dir, story, character_path, int(n))
    prompt = _spread_prompt(
        inputs,
        story,
        spread,
        "FACE LOCK: match the character sheet exactly.",
        prev_setting_ref=used_prev,
    )
    append_event(
        job_dir,
        "spread_start",
        f"Generating spread {n}" + (" (with previous-setting ref)." if used_prev else "."),
        spread=n,
    )
    result = image_edit(
        client,
        refs,
        prompt,
        size=largest_image_size(aspect="landscape"),
        description=f"Spread {n}",
    )
    path.write_bytes(_decode(result))
    write_json(
        path.parent / "spread.json",
        {
            "n": int(n),
            "setting": spread.get("setting"),
            "calm_zone": spread.get("calm_zone") or calm_zone_for(n),
            "calm_zone_note": spread.get("calm_zone_note"),
            "scene": spread.get("scene") or spread.get("art"),
            "art": spread.get("art"),
            "anchor": spread.get("anchor"),
            "text": spread.get("text"),
            "prev_setting_ref": used_prev,
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
    refs, used_prev = _spread_image_refs(
        job_dir, story, character_path, int(n), include_current_if_present=True
    )
    base = _spread_prompt(
        inputs,
        story,
        spread,
        "FACE LOCK: match the character sheet exactly.",
        prev_setting_ref=used_prev,
    )
    prompt = base + f"\nEDIT: {instruction.strip()}"
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
    outfit = story.get("outfit") or title.get("outfit")
    book_title = story.get("title") or ""
    name = inputs.get("name") or ""
    if kind == "front":
        zones = front_cover_zones(book_title, name)
        cover_art = (story.get("cover_art") or "").strip()
        scene = cover_art or (
            "Child facing viewer, largest element, companion (if any) beside them at face height."
        )
        return (
            f"{preset['preamble']}\n"
            f"FRONT COVER for '{book_title}'. Square. {scene} "
            f"{layout_prompt_rules('front', zones)} "
            f"No carer face. No text in art. Outfit: {outfit}. "
            f"{SHARED_NEGATIVES}. {preset['negatives']}."
        )
    zones = back_cover_zones()
    corner = BACK_QR_SAFE_CORNER.replace("_", "-")
    carer = (inputs.get("carer") or {}).get("name")
    with_carer = (
        f" Quiet epilogue with {carer} and the child in the middle."
        if carer
        else " Quiet epilogue with the child; no named carer required."
    )
    return (
        f"{preset['preamble']}\n"
        f"BACK COVER for '{book_title}'. Square.{with_carer} "
        f"{layout_prompt_rules('back', zones)} "
        f"Leave the {corner} of the text band plain (~{BACK_QR_SAFE_MM:g} mm). "
        f"Outfit: {outfit}. {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def _generate_one_cover(
    client, job_dir: Path, inputs: dict, story: dict, character_path: Path, kind: str, path: Path
) -> None:
    result = image_edit(
        client,
        [character_path],
        _cover_prompt(kind, inputs, story),
        size=square_image_size(),
        description=f"{kind.title()} cover",
    )
    path.write_bytes(_decode(result))

    def _regen(_path: Path) -> None:
        stronger = (
            _cover_prompt(kind, inputs, story)
            + "\nREGEN: previous attempt placed a face under the title or logo. "
            "Pull every face firmly into the middle clear band; companions beside the child only."
        )
        again = image_edit(
            client,
            [character_path, _path] if _path.is_file() else [character_path],
            stronger,
            size=square_image_size(),
            description=f"{kind.title()} cover regen",
        )
        _path.write_bytes(_decode(again))

    layout = enforce_cover_layout(
        client,
        job_dir,
        path,
        kind,
        inputs,
        story,
        character_path,
        regenerate_fn=_regen,
    )
    meta = read_json(job_dir / "cover" / "cover.json") or {}
    meta.setdefault("title", story.get("title"))
    write_json(job_dir / "cover" / "cover.json", meta)
    write_cover_layout(job_dir, layout)


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
        _generate_one_cover(client, job_dir, inputs, story, character_path, kind, path)
        out[kind] = path
        append_event(job_dir, "cover_done", f"{kind.title()} cover saved.")
    write_json(
        cover_dir / "cover.json",
        {
            **(read_json(cover_dir / "cover.json") or {}),
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

    def _regen(_path: Path) -> None:
        again = image_edit(
            client,
            [character_path, _path],
            _cover_prompt(kind, inputs, story)
            + "\nREGEN: move all faces into the clear middle band; companions beside the child.",
            size=square_image_size(),
            description=f"{kind.title()} cover regen",
        )
        _path.write_bytes(_decode(again))

    layout = enforce_cover_layout(
        client, job_dir, path, kind, inputs, story, character_path, regenerate_fn=_regen
    )
    write_cover_layout(job_dir, layout)
    append_event(job_dir, "cover_done", f"{kind.title()} cover replaced.")
    return path


def generate_title_spot(client, job_dir: Path, inputs: dict, story: dict) -> Path | None:
    path = job_dir / "title_page" / "spot.png"
    if path.is_file():
        return path
    title, preset = _route_bits(inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    title_page = story.get("title_page") or {}
    spot_brief = (title_page.get("spot") or "").strip()
    if not spot_brief:
        anchor = story.get("anchor") or title.get("anchor") or {}
        spot_brief = (anchor.get("description") if isinstance(anchor, dict) else str(anchor)) or "story anchor"
    prompt = (
        f"{preset['preamble']}\n"
        f"Small spot illustration for the title page only: {spot_brief}. "
        f"No people unless the brief asks. No text, letters or logos. {SHARED_NEGATIVES}."
    )
    try:
        result = image_generate(client, prompt, size="1024x1024", description="Title spot")
        path.write_bytes(_decode(result))
        return path
    except Exception as error:  # noqa: BLE001
        append_event(job_dir, "title_spot_failed", str(error))
        return None


def generate_parent_spot(client, job_dir: Path, inputs: dict, story: dict) -> Path | None:
    """Optional spot for words-for-the-real-moment (pp 30–31)."""
    path = job_dir / "parent" / "spot.png"
    if path.is_file():
        return path
    brief = (story.get("parent_art") or "").strip()
    if not brief:
        return None
    _, preset = _route_bits(inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"{preset['preamble']}\n"
        f"Small spot illustration for the parent pages: {brief}. No text in art. {SHARED_NEGATIVES}."
    )
    try:
        result = image_generate(client, prompt, size="1024x1024", description="Parent spot")
        path.write_bytes(_decode(result))
        return path
    except Exception as error:  # noqa: BLE001
        append_event(job_dir, "parent_spot_failed", str(error))
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
