"""Load Raised Ready title catalogue (spec §2)."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from .brand import BRAND_DIR, default_route_for_age_band

logger = logging.getLogger(__name__)

CATALOGUE_PATH = BRAND_DIR / "catalogue.json"
STORIES_DIR = BRAND_DIR / "stories"

REQUIRED_TITLE_KEYS = (
    "title_id",
    "family",
    "colour",
    "band",
    "question_form",
    "title_template",
    "age_band",
    "route",
    "outfit",
    "anchor",
    "must_not_show",
    "personalisation_slots",
    "blurb",
    "parent_spread",
    "last_line",
    "approved",
)

REQUIRED_PARENT_BLOCKS = (
    "you_could_say",
    "try_together",
    "in_real_life",
    "read_it_again",
)


class CatalogueError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_catalogue() -> dict:
    if not CATALOGUE_PATH.is_file():
        raise CatalogueError(f"Missing catalogue: {CATALOGUE_PATH}")
    data = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    titles = data.get("titles") or {}
    if not titles:
        raise CatalogueError("catalogue.json has no titles")
    for title_id, entry in titles.items():
        _validate_title(title_id, entry)
    return data


def list_titles() -> list[dict]:
    data = load_catalogue()
    return [data["titles"][tid] for tid in data.get("order") or data["titles"].keys()]


def get_title(title_id: str) -> dict:
    data = load_catalogue()
    entry = (data.get("titles") or {}).get(title_id)
    if not entry:
        raise CatalogueError(f"Unknown title_id: {title_id}")
    return entry


def resolve_route(title: dict) -> str:
    route = (title.get("route") or "open").strip().lower()
    if route == "open":
        resolved = default_route_for_age_band(title["age_band"])
        logger.warning(
            "title_id=%s route is open; defaulting to %s for age_band=%s",
            title.get("title_id"),
            resolved,
            title.get("age_band"),
        )
        return resolved
    return route


def load_story_beats(title_id: str) -> dict:
    path = STORIES_DIR / f"{title_id}.md"
    if not path.is_file():
        raise CatalogueError(f"Missing story beats for {title_id}: {path}")
    text = path.read_text(encoding="utf-8")
    beats = []
    current = None
    for line in text.splitlines():
        if line.startswith("## Spread "):
            if current:
                beats.append(current)
            num = int(line.replace("## Spread ", "").strip().split()[0])
            current = {"n": num, "text": ""}
        elif current is not None:
            current["text"] += (line + "\n")
    if current:
        beats.append(current)
    for beat in beats:
        beat["text"] = beat["text"].strip()
    if len(beats) != 14:
        raise CatalogueError(
            f"title_id={title_id} story file must have 14 spreads, found {len(beats)}"
        )
    meta = {
        "approved": False,
        "sets": [],
        "title_id": title_id,
    }
    for line in text.splitlines()[:20]:
        if line.startswith("approved:"):
            meta["approved"] = line.split(":", 1)[1].strip().lower() in {"true", "yes", "1"}
        if line.startswith("sets:"):
            raw = line.split(":", 1)[1].strip()
            meta["sets"] = [s.strip() for s in raw.split(",") if s.strip()]
    return {"meta": meta, "beats": beats, "path": str(path)}


def _validate_title(title_id: str, entry: dict) -> None:
    missing = [k for k in REQUIRED_TITLE_KEYS if k not in entry]
    if missing:
        raise CatalogueError(f"title_id={title_id} missing keys: {missing}")
    if entry.get("title_id") != title_id:
        raise CatalogueError(f"title_id mismatch for {title_id}")
    parent = entry.get("parent_spread") or {}
    for block in REQUIRED_PARENT_BLOCKS:
        if block not in parent or not str(parent[block]).strip():
            raise CatalogueError(f"title_id={title_id} missing parent_spread.{block}")
    blurb = (entry.get("blurb") or "").strip()
    if len(blurb.split()) > 45:
        raise CatalogueError(f"title_id={title_id} blurb exceeds 45 words")
    schedule = (entry.get("anchor") or {}).get("state_schedule")
    if schedule is not None and len(schedule) != 14:
        raise CatalogueError(
            f"title_id={title_id} state_schedule length {len(schedule)} != 14"
        )


def check_band_grammar(band: str, family: str) -> bool:
    """Band grammar: Ready for + step; Ready to + skill; adventures = READY FOR ADVENTURE."""
    b = (band or "").strip().upper()
    if family == "adventures":
        return b == "READY FOR ADVENTURE"
    if b.startswith("READY TO "):
        return True
    if b.startswith("READY FOR "):
        return True
    return False
