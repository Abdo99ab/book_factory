"""Validate story, pages, and illustration prompts before expensive image generation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .job_log import append_event, write_json
from .openai_util import chat_json
from .pages import compile_pages


ALLOWED_PLACEMENTS = {
    "top",
    "upper_third",
    "middle",
    "lower_third",
    "bottom",
}

STRUCTURAL_SYSTEM = """You are a strict QA editor for personalized children's picture books.
Validate the rebuilt story and page briefs BEFORE illustration.

Return JSON:
{
  "passed": boolean,
  "score": 0-100,
  "summary": "one sentence",
  "issues": [
    {
      "severity": "error" | "warning",
      "code": "short_code",
      "message": "what is wrong",
      "page": number or null,
      "fix_hint": "how to fix"
    }
  ],
  "invented_elements": ["names/props/mascots not grounded in the draft"],
  "repair": {
    "needed": boolean,
    "supporting_characters": [{"name":"","description":""}],
    "recurring_outfit": "string or null to leave unchanged",
    "page_fixes": [
      {"number": 1, "scene": "corrected scene", "text": "corrected text or null"}
    ]
  }
}

Hard errors (must set passed=false):
- Supporting characters, mascots, toys, or pets invented and NOT present in the draft story
- Hero name missing from personalized pages / story
- Page count wrong
- Scenes that demand painted letters/titles/captions in the illustration
- Unsafe / age-inappropriate content
- Empty scene or empty text on non-blank pages
- Story ignores the chosen theme in a major way

Warnings only (passed can still be true):
- Minor pacing issues
- Slightly long page text

