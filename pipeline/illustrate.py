"""Cover + spread art (v2): face + body sheets, story characters, layout checks."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from PIL import Image

from .brand import ROUTE_PRESETS, SHARED_NEGATIVES
from .catalogue import get_title, resolve_route
from .character import character_paths
from .constants import BACK_QR_SAFE_CORNER, BACK_QR_SAFE_MM, SPREAD_COUNT
from .content import calm_zone_for
from .cover_layout import (
    back_cover_zones,
    detect_faces_local,
    enforce_cover_layout,
    front_cover_zones,
    layout_prompt_rules,
    write_cover_layout,
)
from .job_log import append_event, read_json, write_json
from .openai_util import image_edit, image_generate, largest_image_size, square_image_size
from .spread_layout import enforce_spread_fold
from .story_characters import (
    characters_for_spread,
    characters_named_in_text,
    ref_paths_for_characters,
)

# Face + body + up to three story sheets (e.g. Pip, mum, curly shell on spread 13).
MAX_REF_IMAGES = 5


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


def _sheet_paths(job_dir: Path, character_path: Path | None = None) -> tuple[Path, Path]:
    """Resolve face + body sheets (legacy callers may pass only body path)."""
    paths = character_paths(job_dir)
    body = Path(character_path) if character_path else paths["body"]
    face = paths["face"]
    if not face.is_file():
        face = body  # fall back so older jobs still illustrate
    return face, body


def build_reference_pack(
    job_dir: Path,
    inputs: dict,
    story: dict,
    *,
    n: int | None = None,
    kind: str | None = None,
    character_path: Path | None = None,
    include_prev_setting: bool = True,
    include_current_if_present: bool = False,
) -> tuple[list[Path], list[str], dict]:
    """Face + body always; plus story characters for the spread/cover. Cap at 4.

    Returns (paths, label_lines, meta).
    """
    title_id = inputs.get("title_id") or story.get("title_id") or ""
    name = inputs.get("name") or "the child"
    face, body = _sheet_paths(job_dir, character_path)
    refs: list[Path] = []
    labels: list[str] = []

    def _add(path: Path, label: str) -> bool:
        if len(refs) >= MAX_REF_IMAGES:
            return False
        if path in refs or not path.is_file():
            return False
        refs.append(path)
        labels.append(label)
        return True

    _add(face, f"{name}'s face (identity)")
    _add(body, f"{name}'s outfit and body")

    story_chars: list[dict] = []
    if kind in {"front", "back"}:
        brief = (story.get("cover_art") or "") if kind == "front" else (story.get("back_blurb") or "")
        story_chars = characters_named_in_text(story, brief)
        # Front cover always benefits from Pip when listed on the story.
        if kind == "front" and not story_chars:
            story_chars = [c for c in (story.get("characters") or []) if c.get("id") == "pip"]
    elif n is not None:
        story_chars = characters_for_spread(title_id, story, int(n))

    for entry, path in ref_paths_for_characters(story_chars, title_id):
        _add(path, entry.get("name") or entry.get("id") or "companion")

    used_prev = False
    if include_prev_setting and n is not None and len(refs) < MAX_REF_IMAGES:
        spreads = {int(s["n"]): s for s in story.get("spreads") or []}
        current = spreads.get(int(n)) or {"n": n}
        prev = spreads.get(int(n) - 1)
        if settings_match(current, prev):
            prev_path = spread_art_path(job_dir, int(n) - 1)
            if _add(prev_path, "previous spread (same setting — continuity only)"):
                used_prev = True

    if include_current_if_present and n is not None:
        current_path = spread_art_path(job_dir, int(n))
        _add(current_path, "current spread (edit base)")
    elif include_current_if_present and kind in {"front", "back"}:
        current_path = Path(job_dir) / "cover" / f"{kind}_art.png"
        _add(current_path, f"current {kind} cover (edit base)")

    meta = {
        "refs": [p.name for p in refs],
        "labels": labels,
        "prev_setting_ref": used_prev,
        "n": n,
        "kind": kind,
    }
    return refs, labels, meta


def reference_prompt_block(labels: list[str]) -> str:
    if not labels:
        return ""
    parts = [f"image {i} = {lab}" for i, lab in enumerate(labels, start=1)]
    return (
        "Reference images: "
        + ", ".join(parts)
        + ". Copy each character's design exactly; do not copy backgrounds or poses from the references."
    )


# Ordered cues: first match in art+text wins. Prefer art wording over a default smile.
_EXPRESSION_CUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsurpris|\bwonder|\beyes wide|\bclunk|\bit'?s an egg", re.I),
     "surprised / wonder — eyes bright, mouth open in discovery; not a polite smile"),
    (re.compile(r"\blaugh", re.I),
     "laughing — bright, open, joyful"),
    (re.compile(r"\bsmil|\bgentle smile|\bwith a smile", re.I),
     "warm, natural smile matching the beat"),
    (re.compile(r"\bdetermin|\bpuffed|\bheavier|\bstep, step", re.I),
     "determined / effort — set jaw, focused; not a broad smile"),
    (re.compile(r"\bhums?|\bsnuggl|\bsoft(?:ly)?(?:\s+eyes)?|\bquiet", re.I),
     "soft, quiet, content — calm face, not a big grin"),
    (re.compile(r"\beyes closing|\bbedtime|\bgoodnight|\bwhispers|\btucked in|\basleep", re.I),
     "sleepy / peaceful — eyes heavy or closing, soft almost-smile"),
    (re.compile(r"\bworr|\bchin begins to wobble|\bdon'?t worry|\bafraid|\bscared|\bnose first", re.I),
     "gentle concern or care — soft seriousness; not smiling for the camera"),
    (re.compile(r"\bisn'?t sure|\bwhich way|\bwonders|\bhow do we", re.I),
     "thoughtful / uncertain — looking at the choice or companion, not the camera"),
    (re.compile(r"\bwaves?|\bthank you|\bstrokes", re.I),
     "warm, friendly — natural smile toward the companion or action"),
    (re.compile(r"\bwatches|\blooking|\blooks", re.I),
     "attentive — gaze follows the action in the scene, expression matches the beat"),
)


def expression_from_beat(spread: dict) -> str:
    """Guess the child's expression from story text + art (no default camera-smile)."""
    art = (spread.get("art") or spread.get("scene") or "").strip()
    text = (spread.get("text") or "").strip()
    # Prefer cues in the art brief; fall back to story text.
    for blob in (art, text, f"{art}\n{text}"):
        for pattern, phrase in _EXPRESSION_CUES:
            if pattern.search(blob):
                return phrase
    return (
        "natural expression for this beat — calm and present; "
        "do not default to a smile or looking at the camera"
    )


