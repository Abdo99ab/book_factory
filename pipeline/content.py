"""One-shot manuscript generation from catalogue beats (v2).

No validation loop and no separate humanize pass — UK English + brand voice are
part of the single OpenAI call. Beats stay binding: same events, same order, exact last line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .brand import HONESTY_LINE, READ_IT_AGAIN_LINE, UK_SPELLING, word_cap_for_age_band
from .catalogue import get_title, load_story_beats, resolve_route
from .job_log import append_event, write_json
from .openai_util import chat_json

SPREAD_COUNT = 14


def _fill(template: str, inputs: dict) -> str:
    name = inputs["name"]
    carer = inputs["carer"]["name"]
    role = inputs["carer"]["role"]
    text = (
        template.replace("{name}", name)
        .replace("{carer}", carer)
        .replace("{Carer}", carer)
        .replace("{role}", role)
    )
    for key, value in (inputs.get("slots") or {}).items():
        text = text.replace(f"{{{key}}}", str(value))
        text = text.replace(f"slot:{key}", str(value))
    return text


def _apply_uk(text: str) -> str:
    out = text
    for us, uk in UK_SPELLING.items():
        out = re.sub(rf"\b{us}\b", uk, out, flags=re.IGNORECASE)
    return out


def calm_zone_for(n: int) -> str:
    """Spread 1 left, 2 right, 3 left…"""
    return "left" if int(n) % 2 == 1 else "right"


SYSTEM = """You write Raised Ready personalised picture-book manuscripts.

VOICE: warm UK nursery key worker — calm, specific, never preachy. Story voice for spreads
(short sentences, the child acting). Parent voice for the closing spread (plain, practical).

BINDING RULES:
- Keep the SAME events in the SAME order as the beats. Do not invent new plot beats.
- The last spread's last line MUST be exactly the catalogue last_line provided.
- UK English only (colour, practise as verb, nappies, nursery, mum not mom).
- Humanise: natural spoken rhythm, no lecture, no outcome claims ("fixes", "in 3 days").
- No deficit words about the child (naughty, behind, struggling).
- Word count per spread at or under the given cap.

Return JSON only with this shape:
{
  "title": string,
  "front_band": string,
  "back_question": string,
  "back_blurb": string,
  "honesty_line": string,
  "spreads": [
    {"n": 1, "text": string, "scene": string, "calm_zone": "left"|"right"}
  ],
  "parent_spread": {
    "you_could_say": string,
    "try_together": string,
    "in_real_life": string,
    "read_it_again": string
  },
  "dedication": string
}

Exactly 14 spreads. calm_zone MUST alternate: 1 left, 2 right, 3 left…
scene = short illustrator brief (no text in the picture). text = words printed on the calm half.
read_it_again MUST be the fixed line provided. honesty_line MUST be the fixed honesty line provided.
"""


def generate_content(client, job_dir: Path, inputs: dict) -> dict:
    title = get_title(inputs["title_id"])
    beats = load_story_beats(inputs["title_id"])
    route = resolve_route(title)
    cap = word_cap_for_age_band(title["age_band"])
    name = inputs["name"]
    last_line = _fill(title["last_line"] or beats["meta"].get("last_line") or "", inputs)
    honesty = HONESTY_LINE.format(name=name)
    band = title["band"]
    question = _fill(title["question_form"], inputs)
    book_title = _fill(title["title_template"], inputs)
    parent_src = title.get("parent_spread") or {}

    beat_block = []
    for beat in beats["beats"]:
        beat_block.append(f"### Spread {beat['n']}\n{_fill(beat['text'], inputs)}")

    user = f"""Title id: {inputs['title_id']}
