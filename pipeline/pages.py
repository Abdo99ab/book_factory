from pathlib import Path

from .catalogue import get_title
from .constants import CONTENT_PAGE_COUNT, SPREAD_COUNT
from .job_log import append_event, write_json


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
            "text": spread.get("text") or "",
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
        pages.append({**base, "number": 2 * n, "kind": "spread_half", "side": "left"})
        pages.append({**base, "number": 2 * n + 1, "kind": "spread_half", "side": "right"})

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
