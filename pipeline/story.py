import json
from pathlib import Path

from .brand import READ_IT_AGAIN_LINE
from .catalogue import get_title, load_story_beats
from .constants import OUTFIT_CONSTANT, pronouns_for
from .humanize import humanize_story_fields
from .job_log import append_event, write_json
from .memory import format_memory_for_prompt
from .openai_util import chat_json
from .text_guards import check_spreads

SYSTEM = """You are a UK English picture-book author and editor for Raised Ready books.
Write warm, concrete, age-appropriate prose. No stated moral. No AI clichés.
UK spelling only. Obey voice rules: short sentences, child acts, at most one adjective per noun.
Do not invent characters beyond the child, carer, and slot personalisation.
Return strict JSON only."""


def _format_beats(beats: list, name: str) -> list[dict]:
    formatted = []
    for beat in beats:
        text = (beat.get("text") or "").replace("{name}", name)
        formatted.append({"n": int(beat["n"]), "beat": text.strip()})
    return formatted


def _build_locked(title: dict, inputs: dict) -> dict:
    name = inputs["name"]
    parent = dict(title.get("parent_spread") or {})
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
        "beats": _format_beats(beats_payload["beats"], inputs["name"]),
        "must_not_show": title.get("must_not_show") or [],
        "last_line": title.get("last_line", "").replace("{name}", inputs["name"]),
        "story_override": inputs.get("story_override") or "",
        "preference_memory": format_memory_for_prompt(),
        "output_schema": {
            "spreads": [
                {
                    "n": "1..14",
                    "text": "story text for this spread",
                    "scene": "illustrator brief, no readable text in art",
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

    draft_payload = _generate()
    spreads = draft_payload.get("spreads") or []
    story_body = {
        "title_id": title_id,
        "title": locked["title"],
        "locked": locked,
        "spreads": spreads,
        "blurb_draft": draft_payload.get("blurb_draft") or title.get("blurb"),
        "recurring_outfit": recurring_outfit,
        "supporting_characters": [],
        "_cast_lock": _cast_lock(inputs),
        "anchor": title.get("anchor"),
        "must_not_show": title.get("must_not_show"),
    }

    draft_for_guards = {"spreads": spreads, "locked": locked}
    issues = check_spreads(
        draft_for_guards,
        story_body,
        locked,
        child_name=inputs["name"],
        age_band=inputs.get("age_band") or title["age_band"],
        last_line=title.get("last_line", "").replace("{name}", inputs["name"]),
    )
    if issues:
        fix_note = "; ".join(i["message"] for i in issues)
        append_event(job_dir, "story_guard_retry", fix_note, issues=issues)
        retry_payload = _generate(
            "Fix these guard violations and return JSON again:\n" + fix_note
        )
        story_body["spreads"] = retry_payload.get("spreads") or spreads
        # Baseline draft = retry output so word-growth only applies to humanize later
        post_retry_draft = {
            "spreads": story_body["spreads"],
            "locked": locked,
        }
        issues = check_spreads(
            post_retry_draft,
            story_body,
            locked,
            child_name=inputs["name"],
            age_band=inputs.get("age_band") or title["age_band"],
            last_line=title.get("last_line", "").replace("{name}", inputs["name"]),
        )
        if issues:
            append_event(job_dir, "story_guard_failed", "Guard checks failed after retry.", issues=issues)
            raise ValueError("Story guard checks failed: " + issues[0]["message"])

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
        post_issues = check_spreads(
            pre_humanize,
            story_body,
            locked,
            child_name=inputs["name"],
            age_band=inputs.get("age_band") or title["age_band"],
            last_line=title.get("last_line", "").replace("{name}", inputs["name"]),
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

    return story_body
