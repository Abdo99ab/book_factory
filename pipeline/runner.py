"""Orchestrates the full A-to-Z book generation job."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .character import generate_character
from .constants import PAGE_CONCURRENCY
from .illustrate import generate_cover_panels, generate_page_image
from .job_log import append_event, read_json, write_json
from .openai_util import create_client
from .pages import compile_pages
from .pdf_build import build_pdf
from .story import rebuild_story
from .validate import ValidationError, validate_content

logger = logging.getLogger(__name__)

PREVIEW_PAGE_COUNT = 3


def _update_job(job_dir: Path, **updates):
    path = job_dir / "job.json"
    job = read_json(path, default={})
    job.update(updates)
    job["updated_at"] = time.time()
    write_json(path, job)
    return job


def _count_completed_pages(job_dir: Path, pages: list) -> int:
    completed = 0
    for page in pages:
        page_dir = job_dir / "pages" / f"page-{page['number']:02d}"
        if page.get("blank") and (page_dir / "page.json").is_file():
            completed += 1
        elif (page_dir / "art.png").is_file():
            completed += 1
    return completed


def _preview_content_pages(pages: list, count: int = PREVIEW_PAGE_COUNT) -> list:
    """First N non-blank interior content pages (never cover)."""
    selected = []
    for page in sorted(pages, key=lambda item: item["number"]):
        if page.get("blank"):
            continue
        selected.append(page)
        if len(selected) >= count:
            break
    return selected


def _generate_page_batch(client, job_dir: Path, pages_to_make: list, inputs: dict, story: dict, character_path: Path, all_pages: list):
    pending = []
    for page in pages_to_make:
        page_dir = job_dir / "pages" / f"page-{page['number']:02d}"
        if page.get("blank") and (page_dir / "page.json").is_file():
            continue
        if (page_dir / "art.png").is_file():
            continue
        pending.append(page)

    _update_job(job_dir, current_page=_count_completed_pages(job_dir, all_pages))
    if not pending:
        append_event(job_dir, "pages_skip", "Requested pages already exist; not regenerating.")
        return

    errors = []
    with ThreadPoolExecutor(max_workers=PAGE_CONCURRENCY) as executor:
        futures = {
            executor.submit(
                generate_page_image,
                client,
                job_dir,
                page,
                inputs,
                story,
                character_path,
            ): page
            for page in pending
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                future.result()
            except Exception as error:
                logger.exception("Page %s failed", page["number"])
                errors.append(f"Page {page['number']}: {error}")
                append_event(
                    job_dir,
                    "page_error",
                    f"Page {page['number']} failed: {error}",
                )
            _update_job(
                job_dir,
                current_page=_count_completed_pages(job_dir, all_pages),
            )

    if errors:
        raise RuntimeError("; ".join(errors))


def prepare_content(client, job_dir: Path, inputs: dict):
    """Character + story rebuild + page briefs. No cover, no illustration."""
    reference_path = next((job_dir / "character").glob("reference.*"), None)
    if reference_path is None:
        raise FileNotFoundError("Reference image missing")

    _update_job(job_dir, status="generating_character", error=None)
    character_path = job_dir / "character" / "character.png"
    if not character_path.is_file():
        character_path = generate_character(client, job_dir, reference_path, inputs)
    else:
        append_event(job_dir, "character_skip", "Using existing character sheet.")

    _update_job(job_dir, status="rebuilding_story")
    story_path = job_dir / "story" / "rebuilt_story.json"
    if story_path.is_file():
        story = read_json(story_path)
        append_event(job_dir, "story_skip", "Using existing rebuilt story.")
    else:
        story = rebuild_story(client, job_dir, inputs)
    _update_job(job_dir, book_title=story.get("title"))

    _update_job(job_dir, status="compiling_pages")
    pages_path = job_dir / "story" / "pages.json"
    if pages_path.is_file():
        pages = read_json(pages_path)["pages"]
        append_event(job_dir, "pages_skip", "Using existing page compilation.")
    else:
        pages = compile_pages(client, job_dir, inputs, story)

    _update_job(
        job_dir,
        total_pages=len(pages),
        current_page=_count_completed_pages(job_dir, pages),
        book_title=story.get("title"),
    )
    return character_path, story, pages


def run_prepare(job_id: str, job_dir: Path):
    inputs = read_json(job_dir / "inputs.json")
    if not inputs:
        raise FileNotFoundError("inputs.json missing")

    append_event(job_dir, "job_start", "Preparing story content (no illustration yet).")
    _update_job(job_dir, status="starting", error=None)
    client = create_client()
    try:
        prepare_content(client, job_dir, inputs)
        _update_job(job_dir, status="content_ready", error=None)
        append_event(
            job_dir,
            "content_ready",
            "Story and pages ready. Choose Preview (3 content pages) or Validate & build.",
        )
    except Exception as error:
        logger.exception("Job %s prepare failed", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_preview(job_id: str, job_dir: Path):
    """Generate first 3 non-blank content pages only (no cover)."""
    inputs = read_json(job_dir / "inputs.json")
    if not inputs:
        raise FileNotFoundError("inputs.json missing")

    append_event(job_dir, "preview_start", f"Generating {PREVIEW_PAGE_COUNT} content pages (no cover).")
    _update_job(job_dir, status="generating_preview", error=None)
    client = create_client()
    try:
        character_path, story, pages = prepare_content(client, job_dir, inputs)
        preview_pages = _preview_content_pages(pages, PREVIEW_PAGE_COUNT)
        if not preview_pages:
            raise RuntimeError("No content pages available to preview.")

        numbers = [page["number"] for page in preview_pages]
        _update_job(
            job_dir,
            status="generating_preview",
            preview_page_numbers=numbers,
            preview_requested=True,
        )
        append_event(
            job_dir,
            "preview_pages_selected",
            f"Preview content pages: {numbers}",
            pages=numbers,
        )
        _generate_page_batch(
            client,
            job_dir,
            preview_pages,
            inputs,
            story,
            character_path,
            pages,
        )
        _update_job(
            job_dir,
            status="preview_ready",
            preview_ready=True,
            preview_page_numbers=numbers,
            current_page=_count_completed_pages(job_dir, pages),
            error=None,
        )
        append_event(
            job_dir,
            "preview_ready",
            f"Preview ready for pages {numbers}. Validate & build will keep these pages.",
        )
    except Exception as error:
        logger.exception("Job %s preview failed", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_validate_and_build(job_id: str, job_dir: Path):
    """Validate content, then build cover + remaining pages + PDF. Keeps existing page art."""
    inputs = read_json(job_dir / "inputs.json")
    if not inputs:
        raise FileNotFoundError("inputs.json missing")

    append_event(job_dir, "build_start", "Validate & build started.")
    _update_job(job_dir, status="starting", error=None)
    client = create_client()
    try:
        character_path, story, pages = prepare_content(client, job_dir, inputs)
        pages_path = job_dir / "story" / "pages.json"

        job = read_json(job_dir / "job.json") or {}
        validation_path = job_dir / "story" / "validation_report.json"
        prior = read_json(validation_path) if validation_path.is_file() else None
        already_validated = bool(job.get("validation_passed") and prior and prior.get("passed"))

        if already_validated:
            append_event(job_dir, "validation_skip", "Using prior passed validation.")
            story = read_json(job_dir / "story" / "rebuilt_story.json") or story
            pages = read_json(pages_path)["pages"]
        else:
            _update_job(job_dir, status="validating_content", error=None)
            try:
                validate_content(client, job_dir, inputs, story, pages)
            except ValidationError as error:
                _update_job(
                    job_dir,
                    status="validation_failed",
                    error=str(error),
                    validation_passed=False,
                )
                append_event(job_dir, "validation_failed", str(error))
                return
            story = read_json(job_dir / "story" / "rebuilt_story.json") or story
            pages = read_json(pages_path)["pages"]
            _update_job(
                job_dir,
                book_title=story.get("title"),
                total_pages=len(pages),
                validation_passed=True,
            )

        _update_job(job_dir, status="generating_cover")
        front = job_dir / "cover" / "front_art.png"
        back = job_dir / "cover" / "back_art.png"
        if not (front.is_file() and back.is_file()):
            generate_cover_panels(client, job_dir, inputs, story, character_path)
        else:
            append_event(job_dir, "cover_skip", "Using existing cover panels.")

        _update_job(job_dir, status="generating_pages")
        # Existing preview/content art is skipped inside _generate_page_batch.
        _generate_page_batch(
            client,
            job_dir,
            pages,
            inputs,
            story,
            character_path,
            pages,
        )

        _update_job(job_dir, status="building_pdf")
        pdf_path = build_pdf(job_dir, story, pages, inputs)
        _update_job(
            job_dir,
            status="complete",
            pdf="final_book.pdf",
            current_page=len(pages),
            error=None,
        )
        append_event(job_dir, "job_complete", f"Book ready: {pdf_path.name}")

        # Auto self-revise once: vision-QA composited pages and apply precise fixes.
        # Failures here must not mark the finished PDF job as failed.
        job_now = read_json(job_dir / "job.json") or {}
        if not job_now.get("self_revise_reviewed") and job_now.get("status") != "self_revise_ready":
            try:
                from .self_revise import run_self_revise

                append_event(
                    job_dir,
                    "self_revise_auto_start",
                    "Starting automatic self-revise review (plan awaits your approval).",
                )
                run_self_revise(client, job_dir, inputs, force=False)
            except Exception as revise_error:
                logger.exception("Self-revise failed for %s", job_id)
                append_event(job_dir, "self_revise_failed", str(revise_error))
                _update_job(
                    job_dir,
                    status="complete",
                    error=f"Self-revise failed: {revise_error}",
                    self_revise={
                        "phase": "failed",
                        "summary": str(revise_error),
                    },
                )
    except Exception as error:
        logger.exception("Job %s build failed", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_job(job_id: str, job_dir: Path, mode: str | None = None):
    """
    mode:
      - prepare: stop at content_ready
      - preview: 3 content pages, stop at preview_ready
      - validate_and_build / full: validate + cover + all pages + PDF
      - None: read start_mode from job/inputs (default full)
    """
    job = read_json(job_dir / "job.json") or {}
    inputs = read_json(job_dir / "inputs.json") or {}
    resolved = mode or job.get("run_mode") or inputs.get("start_mode") or "full"

    if resolved in {"prepare", "content"}:
        return run_prepare(job_id, job_dir)
    if resolved in {"preview", "preview_first"}:
        return run_preview(job_id, job_dir)
    return run_validate_and_build(job_id, job_dir)
