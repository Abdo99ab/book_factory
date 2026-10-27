import json
from pathlib import Path

from .brand import READ_IT_AGAIN_LINE
from .catalogue import get_title, load_story_beats, resolve_carer_text
from .constants import OUTFIT_CONSTANT, pronouns_for
from .humanize import humanize_story_fields
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import chat_json
from .text_guards import (
    SOFT_GUARD_CODES,
    check_spreads,
    ensure_catalogue_last_line,
    hard_issues,
)

SYSTEM = """You are a UK English picture-book author and editor for Raised Ready books.
Write warm, concrete, age-appropriate prose. No stated moral. No AI clichés.
UK spelling only. Obey voice rules: short sentences, child acts, at most one adjective per noun.
Do not invent characters beyond the child, carer, and slot personalisation.

BEATS ARE BINDING. The "beats" list gives the 14 spreads: write each spread from its beat with the
SAME events in the SAME order. Add nothing: no new events, props, characters, places or morals.
You are giving the beat its final wording, not rewriting the story. The last spread (14) must end
with the exact "last_line" given, word for word.

ONE CONTINUOUS PICTURE PER SPREAD. Every spread is ONE wide picture printed across two facing pages
(it is cut down the middle at print time, so it must read as a single scene). For each spread return:
- "scene": ONE paragraph describing the left half and the right half of the same room and the same
  moment. Say what is on the left of the picture and what is on the right, using the words "left"
  and "right". The child and the key prop sit well away from the centre fold.
- "calm_zone": "left" or "right". The calm-zone half is the quieter half: a large plain,
  uncluttered area where the text will sit, no face close-up. By default the calm zone is the left
  half on odd spreads and the right half on even spreads; you may swap it when the beat reads better
  that way. The other half carries the action: the child's face clear, centred in that half.
The scene describes the picture only: no readable text in the art.
Return strict JSON only."""


def with_calm_zones(spreads: list) -> list:
    """Default calm_zone alternates (odd = left, even = right); a model-chosen left/right is kept.

    Anything else is left untouched so the text guard (which only requires left|right) reports it.
    """
    out = []
    for spread in spreads or []:
        item = dict(spread)
        zone = str(item.get("calm_zone") or "").strip().lower()
        if not zone:
            zone = "left" if int(item.get("n") or 0) % 2 == 1 else "right"
        item["calm_zone"] = zone
        out.append(item)
    return out


def _format_beats(beats: list, name: str, inputs: dict | None = None) -> list[dict]:
    formatted = []
    for beat in beats:
        text = (beat.get("text") or "").replace("{name}", name)
        text = resolve_carer_text(text, inputs or {})
        formatted.append({"n": int(beat["n"]), "beat": text.strip()})
    return formatted


def _build_locked(title: dict, inputs: dict) -> dict:
    name = inputs["name"]
    parent = {
        key: str(value).replace("{name}", name)
        for key, value in (title.get("parent_spread") or {}).items()
    }
    parent["read_it_again"] = READ_IT_AGAIN_LINE
    return {
        "title": title["title_template"].replace("{name}", name),
        "front_band": title["band"],
        "back_question": title["question_form"].replace("{name}", name),
        "parent_spread": parent,
        "dedication": (inputs.get("dedication_text") or "").strip(),
        "family": title.get("family"),
        "colour": title.get("colour"),
    }


def _cast_lock(inputs: dict) -> dict:
    carer = inputs.get("carer") or {}
    allowed = [inputs["name"], carer.get("name", "")]
    allowed = [n for n in allowed if n]
    for value in (inputs.get("slots") or {}).values():
        if value:
            allowed.append(str(value))
    return {
        "allowed_names": allowed,
        "forbidden_names": [],
    }