Child: {name}, age {inputs['age']}, pronouns {inputs['pronouns']}
Carer: {inputs['carer']['name']} ({inputs['carer']['role']})
Home: {inputs.get('home_type')}
Comfort object: {inputs.get('comfort_object')}
Slots: {json.dumps(inputs.get('slots') or {})}
Outfit (from catalogue): {title.get('outfit')}
Anchor: {json.dumps(title.get('anchor') or {})}
Must not show: {json.dumps(title.get('must_not_show') or [])}
Route: {route}
Word cap per spread: {cap}
Fixed last_line: {last_line!r}
Fixed front_band: {band!r}
Fixed back_question: {question!r}
Fixed title: {book_title!r}
Fixed honesty_line: {honesty!r}
Fixed read_it_again: {READ_IT_AGAIN_LINE!r}
Catalogue parent blocks (rewrite into brand voice, keep intent):
{json.dumps(parent_src, indent=2)}
Dedication hint from form: {(inputs.get('dedication_text') or '').strip() or f'For {name}, from {inputs["carer"]["name"]}'}
Blurb seed: {_fill(title.get('blurb') or '', inputs)}

BEATS (binding — same events, same order):
{chr(10).join(beat_block)}
"""

    append_event(job_dir, "content_start", "Generating full manuscript in one call.")
    raw = chat_json(client, SYSTEM, user, description="Manuscript")
    data = json.loads(raw)

    # Deterministic locks the model must not drift on
    data["title"] = book_title
    data["front_band"] = band
    data["back_question"] = question
    data["honesty_line"] = honesty
    data.setdefault("parent_spread", {})
    data["parent_spread"]["read_it_again"] = READ_IT_AGAIN_LINE
    data["route"] = route
    data["family"] = title["family"]
    data["title_id"] = inputs["title_id"]
    data["locked"] = {
        "title": book_title,
        "front_band": band,
        "back_question": question,
        "honesty_line": honesty,
        "last_line": last_line,
        "parent_spread": data["parent_spread"],
    }

    spreads = list(data.get("spreads") or [])
    by_n = {int(s["n"]): s for s in spreads if "n" in s}
    normalised = []
    for n in range(1, SPREAD_COUNT + 1):
        sp = by_n.get(n) or {"n": n, "text": _fill(beats["beats"][n - 1]["text"], inputs), "scene": ""}
        sp["n"] = n
        sp["calm_zone"] = calm_zone_for(n)
        sp["text"] = _apply_uk((sp.get("text") or "").strip())
        sp["scene"] = (sp.get("scene") or "").strip()
        if n == SPREAD_COUNT and last_line and last_line not in sp["text"]:
            sp["text"] = (sp["text"].rstrip(".") + ". " + last_line).strip()
        normalised.append(sp)
    data["spreads"] = normalised
    data["back_blurb"] = _apply_uk((data.get("back_blurb") or "").strip())
    data["dedication"] = _apply_uk(
        (data.get("dedication") or inputs.get("dedication_text") or f"For {name}, from {inputs['carer']['name']}").strip()
    )

    story_dir = job_dir / "story"
    story_dir.mkdir(parents=True, exist_ok=True)
    write_json(story_dir / "manuscript.json", data)
    # Flat page list for PDF (32 content pages)
    pages = _pages_from_manuscript(data, inputs)
    write_json(story_dir / "pages.json", {"pages": pages})
    append_event(job_dir, "content_done", f"Manuscript ready: {book_title}")
    return data


def _pages_from_manuscript(data: dict, inputs: dict) -> list[dict]:
    pages = [
        {
            "number": 1,
            "kind": "title",
            "text": data["title"],
        }
    ]
    for sp in data["spreads"]:
        n = int(sp["n"])
        left_num = 2 * n
        right_num = 2 * n + 1
        calm = sp["calm_zone"]
        pages.append(
            {
                "number": left_num,
                "kind": "spread_half",
                "spread": n,
                "side": "left",
                "calm_zone": calm,
                "text": sp["text"] if calm == "left" else "",
                "scene": sp["scene"],
            }
        )
        pages.append(
            {
                "number": right_num,
                "kind": "spread_half",
                "spread": n,
                "side": "right",
                "calm_zone": calm,
                "text": sp["text"] if calm == "right" else "",
                "scene": sp["scene"],
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