repair.needed=true when invented elements or broken scenes can be fixed by rewriting
supporting_characters / outfit / selected page scenes+text. Do not invent new mascots
in repair — only remove or replace with draft-grounded characters (e.g. Mum/Dad if in draft).
"""


class ValidationError(RuntimeError):
    """Raised when content fails validation and cannot be auto-repaired."""

    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def _structural_issues(inputs: dict, story: dict, pages: list) -> list:
    issues = []
    page_count = int(inputs["page_count"])
    hero = str(inputs.get("name") or "").strip()

    if not story.get("title"):
        issues.append(
            {
                "severity": "error",
                "code": "missing_title",
                "message": "Story title is missing.",
                "page": None,
                "fix_hint": "Rebuild story with a clear title.",
            }
        )
    if not story.get("rebuilt_story"):
        issues.append(
            {
                "severity": "error",
                "code": "missing_story",
                "message": "Rebuilt story prose is missing.",
                "page": None,
                "fix_hint": "Rebuild the story from the draft.",
            }
        )
    if not story.get("recurring_outfit"):
        issues.append(
            {
                "severity": "error",
                "code": "missing_outfit",
                "message": "Recurring outfit lock is missing.",
                "page": None,
                "fix_hint": "Define a locked hero outfit.",
            }
        )
    if len(pages) != page_count:
        issues.append(
            {
                "severity": "error",
                "code": "page_count",
                "message": f"Expected {page_count} pages, found {len(pages)}.",
                "page": None,
                "fix_hint": "Recompile pages to the requested count.",
            }
        )

    numbers = [p.get("number") for p in pages]
    if len(numbers) != len(set(numbers)):
        issues.append(
            {
                "severity": "error",
                "code": "duplicate_pages",
                "message": "Duplicate page numbers found.",
                "page": None,
                "fix_hint": "Recompile pages with unique numbers.",
            }
        )

    text_in_art = re.compile(
        r"\b(paint|draw|render|include|show|write)\b.{0,40}\b(text|title|letters?|caption|words?)\b",
        re.I,
    )
    for page in pages:
        number = page.get("number")
        if page.get("blank"):
            continue
        if not (page.get("scene") or "").strip():
            issues.append(
                {
                    "severity": "error",
                    "code": "empty_scene",
                    "message": "Scene description is empty.",
                    "page": number,
                    "fix_hint": "Add a visual scene description.",
                }
            )
        if not (page.get("text") or "").strip():
            issues.append(
                {
                    "severity": "error",
                    "code": "empty_text",
                    "message": "Page story text is empty.",
                    "page": number,
                    "fix_hint": "Add readable story text for overlay.",
                }
            )
        placement = str(page.get("text_placement") or "").lower()
        if placement not in ALLOWED_PLACEMENTS:
            issues.append(
                {
                    "severity": "error",
                    "code": "bad_placement",
                    "message": f"Invalid text_placement '{page.get('text_placement')}'.",
                    "page": number,
                    "fix_hint": f"Use one of: {', '.join(sorted(ALLOWED_PLACEMENTS))}.",
                }
            )
        scene = page.get("scene") or ""
        if text_in_art.search(scene):
            issues.append(
                {
                    "severity": "error",
                    "code": "text_in_scene",
                    "message": "Scene asks for painted text/letters in the art.",
                    "page": number,
                    "fix_hint": "Remove text-in-image instructions; overlay handles copy.",
                }
            )
        if page.get("personalized") and hero and hero.lower() not in scene.lower():
            # soft structural warning — LLM will judge harder
            issues.append(
                {
                    "severity": "warning",
                    "code": "hero_not_named_in_scene",
                    "message": f"Personalized scene may not clearly feature {hero}.",
                    "page": number,
                    "fix_hint": f"Mention {hero} explicitly in the scene.",
                }
            )
    return issues


def _llm_validate(client, inputs: dict, story: dict, pages: list) -> dict:
    page_briefs = [
        {
            "number": p.get("number"),
            "title": p.get("title"),
            "scene": p.get("scene"),
            "text": p.get("text"),
            "text_placement": p.get("text_placement"),
            "personalized": p.get("personalized"),
            "blank": p.get("blank"),
        }
        for p in pages
    ]
    user = {
        "draft_story": inputs.get("draft_story"),
        "child_name": inputs.get("name"),
        "age": inputs.get("age"),
        "theme": inputs.get("theme"),
        "purpose": inputs.get("purpose"),
        "design_rules": inputs.get("design_rules"),
        "page_count": inputs.get("page_count"),
        "story": {
            "title": story.get("title"),
            "subtitle": story.get("subtitle"),
            "moral": story.get("moral"),
            "synopsis": story.get("synopsis"),
            "recurring_outfit": story.get("recurring_outfit"),
            "supporting_characters": story.get("supporting_characters") or [],
            "story_beats": story.get("story_beats") or [],
            "rebuilt_story": story.get("rebuilt_story"),
        },
        "pages": page_briefs,
    }
    raw = chat_json(
        client,
        STRUCTURAL_SYSTEM,
        json.dumps(user, ensure_ascii=True),
        description="Content validation",
    )
    return json.loads(raw)


def _apply_repair(story: dict, pages: list, repair: dict) -> tuple[dict, list]:
    if not repair or not repair.get("needed"):
        return story, pages

    if "supporting_characters" in repair and repair["supporting_characters"] is not None:
        story["supporting_characters"] = repair["supporting_characters"]
    if repair.get("recurring_outfit"):
        story["recurring_outfit"] = repair["recurring_outfit"]

    fixes = {int(item["number"]): item for item in repair.get("page_fixes") or [] if item.get("number")}
    for page in pages:
        fix = fixes.get(int(page["number"]))
        if not fix:
            continue
        if fix.get("scene"):
            page["scene"] = fix["scene"]
        if fix.get("text") is not None:
            page["text"] = fix["text"]
        if fix.get("text_placement"):
            page["text_placement"] = fix["text_placement"]
    return story, pages


def _merge_report(structural: list, llm_report: dict) -> dict:
    issues = list(structural) + list(llm_report.get("issues") or [])
    has_error = any(i.get("severity") == "error" for i in issues)
    passed = bool(llm_report.get("passed", False)) and not has_error
    # If structural errors exist, force fail even if LLM said pass
    if has_error:
        passed = False
    return {
        "passed": passed,
        "score": llm_report.get("score"),
        "summary": llm_report.get("summary")
        or ("Structural checks failed." if not passed else "Content validated."),
        "issues": issues,
        "invented_elements": llm_report.get("invented_elements") or [],
        "repair": llm_report.get("repair") or {"needed": False},
    }


def validate_content(client, job_dir: Path, inputs: dict, story: dict, pages: list) -> dict:
    """Validate and optionally auto-repair once. Raises ValidationError if still failing."""
    append_event(job_dir, "validation_start", "Validating story, pages, and prompts before illustration.")

    structural = _structural_issues(inputs, story, pages)
    llm_report = _llm_validate(client, inputs, story, pages)
    report = _merge_report(structural, llm_report)
    report["attempt"] = 1
    write_json(job_dir / "story" / "validation_report.json", report)
    append_event(
        job_dir,
        "validation_result",
        report["summary"],
        passed=report["passed"],
        issue_count=len(report["issues"]),
        invented_elements=report.get("invented_elements"),
    )

    if report["passed"]:
        append_event(job_dir, "validation_passed", "Content approved for illustration.")
        return report

    repair = report.get("repair") or {}
    if repair.get("needed"):
        append_event(job_dir, "validation_repair_start", "Auto-repairing invented or invalid content.")
        story, pages = _apply_repair(story, pages, repair)
        # If invented mascots were the issue and repair emptied supporting chars wrongly,
        # recompile pages from repaired story for consistency when many page_fixes missing.
        if not repair.get("page_fixes") and report.get("invented_elements"):
            pages = compile_pages(client, job_dir, inputs, story)
        write_json(job_dir / "story" / "rebuilt_story.json", story)
        write_json(job_dir / "story" / "pages.json", {"pages": pages})
        (job_dir / "story" / "rebuilt_story.txt").write_text(
            story.get("rebuilt_story") or "", encoding="utf-8"
        )

        structural2 = _structural_issues(inputs, story, pages)
        llm_report2 = _llm_validate(client, inputs, story, pages)
        report2 = _merge_report(structural2, llm_report2)
        report2["attempt"] = 2
        report2["repaired_from"] = report
        write_json(job_dir / "story" / "validation_report.json", report2)
        append_event(
            job_dir,
            "validation_result",
            report2["summary"],
            passed=report2["passed"],
            attempt=2,
            issue_count=len(report2["issues"]),
        )
        if report2["passed"]:
            append_event(job_dir, "validation_passed", "Content approved after auto-repair.")
            return report2
        report = report2

    write_json(job_dir / "story" / "validation_report.json", report)
    raise ValidationError(
        report.get("summary") or "Content validation failed.",
        report=report,
    )