def _spread_prompt(
    inputs: dict,
    story: dict,
    spread: dict,
    character_note: str,
    *,
    prev_setting_ref: bool = False,
    ref_labels: list[str] | None = None,
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
    expression = expression_from_beat(spread)
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
            "CONTINUITY: keep the same background and location as the previous-spread reference "
            "(same setting); match shed/swing/washing line or fern/river props already shown. "
            "Identity comes from the face sheet; outfit from the body sheet.\n"
        )
    ref_block = reference_prompt_block(ref_labels or [])
    return (
        f"{preset['preamble']}\n"
        f"ONE CONTINUOUS double-page picture for spread {n} of '{story['title']}'. "
        f"Landscape, full bleed, child's eye level. Not two separate images.\n"
        f"{setting_line}"
        f"SCENE: {art}\n"
        f"{continuity}"
        f"{calm_rule}\n"
        f"Child ({inputs['name']}) in {outfit}. "
        f"EXPRESSION / GAZE: {expression}. "
        f"Pose, gaze and expression follow the SCENE and story text — "
        f"do not default to facing the camera or smiling unless the beat asks for it. "
        f"Keep the face readable when it is the focus of the beat.\n"
        f"Spread anchor: {spread_anchor}. Story anchor lock: {anchor_desc}\n"
        f"No face within 10% of the centre fold. No face or important object on the centre fold. "
        f"No text, letters or logos in the art. "
        f"{character_note}\n"
        f"{ref_block}\n"
        f"Must not show: {must_not}. {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def _spread_image_refs(
    job_dir: Path,
    story: dict,
    character_path: Path,
    n: int,
    *,
    include_current_if_present: bool = False,
    inputs: dict | None = None,
) -> tuple[list[Path], bool, list[str]]:
    """Compatibility wrapper: returns (refs, used_prev, labels)."""
    inputs = inputs or {"title_id": story.get("title_id"), "name": "Child"}
    refs, labels, meta = build_reference_pack(
        job_dir,
        inputs,
        story,
        n=int(n),
        character_path=character_path,
        include_prev_setting=True,
        include_current_if_present=include_current_if_present,
    )
    return refs, bool(meta.get("prev_setting_ref")), labels


