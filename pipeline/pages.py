"""True spreads: one wide picture per spread, the 32-page map, and the spread -> page split.

Interior art is ONE continuous double-page image per spread, ``spreads/spread-NN/art.png``. It is
cut into a left and a right page only at PDF time (and, lazily, for the operator UI) by
``split_spread``; the halves are derived files, never the source of truth.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .catalogue import get_title
from .constants import (
    CONTENT_PAGE_COUNT,
    PAGE_WITH_BLEED_MM,
    SPREAD_ASPECT,
    SPREAD_COUNT,
    SPREAD_WIDTH_MM,
)
from .job_log import append_event, write_json

SIDES = ("left", "right")


# --- paths -------------------------------------------------------------------------------------


def spread_dir(job_dir: Path, n: int) -> Path:
    return job_dir / "spreads" / f"spread-{int(n):02d}"


def spread_art_path(job_dir: Path, n: int) -> Path:
    """``spreads/spread-NN/art.png``: the single continuous double-page picture."""
    return spread_dir(job_dir, n) / "art.png"


def spread_page_path(job_dir: Path, n: int, side: str) -> Path:
    """Derived half (``page-left.png`` / ``page-right.png``); a UI cache, never the source."""
    if side not in SIDES:
        raise ValueError(f"side must be left/right, got {side!r}")
    return spread_dir(job_dir, n) / f"page-{side}.png"


def spread_art_rel(n: int) -> str:
    return f"spreads/spread-{int(n):02d}/art.png"


def page_spread_side(page_number: int) -> tuple[int, str] | None:
    """Spread n occupies pages 2n (left) and 2n+1 (right); None outside pages 2-29."""
    page_number = int(page_number)
    if page_number < 2 or page_number > 2 * SPREAD_COUNT + 1:
        return None
    return page_number // 2, ("left" if page_number % 2 == 0 else "right")


def page_art_path(job_dir: Path, page_number: int) -> Path | None:
    """Spread art that a content page is cut from (both halves share one file)."""
    located = page_spread_side(page_number)
    if located is None:
        return None
    return spread_art_path(job_dir, located[0])


def clear_spread_derivatives(job_dir: Path, n: int) -> None:
    """Delete the cached UI halves (and nothing else). Always done when the art is regenerated."""
    for side in SIDES:
        spread_page_path(job_dir, n, side).unlink(missing_ok=True)


def clear_spread_art(job_dir: Path, n: int, *, keep_meta: bool = False) -> bool:
    """Remove a spread's art, its cached halves and (unless ``keep_meta``) its spread.json."""
    art = spread_art_path(job_dir, n)
    removed = art.is_file()
    art.unlink(missing_ok=True)
    clear_spread_derivatives(job_dir, n)
    if not keep_meta:
        (spread_dir(job_dir, n) / "spread.json").unlink(missing_ok=True)
    return removed


# --- geometry: fit + split ---------------------------------------------------------------------


