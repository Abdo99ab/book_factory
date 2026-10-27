"""Orchestrates the full A-to-Z book generation job."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .character import generate_anchor_sheet, generate_character, generate_set_sheets
from .constants import PAGE_CONCURRENCY, PPI_FAIL, SPREAD_COUNT
from .illustrate import (
    PrintResolutionError,
    assert_print_resolution,
    generate_cover_panels,
    generate_spread_art,
    generate_title_spot,
    min_effective_ppi,
    spread_art_exists,
)
from .job_log import append_event, read_json, write_json
from .openai_util import create_client
from .pages import compile_pages, spread_art_path, spread_pages
from .pdf_build import build_pdf
from .qa import run_qa
from .story import rebuild_story
from .validate import ValidationError, validate_content

logger = logging.getLogger(__name__)

# Preview = the first spreads, generated as page pairs (left, then right with left as reference).
PREVIEW_SPREAD_COUNT = 2
PREVIEW_PAGE_COUNT = PREVIEW_SPREAD_COUNT * 2
ART_PAGE_COUNT = SPREAD_COUNT * 2  # 28 page images = 14 pairs


def _update_job(job_dir: Path, **updates):
    path = job_dir / "job.json"
    job = read_json(path, default={})
    job.update(updates)
    job["updated_at"] = time.time()
    write_json(path, job)
    return job


def _count_completed_pages(job_dir: Path, pages: list | None = None) -> int:
    """Number of page images on disk (0..28)."""
    return sum(
        1
        for n in range(1, SPREAD_COUNT + 1)
        for side in ("left", "right")
        if spread_art_path(job_dir, n, side).is_file()
    )


def _preview_spreads(pages: list, count: int = PREVIEW_SPREAD_COUNT) -> list[int]:
    """First N spread numbers (never the cover)."""
    return sorted(spread_pages(pages))[:count]


def _spread_page_numbers(spread_numbers: list[int]) -> list[int]:
    return [p for n in spread_numbers for p in (2 * n, 2 * n + 1)]


def _generate_spread_batch(
    client,
    job_dir: Path,
    spread_numbers: list[int],
    inputs: dict,
    story: dict,
    character_path: Path,
    all_pages: list,
):
    briefs = spread_pages(all_pages)
    pending = [n for n in spread_numbers if not spread_art_exists(job_dir, n)]

    _update_job(job_dir, current_page=_count_completed_pages(job_dir))
    if not pending:
        append_event(job_dir, "pages_skip", "Requested spreads already exist; not regenerating.")
        return

    errors = []
    resolution_failed = None
    with ThreadPoolExecutor(max_workers=PAGE_CONCURRENCY) as executor:
        futures = {
            executor.submit(
                generate_spread_art,
                client,
                job_dir,
                {**briefs[n], "spread": n},
                inputs,
                story,
                character_path,
            ): n
            for n in pending
        }
        for future in as_completed(futures):
            n = futures[future]
            try:
                meta = future.result()
                if meta.get("resolution_status") == "fail" and resolution_failed is None:
                    resolution_failed = meta.get("effective_ppi")
                    from .constants import ENFORCE_PRINT_PPI

                    if ENFORCE_PRINT_PPI:
                        # Stop spending on pages that cannot print when enforcement is on.
                        for other in futures:
                            other.cancel()
            except Exception as error:
                logger.exception("Spread %s failed", n)
                errors.append(f"Spread {n}: {error}")
                append_event(job_dir, "page_error", f"Spread {n} failed: {error}")
            _update_job(job_dir, current_page=_count_completed_pages(job_dir))

    _update_job(job_dir, min_effective_ppi=min_effective_ppi(job_dir))
    from .constants import ENFORCE_PRINT_PPI

    if resolution_failed is not None and ENFORCE_PRINT_PPI:
        raise PrintResolutionError(
            f"Art is {resolution_failed} ppi in the 216 mm box (< {PPI_FAIL}). "
            "The model returned too few pixels and there is no upscaler."
        )
    if resolution_failed is not None and not ENFORCE_PRINT_PPI:
        append_event(
            job_dir,
            "print_resolution_ignored",
            f"Art is {resolution_failed} ppi (< {PPI_FAIL}); continuing — ENFORCE_PRINT_PPI is off.",
            effective_ppi=resolution_failed,
        )
    if errors:
        raise RuntimeError("; ".join(errors))


def _illustrate_and_build(
    client,
    job_dir: Path,
    inputs: dict,
    story: dict,
    pages: list,
    character_path: Path,
) -> Path:
    """Title spot + cover + 14 spread pairs (skipping existing art), resolution gate, PDF."""
    _update_job(job_dir, status="generating_cover")
    if not (job_dir / "title_page" / "spot.png").is_file():
        generate_title_spot(client, job_dir, inputs, story)
    front = job_dir / "cover" / "front_art.png"
    back = job_dir / "cover" / "back_art.png"
    if not (front.is_file() and back.is_file()):
        generate_cover_panels(client, job_dir, inputs, story, character_path)
    else:
        append_event(job_dir, "cover_skip", "Using existing cover panels.")

    _update_job(job_dir, status="generating_pages")
    # Existing preview art is skipped inside _generate_spread_batch.
    _generate_spread_batch(
        client,
        job_dir,
        list(range(1, SPREAD_COUNT + 1)),
        inputs,
        story,
        character_path,
        pages,
    )
    # PPI gate is warn-only unless ENFORCE_PRINT_PPI=1.
    lowest = assert_print_resolution(job_dir)

    _update_job(job_dir, status="building_pdf", min_effective_ppi=lowest)
    pdf_path = build_pdf(job_dir, story, pages, inputs)
    _update_job(
        job_dir,
        status="complete",
        pdf=pdf_path.relative_to(job_dir).as_posix(),
        current_page=_count_completed_pages(job_dir),
        min_effective_ppi=lowest,
        error=None,
    )
    try:
        run_qa(job_dir)  # deterministic reports; Send stays blocked until a human also signs off
    except Exception as qa_error:  # never lose a finished build to a QA crash
        append_event(job_dir, "qa_failed_to_run", str(qa_error))
    return pdf_path


def prepare_content(client, job_dir: Path, inputs: dict):
    """Character sheets (gate) + story rebuild + page briefs. No cover, no illustration."""
    reference_path = next((job_dir / "character").glob("reference.*"), None)
    if reference_path is None:
        raise FileNotFoundError("Reference image missing")

    job = read_json(job_dir / "job.json") or {}

    _update_job(job_dir, status="generating_character", error=None)
    character_path = job_dir / "character" / "character.png"
    if not character_path.is_file():
        character_path = generate_character(client, job_dir, reference_path, inputs)
    else:
        append_event(job_dir, "character_skip", "Using existing character sheet.")

    if not (job_dir / "sets").is_dir() or not any((job_dir / "sets").glob("*.png")):
        generate_set_sheets(client, job_dir, inputs)
    if not (job_dir / "anchor" / "anchor.png").is_file():
        generate_anchor_sheet(client, job_dir, inputs)

    job = read_json(job_dir / "job.json") or {}
    if not job.get("character_approved_at"):
        _update_job(job_dir, status="character_ready", error=None)
        append_event(
            job_dir,
            "character_awaiting_approval",
            "Hero, set and anchor sheets ready — approve before story generation.",
        )
        return character_path, None, None

    _update_job(job_dir, status="rebuilding_story")
    story_path = job_dir / "story" / "rebuilt_story.json"
    if story_path.is_file():
        story = read_json(story_path)
        append_event(job_dir, "story_skip", "Using existing rebuilt story.")
    else:
        _update_job(job_dir, status="humanizing_story")
        story = rebuild_story(client, job_dir, inputs)
    _update_job(job_dir, book_title=story.get("title"))

    _update_job(job_dir, status="compiling_pages")
    pages_path = job_dir / "story" / "pages.json"
    if pages_path.is_file():
        pages = read_json(pages_path)["pages"]
        append_event(job_dir, "pages_skip", "Using existing page compilation.")
    else:
        _update_job(job_dir, status="humanizing_pages")
        pages = compile_pages(client, job_dir, inputs, story)

    _update_job(
        job_dir,
        total_pages=ART_PAGE_COUNT,  # 28 page images = 14 spreads x 2
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
        _character_path, story, pages = prepare_content(client, job_dir, inputs)
        if story is None:
            _update_job(job_dir, status="character_ready", error=None)
            append_event(
                job_dir,
                "character_ready",
                "Approve the hero sheet to continue to story and pages.",
            )
            return
        _update_job(job_dir, status="content_ready", error=None)
        append_event(
            job_dir,
            "content_ready",
            f"Story and pages ready. Choose Preview ({PREVIEW_SPREAD_COUNT} spreads) or Validate & build.",
        )
    except Exception as error:
        logger.exception("Job %s prepare failed", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_preview(job_id: str, job_dir: Path):
    """Generate the first spreads as page pairs (no cover)."""
    inputs = read_json(job_dir / "inputs.json")
    if not inputs:
        raise FileNotFoundError("inputs.json missing")

    append_event(
        job_dir,
        "preview_start",
        f"Generating {PREVIEW_SPREAD_COUNT} spreads ({PREVIEW_PAGE_COUNT} page images, no cover).",
    )
    _update_job(job_dir, status="generating_preview", error=None)
    client = create_client()
    try:
        character_path, story, pages = prepare_content(client, job_dir, inputs)
        if story is None or pages is None:
            raise RuntimeError("Approve the hero character sheet before preview.")
        preview_spreads = _preview_spreads(pages, PREVIEW_SPREAD_COUNT)
        if not preview_spreads:
            raise RuntimeError("No spreads available to preview.")

        numbers = _spread_page_numbers(preview_spreads)
        _update_job(
            job_dir,
            status="generating_preview",
            preview_page_numbers=numbers,
            preview_requested=True,
        )
        append_event(
            job_dir,
            "preview_pages_selected",
            f"Preview spreads {preview_spreads} (pages {numbers})",
            pages=numbers,
            spreads=preview_spreads,
        )
        _generate_spread_batch(
            client,
            job_dir,
            preview_spreads,
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
                total_pages=ART_PAGE_COUNT,
                validation_passed=True,
            )

        pdf_path = _illustrate_and_build(
            client, job_dir, inputs, story, pages, character_path
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


def run_from_template(job_id: str, job_dir: Path):
    """Reuse approved template text: new sheets + all 28 page images + cover + PDF.

    Story/pages are already on disk (slots filled, locked copy rebuilt). Does not rewrite the text.
    """
    inputs = read_json(job_dir / "inputs.json")
    if not inputs:
        raise FileNotFoundError("inputs.json missing")

    story = read_json(job_dir / "story" / "rebuilt_story.json")
    pages_payload = read_json(job_dir / "story" / "pages.json")
    if not story or not pages_payload:
        raise FileNotFoundError("Template story/pages missing on job")
    pages = pages_payload["pages"]

    append_event(
        job_dir,
        "template_run_start",
        f"Building from template {inputs.get('from_template_id')} "
        f"(content locked; new hero {inputs.get('name')}).",
    )
    _update_job(
        job_dir,
        status="starting",
        error=None,
        book_title=story.get("title"),
        total_pages=ART_PAGE_COUNT,
        validation_passed=True,
    )
    client = create_client()
    try:
        reference_path = next((job_dir / "character").glob("reference.*"), None)
        if reference_path is None:
            raise FileNotFoundError("Reference image missing")

        _update_job(job_dir, status="generating_character")
        # Always regenerate the character sheet for the new child.
        character_path = generate_character(client, job_dir, reference_path, inputs)
        # Templates never carry sheets over: set and anchor sheets are regenerated too.
        generate_set_sheets(client, job_dir, inputs)
        generate_anchor_sheet(client, job_dir, inputs)

        # Force fresh cover/spread art for the new face (ignore any leftover art).
        for leftover in (
            job_dir / "cover" / "front_art.png",
            job_dir / "cover" / "back_art.png",
            job_dir / "title_page" / "spot.png",
        ):
            if leftover.is_file():
                leftover.unlink()
        for n in range(1, SPREAD_COUNT + 1):
            for side in ("left", "right"):
                art = spread_art_path(job_dir, n, side)
                if art.is_file():
                    art.unlink()
        pdf_path = _illustrate_and_build(
            client, job_dir, inputs, story, pages, character_path
        )
        append_event(job_dir, "job_complete", f"Template book ready: {pdf_path.name}")

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
        logger.exception("Job %s template run failed", job_id)
        _update_job(job_dir, status="failed", error=str(error))
        append_event(job_dir, "job_failed", str(error))
        raise


def run_job(job_id: str, job_dir: Path, mode: str | None = None):
    """
    mode:
      - prepare: stop at content_ready
      - preview: first 2 spreads (4 page images), stop at preview_ready
      - validate_and_build / full: validate + cover + all pages + PDF
      - from_template: reuse locked content, new face/name art + PDF
      - None: read start_mode from job/inputs (default full)
    """
    job = read_json(job_dir / "job.json") or {}
    inputs = read_json(job_dir / "inputs.json") or {}
    resolved = mode or job.get("run_mode") or inputs.get("start_mode") or "full"

    if resolved in {"prepare", "content"}:
        return run_prepare(job_id, job_dir)
    if resolved in {"preview", "preview_first"}:
        return run_preview(job_id, job_dir)
    if resolved in {"from_template", "template"}:
        return run_from_template(job_id, job_dir)
    return run_validate_and_build(job_id, job_dir)