def generate_spread(client, job_dir: Path, inputs: dict, story: dict, character_path: Path, n: int) -> Path:
    path = spread_art_path(job_dir, n)
    if path.is_file():
        append_event(job_dir, "spread_skip", f"Spread {n} art exists; keeping.", spread=n)
        return path
    spreads = {int(s["n"]): s for s in story.get("spreads") or []}
    spread = spreads.get(int(n)) or {"n": n, "scene": "", "calm_zone": calm_zone_for(n)}
    path.parent.mkdir(parents=True, exist_ok=True)
    refs, labels, meta = build_reference_pack(
        job_dir, inputs, story, n=int(n), character_path=character_path
    )
    prompt = _spread_prompt(
        inputs,
        story,
        spread,
        "FACE LOCK: match the face sheet exactly; outfit from the body sheet.",
        prev_setting_ref=bool(meta.get("prev_setting_ref")),
        ref_labels=labels,
    )
    append_event(
        job_dir,
        "spread_start",
        f"Generating spread {n}; refs={meta['refs']}.",
        spread=n,
        refs=meta["refs"],
        labels=labels,
    )
    result = image_edit(
        client,
        refs,
        prompt,
        size=largest_image_size(aspect="landscape"),
        description=f"Spread {n}",
    )
    path.write_bytes(_decode(result))

    def _regen(_path: Path) -> None:
        stronger = (
            prompt
            + "\nREGEN: previous attempt put a face on the centre fold. "
            "Move every face clear of the middle 10% of the width."
        )
        again = image_edit(
            client,
            refs + ([_path] if _path.is_file() and _path not in refs else []),
            stronger,
            size=largest_image_size(aspect="landscape"),
            description=f"Spread {n} fold regen",
        )
        _path.write_bytes(_decode(again))

    fold = enforce_spread_fold(
        client,
        job_dir,
        path,
        (spread.get("calm_zone") or calm_zone_for(n)),
        regenerate_fn=_regen,
        n=int(n),
    )
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
            "prev_setting_ref": bool(meta.get("prev_setting_ref")),
            "refs": meta["refs"],
            "labels": labels,
            "layout": {"fold_warning": bool(fold.get("warning")), "fold_passed": bool(fold.get("passed"))},
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
    refs, labels, meta = build_reference_pack(
        job_dir,
        inputs,
        story,
        n=int(n),
        character_path=character_path,
        include_current_if_present=True,
    )
    base = _spread_prompt(
        inputs,
        story,
        spread,
        "FACE LOCK: match the face sheet exactly; outfit from the body sheet.",
        prev_setting_ref=bool(meta.get("prev_setting_ref")),
        ref_labels=labels,
    )
    prompt = base + f"\nEDIT: {instruction.strip()}"
    append_event(
        job_dir,
        "spread_edit",
        f"Editing spread {n}: {instruction[:120]}; refs={meta['refs']}",
        spread=n,
        refs=meta["refs"],
        labels=labels,
    )
    result = image_edit(
        client,
        refs,
        prompt,
        size=largest_image_size(aspect="landscape"),
        description=f"Spread {n} edit",
    )
    path.write_bytes(_decode(result))

    def _regen(_path: Path) -> None:
        again = image_edit(
            client,
            refs,
            base + "\nREGEN: clear every face from the centre fold (middle 10%).",
            size=largest_image_size(aspect="landscape"),
            description=f"Spread {n} fold regen",
        )
        _path.write_bytes(_decode(again))

    enforce_spread_fold(
        client,
        job_dir,
        path,
        (spread.get("calm_zone") or calm_zone_for(n)),
        regenerate_fn=_regen,
        n=int(n),
    )
    append_event(job_dir, "spread_done", f"Spread {n} replaced.", spread=n)
    return path


def _cover_prompt(kind: str, inputs: dict, story: dict, *, ref_labels: list[str] | None = None) -> str:
    title, preset = _route_bits(inputs)
    outfit = story.get("outfit") or title.get("outfit")
    book_title = story.get("title") or ""
    name = inputs.get("name") or ""
    ref_block = reference_prompt_block(ref_labels or [])
    smile = (
        f"The child ({name or 'the child'}) has a warm, natural smile — happy and inviting, "
        f"not a stiff camera grin."
    )
    if kind == "front":
        zones = front_cover_zones(book_title, name)
        cover_art = (story.get("cover_art") or "").strip()
        scene = cover_art or (
            "Child facing viewer, largest element, companion (if any) beside them at face height."
        )
        return (
            f"{preset['preamble']}\n"
            f"FRONT COVER for '{book_title}'. Square. {scene} "
            f"{smile} "
            f"{layout_prompt_rules('front', zones)} "
            f"No carer face. No text in art. Outfit: {outfit}. "
            f"{ref_block} "
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
        f"{smile} "
        f"{layout_prompt_rules('back', zones)} "
        f"Leave the {corner} of the text band plain (~{BACK_QR_SAFE_MM:g} mm). "
        f"Outfit: {outfit}. {ref_block} {SHARED_NEGATIVES}. {preset['negatives']}."
    )


