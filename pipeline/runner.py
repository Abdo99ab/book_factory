"""Job runner v2: content → character → preview → build → per-asset edit."""

from __future__ import annotations

import logging
from pathlib import Path

from .character import generate_character
from .constants import PREVIEW_SPREAD_COUNT, SPREAD_COUNT
from .content import generate_content
from .illustrate import (
    ensure_spreads,
    generate_cover_panels,
    generate_title_spot,
    regenerate_cover,
    regenerate_spread,
)
from .job_log import append_event, read_json, write_json
from .openai_util import create_client
from .pdf_build import build_pdf, render_front_preview

logger = logging.getLogger(__name__)


def _update_job(job_dir: Path, **fields) -> dict:
    job = read_json(job_dir / "job.json") or {}
    job.update(fields)
    write_json(job_dir / "job.json", job)
    return job


def _load_story(job_dir: Path) -> dict:
    story = read_json(job_dir / "story" / "manuscript.json")
    if not story:
        raise FileNotFoundError("manuscript.json missing — run content first.")
    return story


def _load_pages(job_dir: Path) -> list:
    data = read_json(job_dir / "story" / "pages.json") or {}
    return data.get("pages") or []


def _character_path(job_dir: Path) -> Path:
    path = job_dir / "character" / "character.png"
    if not path.is_file():
        raise FileNotFoundError("character.png missing")
    return path


def _reference(job_dir: Path) -> Path:
    refs = list((job_dir / "character").glob("reference.*"))
    if not refs:
        raise FileNotFoundError("Reference photo missing")
    return refs[0]


