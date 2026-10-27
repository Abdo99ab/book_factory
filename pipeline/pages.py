from pathlib import Path

from .catalogue import get_title
from .constants import CONTENT_PAGE_COUNT, SPREAD_COUNT
from .job_log import append_event, write_json


def spread_dir(job_dir: Path, n: int) -> Path:
    return job_dir / "spreads" / f"spread-{int(n):02d}"


def spread_art_path(job_dir: Path, n: int, side: str) -> Path:
    """spreads/spread-NN/art-left.png | art-right.png (one square image per printed page)."""
    if side not in {"left", "right"}:
        raise ValueError(f"side must be left/right, got {side!r}")
    return spread_dir(job_dir, n) / f"art-{side}.png"


def page_spread_side(page_number: int) -> tuple[int, str] | None:
    """Spread n occupies pages 2n (left) and 2n+1 (right); None outside pages 2-29."""
    page_number = int(page_number)
    if page_number < 2 or page_number > 2 * SPREAD_COUNT + 1:
        return None
    return page_number // 2, ("left" if page_number % 2 == 0 else "right")


def page_art_path(job_dir: Path, page_number: int) -> Path | None:
    located = page_spread_side(page_number)
    if located is None:
        return None
    return spread_art_path(job_dir, located[0], located[1])


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


def _calm_zone_for_spread(n: int) -> str:
    """Alternate calm zone by spread (left on odd, right on even)."""
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
        calm = spread.get("calm_zone") or _calm_zone_for_spread(n)
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
            "art_path_left": f"spreads/spread-{n:02d}/art-left.png",
            "art_path_right": f"spreads/spread-{n:02d}/art-right.png",
            # Legacy overlay field until Step 9 ReportLab text layer
            "text_placement": "bottom",
        }
        # Two different pictures per spread; the story text lives ONLY on the calm-zone half.
        for side, number in (("left", 2 * n), ("right", 2 * n + 1)):
            pages.append(
                {
                    **base,
                    "number": number,
                    "kind": "spread_half",
                    "side": side,
                    "scene_left": spread.get("scene_left") or "",
                    "scene_right": spread.get("scene_right") or "",
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