def fit_crop_box(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Centre-crop box (left, top, right, bottom) that gives ``size`` the SPREAD_ASPECT.

    The image models offer landscape frames TALLER than the spread (e.g. 1536x1024 is 1.5:1, the
    spread is ~1.97:1), so the crop is normally vertical: top and bottom bands are cut equally.
    A frame that is already wider than the spread (e.g. 2048x1024) loses a sliver from the left and
    right instead. Either way the centre (the fold) stays put and nothing is ever resized up.
    """
    width, height = size
    target_h = round(width / SPREAD_ASPECT)
    if target_h <= height:
        top = (height - target_h) // 2
        return (0, top, width, top + target_h)
    target_w = round(height * SPREAD_ASPECT)
    left = (width - target_w) // 2
    return (left, 0, left + target_w, height)


def fit_to_spread(image: Image.Image) -> Image.Image:
    """Centre-crop ``image`` to SPREAD_ASPECT (never resizes). See ``fit_crop_box``."""
    box = fit_crop_box(image.size)
    if box == (0, 0, image.size[0], image.size[1]):
        return image
    cropped = image.crop(box)
    cropped.load()
    return cropped


def _open(image) -> tuple[Image.Image, bool]:
    if isinstance(image, Image.Image):
        return image, False
    return Image.open(image), True


def split_box_px(width: int) -> int:
    """Pixel width of one page half for a spread image ``width`` px wide (216 of 426 mm)."""
    return max(1, round(width * PAGE_WITH_BLEED_MM / SPREAD_WIDTH_MM))


def split_spread(image) -> tuple[Image.Image, Image.Image]:
    """Cut a spread image into (left_page, right_page) using mm -> px ratios on its ACTUAL width.

    Left page  = x in [0, 216] mm (outer bleed + trim + 3 mm inner bleed).
    Right page = x in [210, 426] mm (3 mm inner bleed + trim + outer bleed).
    The halves overlap by 6 mm (what binding / trim consume). Both halves are exactly
    ``split_box_px(width)`` px wide, so 1972 px -> left [0, 1000), right [972, 1972).
    """
    source, opened = _open(image)
    try:
        width, height = source.size
        half = split_box_px(width)
        left = source.crop((0, 0, half, height))
        right = source.crop((width - half, 0, width, height))
        left.load()
        right.load()
        return left, right
    finally:
        if opened:
            source.close()


def ensure_spread_halves(job_dir: Path, n: int) -> dict[str, Path]:
    """Lazily write ``page-left.png`` / ``page-right.png`` from ``art.png`` (UI cache). {} if no art."""
    art = spread_art_path(job_dir, n)
    if not art.is_file():
        return {}
    paths = {side: spread_page_path(job_dir, n, side) for side in SIDES}
    fresh = all(
        p.is_file() and p.stat().st_mtime >= art.stat().st_mtime for p in paths.values()
    )
    if not fresh:
        left, right = split_spread(art)
        left.save(paths["left"], format="PNG", compress_level=1)
        right.save(paths["right"], format="PNG", compress_level=1)
    return paths


# --- spread / page-map helpers -----------------------------------------------------------------


def spread_pages(pages: list) -> dict[int, dict]:
    """Spread-level briefs keyed by spread number (taken from each spread's left half)."""
    return {
        int(p["spread"]): {**p, "text": spread_text(pages, int(p["spread"]))}
        for p in pages
        if p.get("kind") == "spread_half" and p.get("side") == "left"
    }


def spread_halves(pages: list, n: int) -> list[dict]:
    return [
        p for p in pages if p.get("kind") == "spread_half" and int(p.get("spread") or -1) == int(n)
    ]


def spread_text(pages: list, n: int) -> str:
    """The spread's story text (stored on its calm-zone half only; falls back to any half)."""
    halves = spread_halves(pages, n)
    for half in halves:
        if half.get("side") == half.get("calm_zone") and (half.get("text") or "").strip():
            return half["text"]
    for half in halves:
        if (half.get("text") or "").strip():
            return half["text"]
    return ""


def set_spread_text(pages: list, n: int, text: str, calm_zone: str | None = None) -> None:
    """Store ``text`` on the calm-zone half ONLY; the other half gets ''."""
    halves = spread_halves(pages, n)
    zone = calm_zone or next((h.get("calm_zone") for h in halves if h.get("calm_zone")), None)
    zone = zone or _calm_zone_for_spread(int(n))
    for half in halves:
        half["calm_zone"] = zone
        half["text"] = (text or "") if half.get("side") == zone else ""


def update_spread(
    story: dict,
    pages: list,
    n: int,
    *,
    text: str | None = None,
    scene: str | None = None,
    calm_zone: str | None = None,
    personalized: bool | None = None,
) -> None:
    """Apply an edit to spread ``n`` in BOTH the story and the page map (kept in lock-step)."""
    n = int(n)
    spread = next((s for s in story.get("spreads") or [] if int(s.get("n") or 0) == n), None)
    zone = calm_zone if calm_zone in SIDES else None
    current_text = text if text is not None else spread_text(pages, n)
    for half in spread_halves(pages, n):
        if scene is not None:
            half["scene"] = scene
        if personalized is not None:
            half["personalized"] = personalized
    set_spread_text(pages, n, current_text, zone)
    if spread is not None:
        if text is not None:
            spread["text"] = text
        if scene is not None:
            spread["scene"] = scene
        if zone:
            spread["calm_zone"] = zone


def _calm_zone_for_spread(n: int) -> str:
    """Default calm zone alternates by spread (left on odd, right on even); the model may swap it."""
    return "left" if n % 2 == 1 else "right"


def _anchor_state(title: dict, spread_n: int) -> int | None:
    schedule = (title.get("anchor") or {}).get("state_schedule")
    if schedule is None:
        return None
    if spread_n < 1 or spread_n > len(schedule):
        return None
    return int(schedule[spread_n - 1])


def compile_pages(client, job_dir: Path, inputs: dict, story: dict) -> list:
    """Deterministic 32-page map from 14 spreads (no LLM)."""
    del client  # unused — kept for runner signature
    title = get_title(inputs["title_id"])
    spreads = story.get("spreads") or []
    if len(spreads) != SPREAD_COUNT:
        raise ValueError(f"Expected {SPREAD_COUNT} spreads, got {len(spreads)}")

    by_n = {int(s["n"]): s for s in spreads}

    append_event(
        job_dir,
        "pages_compile_start",
        f"Compiling deterministic {CONTENT_PAGE_COUNT}-page map.",
    )

    pages: list[dict] = []
    pages.append(
        {
            "number": 1,
            "kind": "title",
            "title": story.get("locked", {}).get("title") or story.get("title"),
            "blank": False,
        }
    )

    for n in range(1, SPREAD_COUNT + 1):
        spread = by_n.get(n)
        if not spread:
            raise ValueError(f"Missing spread {n}")
        calm = spread.get("calm_zone")
        if calm not in SIDES:
            calm = _calm_zone_for_spread(n)
        anchor_state = spread.get("anchor_state")
        if anchor_state is None:
            anchor_state = _anchor_state(title, n)
        base = {
            "spread": n,
            "scene": spread.get("scene") or "",
            "calm_zone": calm,
            "anchor_state": anchor_state,
            "personalized": True,
            "blank": False,
            # BOTH halves are cut from this one picture at PDF time.
            "art_path": spread_art_rel(n),
        }
        # One picture per spread; the story text lives ONLY on the calm-zone half.
        for side, number in (("left", 2 * n), ("right", 2 * n + 1)):
            pages.append(
                {
                    **base,
                    "number": number,
                    "kind": "spread_half",
                    "side": side,
                    "text": (spread.get("text") or "") if side == calm else "",
                }
            )

    parent = story.get("locked", {}).get("parent_spread") or {}
    pages.append(
        {
            "number": 30,
            "kind": "parent_left",
            "text": parent.get("you_could_say") or "",
            "blank": False,
        }
    )
    pages.append(
        {
            "number": 31,
            "kind": "parent_right",
            "text": parent.get("try_together") or "",
            "blank": False,
        }
    )
    pages.append(
        {
            "number": 32,
            "kind": "dedication",
            "text": story.get("locked", {}).get("dedication") or "",
            "blank": False,
        }
    )

    if len(pages) != CONTENT_PAGE_COUNT:
        raise ValueError(f"Page map length {len(pages)} != {CONTENT_PAGE_COUNT}")

    write_json(job_dir / "story" / "pages.json", {"pages": pages})
    append_event(
        job_dir,
        "pages_compile_done",
        f"Compiled {len(pages)} pages from {SPREAD_COUNT} spreads.",
        page_count=len(pages),
    )
    return pages