def rebuild_story(client, job_dir: Path, inputs: dict) -> dict:
    title_id = inputs["title_id"]
    title = get_title(title_id)
    beats_payload = load_story_beats(title_id)
    pronouns = pronouns_for(inputs.get("pronouns") or inputs.get("gender"))
    locked = _build_locked(title, inputs)
    outfit = title.get("outfit") or ""
    recurring_outfit = outfit if OUTFIT_CONSTANT else outfit

    user = {
        "child_name": inputs["name"],
        "age": inputs["age"],
        "age_band": inputs.get("age_band") or title["age_band"],
        "pronouns": pronouns,
        "carer": inputs.get("carer"),
        "home_type": inputs.get("home_type"),
        "comfort_object": inputs.get("comfort_object"),
        "slots": inputs.get("slots") or {},
        "beats": _format_beats(beats_payload["beats"], inputs["name"], inputs),
        "beats_binding": True,
        "carer_signature": resolve_carer_text(
            beats_payload["meta"].get("carer_signature") or "", inputs
        ),
        "sets": list(beats_payload["meta"].get("sets") or []),
        "must_not_show": title.get("must_not_show") or [],
        "last_line": title.get("last_line", "").replace("{name}", inputs["name"]),
        "story_override": inputs.get("story_override") or "",
        "preference_memory": format_memory_for_prompt(),
        "output_schema": {
            "spreads": [
                {
                    "n": "1..14",
                    "text": "story text for this spread",
                    "scene": (
                        "ONE paragraph: what is on the LEFT half and what is on the RIGHT half of "
                        "the same moment in one continuous picture (use the words left and right); "
                        "no readable text in art"
                    ),
                    "calm_zone": "left or right (default: left on odd spreads, right on even spreads)",
                }
            ],
            "blurb_draft": "optional ≤45 words",
        },
    }

    append_event(
        job_dir,
        "story_rebuild_start",
        f"Writing 14 spreads for title_id={title_id}.",
    )

    def _generate(extra_note: str = "") -> dict:
        payload_user = dict(user)
        if extra_note:
            payload_user["guard_fixes"] = extra_note
        raw = chat_json(
            client,
            SYSTEM + ("\n" + extra_note if extra_note else ""),
            json.dumps(payload_user, ensure_ascii=True),
            description="Story spreads",
        )
        return json.loads(raw)

    catalogue_last_line = title.get("last_line", "").replace("{name}", inputs["name"])

    draft_payload = _generate()
    spreads = ensure_catalogue_last_line(
        with_calm_zones(draft_payload.get("spreads") or []), catalogue_last_line
    )
    story_body = {
        "title_id": title_id,
        "title": locked["title"],
        "locked": locked,
        "spreads": spreads,
        "blurb_draft": draft_payload.get("blurb_draft") or title.get("blurb"),
        "recurring_outfit": recurring_outfit,
        "supporting_characters": [],
        "carer_signature": user["carer_signature"],
        "_cast_lock": _cast_lock(inputs),
        "anchor": title.get("anchor"),
        "must_not_show": title.get("must_not_show"),
    }

    draft_for_guards = {"spreads": spreads, "locked": locked}
    guard_kwargs = dict(
        child_name=inputs["name"],
        age_band=inputs.get("age_band") or title["age_band"],
        last_line=catalogue_last_line,
        require_scenes=True,
    )
    issues = check_spreads(draft_for_guards, story_body, locked, **guard_kwargs)
    blocking = hard_issues(issues)
    soft = [i for i in issues if i.get("code") in SOFT_GUARD_CODES]
    if soft and not blocking:
        append_event(
            job_dir,
            "story_guard_warning",
            "Soft guard warnings (job continues).",
            issues=soft,
        )
    elif blocking:
        fix_note = "; ".join(i["message"] for i in blocking)
        append_event(job_dir, "story_guard_retry", fix_note, issues=blocking)
        retry_payload = _generate(
            "Fix these guard violations and return JSON again:\n" + fix_note
        )
        story_body["spreads"] = ensure_catalogue_last_line(
            with_calm_zones(retry_payload.get("spreads") or spreads), catalogue_last_line
        )
        # Baseline draft = retry output so word-growth only applies to humanize later
        post_retry_draft = {
            "spreads": story_body["spreads"],
            "locked": locked,
        }
        issues = check_spreads(post_retry_draft, story_body, locked, **guard_kwargs)
        soft = [i for i in issues if i.get("code") in SOFT_GUARD_CODES]
        blocking = hard_issues(issues)
        if soft:
            append_event(
                job_dir,
                "story_guard_warning",
                "Soft guard warnings after retry (job continues).",
                issues=soft,
            )
        if blocking:
            append_event(
                job_dir,
                "story_guard_failed",
                "Guard checks failed after retry.",
                issues=blocking,
            )
            raise ValueError("Story guard checks failed: " + blocking[0]["message"])

    write_json(job_dir / "story" / "rebuilt_story.json", story_body)
    append_event(
        job_dir,
        "story_rebuild_done",
        f"Spreads ready for {locked['title']}.",
        title=locked["title"],
    )

    pre_humanize = json.loads(json.dumps(story_body))
    story_body = humanize_story_fields(client, job_dir, story_body)
    if story_body.get("humanized"):
        # Humanize must not drop the locked last line.
        story_body["spreads"] = ensure_catalogue_last_line(
            story_body.get("spreads") or [], catalogue_last_line
        )
        post_issues = check_spreads(
            pre_humanize,
            story_body,
            locked,
            child_name=inputs["name"],
            age_band=inputs.get("age_band") or title["age_band"],
            last_line=catalogue_last_line,
            require_scenes=True,
        )
        if post_issues:
            story_body = pre_humanize
            story_body["humanized"] = False
            story_body["humanize_skipped"] = True
            story_body["humanize_skip_reasons"] = post_issues
            write_json(job_dir / "story" / "rebuilt_story.json", story_body)
            append_event(
                job_dir,
                "humanize_story_skipped",
                "Humanize output failed guards; kept pre-humanize spreads.",
                reasons=post_issues,
            )

    story_body["spreads"] = ensure_catalogue_last_line(
        story_body.get("spreads") or [], catalogue_last_line
    )
    write_json(job_dir / "story" / "rebuilt_story.json", story_body)
    return story_body