def run_prepare(job_id: str, job_dir: Path) -> None:
    """Content (one text call) + character sheet. Stops at content_ready."""
    inputs = read_json(job_dir / "inputs.json")
    if not inputs:
        raise FileNotFoundError("inputs.json missing")
    append_event(job_dir, "job_start", "v2 prepare: manuscript + character.")
    _update_job(job_dir, status="generating_content", error=None)
    client = create_client()
    try:
        story = generate_content(client, job_dir, inputs)
        inputs = {**inputs, "route": story.get("route")}
        write_json(job_dir / "inputs.json", inputs)
        _update_job(job_dir, status="generating_character", book_title=story.get("title"))
        if not (job_dir / "character" / "character.png").is_file():
            generate_character(client, job_dir, _reference(job_dir), inputs)
        _update_job(
            job_dir,
            status="content_ready",
            book_title=story.get("title"),
            error=None,
        )
        append_event(
            job_dir,
            "content_ready",
            "Manuscript and character ready. Run Preview (cover + 2 spreads) or Build.",
        )
    except Exception as error:
        logger.exception("Prepare failed %s", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_preview(job_id: str, job_dir: Path) -> None:
    """Front cover + spreads 1–2. Keeps existing assets."""
    inputs = read_json(job_dir / "inputs.json")
    append_event(job_dir, "preview_start", "Preview: front cover + first two spreads.")
    _update_job(job_dir, status="generating_preview", error=None)
    client = create_client()
    try:
        if not (job_dir / "story" / "manuscript.json").is_file():
            run_prepare(job_id, job_dir)
        story = _load_story(job_dir)
        character = _character_path(job_dir)
        generate_cover_panels(client, job_dir, inputs, story, character, front_only=True)
        ensure_spreads(
            client,
            job_dir,
            inputs,
            story,
            character,
            list(range(1, PREVIEW_SPREAD_COUNT + 1)),
        )
        try:
            render_front_preview(job_dir, inputs, story)
        except Exception as preview_err:  # noqa: BLE001
            append_event(job_dir, "cover_preview_failed", str(preview_err))
        _update_job(
            job_dir,
            status="preview_ready",
            preview_ready=True,
            error=None,
        )
        append_event(job_dir, "preview_ready", "Front cover + spreads 1–2 ready.")
    except Exception as error:
        logger.exception("Preview failed %s", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_build(job_id: str, job_dir: Path) -> Path:
    """Generate any missing art, then the full TJ PDF. Never regenerates existing files."""
    inputs = read_json(job_dir / "inputs.json")
    append_event(job_dir, "build_start", "Full build (reuse existing art).")
    _update_job(job_dir, status="building", error=None)
    client = create_client()
    try:
        if not (job_dir / "story" / "manuscript.json").is_file():
            run_prepare(job_id, job_dir)
        story = _load_story(job_dir)
        pages = _load_pages(job_dir)
        character = _character_path(job_dir)
        generate_title_spot(client, job_dir, inputs, story)
        generate_cover_panels(client, job_dir, inputs, story, character, front_only=False)
        ensure_spreads(client, job_dir, inputs, story, character, list(range(1, SPREAD_COUNT + 1)))
        _update_job(job_dir, status="building_pdf")
        pdf_path = build_pdf(job_dir, story, pages, inputs)
        _update_job(
            job_dir,
            status="complete",
            pdf=pdf_path.relative_to(job_dir).as_posix(),
            error=None,
        )
        append_event(job_dir, "job_complete", f"Book ready: {pdf_path.name}")
        return pdf_path
    except Exception as error:
        logger.exception("Build failed %s", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_edit_spread(job_id: str, job_dir: Path, n: int, instruction: str) -> None:
    inputs = read_json(job_dir / "inputs.json")
    story = _load_story(job_dir)
    character = _character_path(job_dir)
    client = create_client()
    _update_job(job_dir, status="editing", error=None)
    try:
        regenerate_spread(client, job_dir, inputs, story, character, int(n), instruction)
        # Rebuild PDF if we already had one so the edit is visible immediately.
        if (read_json(job_dir / "job.json") or {}).get("pdf") or all(
            (job_dir / "spreads" / f"spread-{i:02d}" / "art.png").is_file()
            for i in range(1, SPREAD_COUNT + 1)
        ) and (job_dir / "cover" / "back_art.png").is_file():
            pages = _load_pages(job_dir)
            if len(pages) == 32 and (job_dir / "cover" / "back_art.png").is_file():
                pdf_path = build_pdf(job_dir, story, pages, inputs)
                _update_job(job_dir, status="complete", pdf=pdf_path.relative_to(job_dir).as_posix())
            else:
                _update_job(job_dir, status="preview_ready" if int(n) <= PREVIEW_SPREAD_COUNT else "editing")
        else:
            prev = (read_json(job_dir / "job.json") or {}).get("status")
            _update_job(job_dir, status=prev if prev != "editing" else "preview_ready")
        append_event(job_dir, "edit_done", f"Spread {n} updated.", spread=int(n))
    except Exception as error:
        logger.exception("Edit spread failed %s", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_edit_cover(job_id: str, job_dir: Path, kind: str, instruction: str) -> None:
    inputs = read_json(job_dir / "inputs.json")
    story = _load_story(job_dir)
    character = _character_path(job_dir)
    client = create_client()
    _update_job(job_dir, status="editing", error=None)
    try:
        regenerate_cover(client, job_dir, inputs, story, character, kind, instruction)
        if kind == "front":
            try:
                render_front_preview(job_dir, inputs, story)
            except Exception:  # noqa: BLE001
                pass
        job = read_json(job_dir / "job.json") or {}
        if job.get("pdf") and (job_dir / "cover" / "back_art.png").is_file():
            pages = _load_pages(job_dir)
            if len(pages) == 32:
                pdf_path = build_pdf(job_dir, story, pages, inputs)
                _update_job(job_dir, status="complete", pdf=pdf_path.relative_to(job_dir).as_posix())
            else:
                _update_job(job_dir, status="preview_ready")
        else:
            _update_job(job_dir, status="preview_ready")
        append_event(job_dir, "edit_done", f"{kind} cover updated.")
    except Exception as error:
        logger.exception("Edit cover failed %s", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_edit_text(job_dir: Path, target: str, text: str) -> None:
    """Update manuscript text for a spread (n=1..14) or back_blurb / dedication / parent block.

    Does not call the image model — next PDF rebuild picks up the new words.
    """
    story = _load_story(job_dir)
    pages = _load_pages(job_dir)
    if target.startswith("spread:"):
        n = int(target.split(":", 1)[1])
        for sp in story["spreads"]:
            if int(sp["n"]) == n:
                sp["text"] = text.strip()
        for page in pages:
            if page.get("spread") == n and page.get("side") == page.get("calm_zone"):
                page["text"] = text.strip()
            elif page.get("spread") == n:
                page["text"] = ""
    elif target == "back_blurb":
        story["back_blurb"] = text.strip()
    elif target == "dedication":
        story["dedication"] = text.strip()
        for page in pages:
            if page.get("kind") == "dedication":
                page["text"] = text.strip()
    elif target.startswith("parent:"):
        key = target.split(":", 1)[1]
        story.setdefault("parent_spread", {})[key] = text.strip()
        story.setdefault("locked", {}).setdefault("parent_spread", {})[key] = text.strip()
        for page in pages:
            if page.get("kind") in {"parent_left", "parent_right"}:
                page["parent"] = story["parent_spread"]
    else:
        raise ValueError(f"Unknown text target: {target}")
    write_json(job_dir / "story" / "manuscript.json", story)
    write_json(job_dir / "story" / "pages.json", {"pages": pages})
    append_event(job_dir, "text_edit", f"Updated {target}.")
    inputs = read_json(job_dir / "inputs.json") or {}
    if (job_dir / "cover" / "back_art.png").is_file() and all(
        (job_dir / "spreads" / f"spread-{i:02d}" / "art.png").is_file() for i in range(1, SPREAD_COUNT + 1)
    ):
        pdf_path = build_pdf(job_dir, story, pages, inputs)
        _update_job(job_dir, status="complete", pdf=pdf_path.relative_to(job_dir).as_posix())