def _generate_one_cover(
    client, job_dir: Path, inputs: dict, story: dict, character_path: Path, kind: str, path: Path
) -> None:
    refs, labels, meta = build_reference_pack(
        job_dir, inputs, story, kind=kind, character_path=character_path
    )
    prompt = _cover_prompt(kind, inputs, story, ref_labels=labels)
    append_event(
        job_dir,
        "cover_refs",
        f"{kind} cover refs={meta['refs']}",
        kind=kind,
        refs=meta["refs"],
        labels=labels,
    )
    result = image_edit(
        client,
        refs,
        prompt,
        size=square_image_size(),
        description=f"{kind.title()} cover",
    )
    path.write_bytes(_decode(result))

    def _regen(_path: Path) -> None:
        stronger = (
            _cover_prompt(kind, inputs, story, ref_labels=labels)
            + "\nREGEN: previous attempt placed a face under the title or logo. "
            "Pull every face firmly into the middle clear band; companions beside the child only."
        )
        again_refs = list(refs)
        if _path.is_file() and _path not in again_refs and len(again_refs) < MAX_REF_IMAGES:
            again_refs.append(_path)
        again = image_edit(
            client,
            again_refs,
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
    meta_json = read_json(job_dir / "cover" / "cover.json") or {}
    meta_json.setdefault("title", story.get("title"))
    write_json(job_dir / "cover" / "cover.json", meta_json)
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
    refs, labels, meta = build_reference_pack(
        job_dir,
        inputs,
        story,
        kind=kind,
        character_path=character_path,
        include_current_if_present=True,
    )
    prompt = _cover_prompt(kind, inputs, story, ref_labels=labels) + f"\nEDIT: {instruction.strip()}"
    append_event(
        job_dir,
        "cover_edit",
        f"Editing {kind} cover: {instruction[:120]}; refs={meta['refs']}",
        refs=meta["refs"],
        labels=labels,
    )
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
            refs,
            _cover_prompt(kind, inputs, story, ref_labels=labels)
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


def crop_parent_from_final_spread(job_dir: Path, story: dict | None = None) -> Path | None:
    """Crop a square from the final story spread so the child's face is fully inside."""
    out = job_dir / "parent" / "spot.png"
    if out.is_file():
        return out
    n = 14
    if story:
        spreads = story.get("spreads") or []
        if spreads:
            n = max(int(s["n"]) for s in spreads)
    src = spread_art_path(job_dir, n)
    if not src.is_file():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        faces = detect_faces_local(src)
        side = min(w, h)
        if faces:
            # Prefer the largest face (child).
            face = max(faces, key=lambda f: float(f["w"]) * float(f["h"]))
            cx = (float(face["x"]) + float(face["w"]) / 2) * w
            cy = (float(face["y"]) + float(face["h"]) / 2) * h
            # Ensure full face inside crop with margin.
            fw = float(face["w"]) * w
            fh = float(face["h"]) * h
            side = int(max(side * 0.55, fw * 2.4, fh * 2.4))
            side = min(side, w, h)
            left = int(round(cx - side / 2))
            top = int(round(cy - side / 2))
            left = max(0, min(left, w - side))
            top = max(0, min(top, h - side))
            # If face still clips, expand toward face.
            fx0 = float(face["x"]) * w
            fy0 = float(face["y"]) * h
            fx1 = fx0 + fw
            fy1 = fy0 + fh
            if fx0 < left:
                left = max(0, int(fx0) - 4)
            if fy0 < top:
                top = max(0, int(fy0) - 4)
            if fx1 > left + side:
                left = min(w - side, int(fx1 - side) + 4)
            if fy1 > top + side:
                top = min(h - side, int(fy1 - side) + 4)
            left = max(0, min(left, w - side))
            top = max(0, min(top, h - side))
        else:
            # Bedroom / final spread: prefer right half (often the bed).
            left = max(0, w - side)
            top = max(0, (h - side) // 2)
        crop = rgb.crop((left, top, left + side, top + side))
        crop.save(out, format="PNG")
    append_event(job_dir, "parent_crop", f"Parent spot cropped from spread {n}.")
    return out


def generate_parent_spot(client, job_dir: Path, inputs: dict, story: dict) -> Path | None:
    """Prefer a crop of the final spread; fall back to generating only if no art yet."""
    del client, inputs
    cropped = crop_parent_from_final_spread(job_dir, story)
    if cropped and cropped.is_file():
        return cropped
    # No final spread yet — leave unset; build will crop after spreads exist.
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
