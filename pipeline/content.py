"""Build the job manuscript from a locked story doc — no AI rewrite.

Story 01 (Lost Dinosaur Egg FINAL CHECK) is the standard: take spread text,
art briefs, calm-zone notes, parent pages and blurbs as written; only fill
placeholders ([Name], [their], [Carer], [Giver], [Object], slots).
"""

from __future__ import annotations

from pathlib import Path

from .catalogue import get_title, load_locked_story, resolve_route
from .job_log import append_event, write_json

SPREAD_COUNT = 14

# Doc 00: only [Name] and [their] carry the child; map [their] from order pronouns.
_POSSESSIVE = {"she": "her", "he": "his", "they": "their"}


def calm_zone_for(n: int) -> str:
    """Fallback only when a locked story omits calm_zone (spread 1 right, 2 left…)."""
    return "right" if int(n) % 2 == 1 else "left"


def personalize(template: str, inputs: dict) -> str:
    """Fill story placeholders. Leave unknown ``[Token]`` untouched."""
    if not template:
        return ""
    name = (inputs.get("name") or "").strip()
    pronouns = (inputs.get("pronouns") or "they").strip().lower()
    their = _POSSESSIVE.get(pronouns, "their")
    carer = inputs.get("carer") or {}
    carer_role = (carer.get("role") or carer.get("name") or "Mum").strip()
    carer_name = (carer.get("name") or carer_role).strip()
    giver = (inputs.get("giver") or carer_name).strip()
    comfort = (inputs.get("comfort_object") or "").strip()

    text = template
    for old, new in (
        ("[Name]", name),
        ("[name]", name),
        ("{name}", name),
        ("[their]", their),
        ("[Their]", their[:1].upper() + their[1:] if their else their),
        ("[Carer]", carer_role),
        ("[carer]", carer_role),
        ("{carer}", carer_name),
        ("{Carer}", carer_role),
        ("[Giver]", giver),
        ("[giver]", giver),
        ("[Object]", comfort),
        ("[object]", comfort),
    ):
        text = text.replace(old, new)

    slots = inputs.get("slots") or {}
    for key, value in slots.items():
        val = str(value)
        text = text.replace(f"[{key}]", val)
        text = text.replace(f"{{{key}}}", val)
        # Doc 00 token names
        pretty = key.replace("_", " ").title().replace(" ", "")
        text = text.replace(f"[{pretty}]", val)
        for token in (
            "Nursery",
            "School",
            "Teacher",
            "Exit line",
            "Broken thing",
            "Praise words",
            "Giver",
        ):
            if key.replace("_", " ").lower() == token.lower():
                text = text.replace(f"[{token}]", val)
    return text


def generate_content(job_dir: Path, inputs: dict, client=None) -> dict:
    """Materialise the locked story for this order. ``client`` is unused (API compat)."""
    del client  # no text model — manuscript comes from the story doc
    title = get_title(inputs["title_id"])
    locked = load_locked_story(inputs["title_id"])
    route = resolve_route(title) if title.get("route") else locked.get("route") or "morning_light"
    name = inputs["name"]
    giver = (inputs.get("giver") or (inputs.get("carer") or {}).get("name") or "").strip()

    append_event(
        job_dir,
        "content_start",
        f"Loading locked story {inputs['title_id']} ({locked.get('version') or 'doc'}) — no AI rewrite.",
    )

    book_title = personalize(locked.get("title_template") or title["title_template"], inputs)
    band = locked.get("front_band") or title["band"]
    question = personalize(locked.get("back_question") or title["question_form"], inputs)
    blurb = personalize(locked.get("back_blurb") or title.get("blurb") or "", inputs)
    honesty = personalize(
        locked.get("honesty_line")
        or "Illustrated from [Name]'s photo and checked by a person before print.",
        inputs,
    )
    parent_src = locked.get("parent_spread") or title.get("parent_spread") or {}
    parent = {k: personalize(str(v), inputs) for k, v in parent_src.items()}
    read_again = personalize(
        parent.get("read_it_again")
        or "Read it a few nights in a row, and again whenever [Name] asks.",
        inputs,
    )
    parent["read_it_again"] = read_again

    # Dedication comes from the locked story template only (no freeform create field).
    if giver:
        dedication = personalize(
            locked.get("dedication_template") or "For [Name], from [Giver]",
            inputs,
        )
    else:
        dedication = personalize("For [Name]", inputs)

    spreads_out = []
    for raw in locked.get("spreads") or []:
        n = int(raw["n"])
        calm = (raw.get("calm_zone") or "").strip().lower()
        if calm not in {"left", "right"}:
            calm = calm_zone_for(n)
        spreads_out.append(
            {
                "n": n,
                "text": personalize(raw.get("text") or "", inputs).strip(),
                "scene": personalize(raw.get("art") or raw.get("scene") or "", inputs).strip(),
                "art": personalize(raw.get("art") or "", inputs).strip(),
                "anchor": personalize(raw.get("anchor") or "", inputs).strip(),
                "calm_zone": calm,
                "calm_zone_note": personalize(raw.get("calm_zone_note") or "", inputs).strip(),
            }
        )
    if len(spreads_out) != SPREAD_COUNT:
        raise ValueError(
            f"Locked story {inputs['title_id']} must have {SPREAD_COUNT} spreads, found {len(spreads_out)}"
        )

    data = {
        "title": book_title,
        "front_band": band,
        "back_question": question,
        "back_blurb": blurb,
        "honesty_line": honesty,
        "spreads": spreads_out,
        "parent_spread": parent,
        "parent_art": personalize(locked.get("parent_art") or "", inputs).strip(),
        "dedication": dedication,
        "cover_art": personalize(locked.get("cover_art") or "", inputs).strip(),
        "title_page": {
            k: personalize(str(v), inputs) if isinstance(v, str) else v
            for k, v in (locked.get("title_page") or {}).items()
        },
        "route": route,
        "family": locked.get("family") or title["family"],
        "title_id": inputs["title_id"],
        "outfit": locked.get("outfit") or title.get("outfit"),
        "anchor": locked.get("anchor") or title.get("anchor"),
        "must_not_show": locked.get("must_not_show") or title.get("must_not_show") or [],
        "source_version": locked.get("version"),
        "locked": {
            "title": book_title,
            "front_band": band,
            "back_question": question,
            "honesty_line": honesty,
            "last_line": personalize(locked.get("last_line") or "", inputs),
            "parent_spread": parent,
            "read_it_again": read_again,
        },
    }

    story_dir = job_dir / "story"
    story_dir.mkdir(parents=True, exist_ok=True)
    write_json(story_dir / "manuscript.json", data)
    write_json(story_dir / "pages.json", {"pages": _pages_from_manuscript(data, inputs)})
    append_event(job_dir, "content_done", f"Manuscript ready from locked story: {book_title}")
    return data


def _pages_from_manuscript(data: dict, inputs: dict) -> list[dict]:
    del inputs  # reserved for future page-level personalisation
    pages = [
        {
            "number": 1,
            "kind": "title",
            "text": data["title"],
        }
    ]
    for sp in data["spreads"]:
        n = int(sp["n"])
        calm = sp["calm_zone"]
        pages.append(
            {
                "number": 2 * n,
                "kind": "spread_half",
                "spread": n,
                "side": "left",
                "calm_zone": calm,
                "text": sp["text"] if calm == "left" else "",
                "scene": sp.get("scene") or sp.get("art") or "",
            }
        )
        pages.append(
            {
                "number": 2 * n + 1,
                "kind": "spread_half",
                "spread": n,
                "side": "right",
                "calm_zone": calm,
                "text": sp["text"] if calm == "right" else "",
                "scene": sp.get("scene") or sp.get("art") or "",
            }
        )
    parent = data.get("parent_spread") or {}
    pages.append({"number": 30, "kind": "parent_left", "text": "", "parent": parent})
    pages.append({"number": 31, "kind": "parent_right", "text": "", "parent": parent})
    pages.append(
        {
            "number": 32,
            "kind": "dedication",
            "text": data.get("dedication") or "",
        }
    )
    return pages
