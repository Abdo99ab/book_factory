import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from pipeline.book_templates import (
    STANDARD_SLOTS,
    delete_template,
    get_template,
    list_templates,
    materialize_template_into_job,
    missing_slots,
    save_job_as_template,
)
from pipeline.cast_rebuild import rebuild_content_for_cast
from pipeline.changes import enqueue_change, process_change_queue
from pipeline.catalogue import get_title, list_titles, resolve_route
from pipeline.constants import (
    ARCHIVE_ROOT,
    CONTENT_PAGE_COUNT,
    GUTTER_BAND_MM,
    JOB_ROOT,
    PPI_FAIL,
    PPI_WARN,
    SPREAD_COUNT,
    SPREAD_WIDTH_MM,
    TEMPLATE_ROOT,
    pronouns_for,
)
from pipeline.inputs_util import age_band_warning, collect_slots, validate_order_id
from pipeline.content_edit import apply_content_edits
from pipeline.fonts import normalize_font_id
from pipeline.job_log import append_event, read_events, read_json, write_json
from pipeline.memory import format_memory_for_prompt, load_memory
from pipeline.openai_util import create_client
from pipeline.build_info import get_build_info
from pipeline.content_edit import edit_spread, insert_last_line
from pipeline.pages import (
    ensure_spread_halves,
    page_spread_side,
    spread_art_path,
    spread_page_path,
    spread_pages,
    spread_text,
)
from pipeline.pdf_build import build_pdf
from pipeline import qa as qa_checks
from pipeline.runner import (
    PREVIEW_PAGE_COUNT,
    PREVIEW_SPREAD_COUNT,
    count_completed_spreads,
    run_job,
    run_regenerate_spread,
)
from pipeline.text_guards import last_line_message, last_line_present
from pipeline.self_revise import apply_self_revise_plan, parse_actions_from_form, run_self_revise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFERENCE_BYTES = 15 * 1024 * 1024
job_executor = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("JOB_CONCURRENCY", "1")))
)
change_lock = threading.Lock()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "local-development-key")
    app.config["MAX_CONTENT_LENGTH"] = MAX_REFERENCE_BYTES
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    TEMPLATE_ROOT.mkdir(parents=True, exist_ok=True)

    build_info = get_build_info(BASE_DIR)
    app.config["BUILD_INFO"] = build_info

    @app.context_processor
    def inject_build_info():
        return {"build_info": build_info}

    @app.get("/")
    def index():
        jobs = _list_jobs(JOB_ROOT)
        memory = load_memory()
        return render_template(
            "index.html",
            jobs=jobs,
            titles=list_titles(),
            preference_count=len(memory.get("preferences") or []),
            archived_count=_count_jobs(ARCHIVE_ROOT),
            template_count=len(list_templates(limit=500)),
        )

    @app.get("/archived")
    def archived_jobs():
        jobs = _list_jobs(ARCHIVE_ROOT)
        return render_template("archived.html", jobs=jobs)

    @app.get("/create")
    def create_form():
        return render_template(
            "create.html",
            titles=list_titles(),
        )

    @app.post("/jobs")
    def create_job():
        # Legacy Magical fields — ignored if posted
        _ = request.form.get("theme", "")
        _ = request.form.get("purpose", "")
        _ = request.form.get("page_count", "")
        _ = request.form.get("design_rules", "")
        _ = request.form.get("draft_story", "")
        _ = request.form.get("font_id", "")

        title_id = request.form.get("title_id", "").strip()
        name = request.form.get("name", "").strip()
        age = request.form.get("age", "").strip()
        pronouns = request.form.get("pronouns", "").strip().lower()
        carer_name = request.form.get("carer_name", "").strip()
        carer_role = request.form.get("carer_role", "").strip()
        home_type = request.form.get("home_type", "house").strip().lower()
        comfort_object = request.form.get("comfort_object", "").strip()
        dedication_text = request.form.get("dedication_text", "").strip()
        order_id = request.form.get("order_id", "").strip()
        start_mode = request.form.get("start_mode", "preview").strip().lower()
        story_override = request.form.get("story_override", "").strip()
        art_notes = request.form.get("art_notes", "").strip()
        reference = request.files.get("reference_image")
        if start_mode not in {"preview", "full"}:
            start_mode = "preview"

        if not title_id:
            flash("Choose a catalogue title.", "error")
            return redirect(url_for("create_form"))
        try:
            title = get_title(title_id)
        except Exception:
            flash("Choose a valid catalogue title.", "error")
            return redirect(url_for("create_form"))
        if not name or not age:
            flash("Name and age are required.", "error")
            return redirect(url_for("create_form"))
        if pronouns not in {"she", "he", "they"}:
            flash("Choose pronouns.", "error")
            return redirect(url_for("create_form"))
        if not carer_name or not carer_role:
            flash("Carer name and role are required.", "error")
            return redirect(url_for("create_form"))
        if home_type not in {"flat", "house"}:
            home_type = "house"
        if not comfort_object:
            flash("Comfort object is required.", "error")
            return redirect(url_for("create_form"))
        if not validate_order_id(order_id):
            flash("Order ID is required (letters, numbers and hyphens only).", "error")
            return redirect(url_for("create_form"))
        if not reference or not reference.filename:
            flash("Upload a child reference photo.", "error")
            return redirect(url_for("create_form"))
        if not os.getenv("_OPENAI_API_KEY"):
            flash("_OPENAI_API_KEY is not configured.", "error")
            return redirect(url_for("create_form"))

        try:
            slots = collect_slots(request.form, title_id)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("create_form"))

        extension = Path(secure_filename(reference.filename)).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            flash("Reference image must be PNG, JPG, JPEG, or WEBP.", "error")
            return redirect(url_for("create_form"))

        route = resolve_route(title)
        age_band = title["age_band"]
        age_warning = age_band_warning(age, age_band)
        pronouns_for(pronouns)
        gender = {"she": "female", "he": "male", "they": "they"}.get(pronouns, pronouns)

        job_id = uuid.uuid4().hex
        job_dir = JOB_ROOT / job_id
        character_dir = job_dir / "character"
        character_dir.mkdir(parents=True, exist_ok=True)
        reference_path = character_dir / f"reference{extension}"
        # PARKED: data protection (spec §14) — consent, retention, redaction
        reference.save(reference_path)

        inputs = {
            "title_id": title_id,
            "route": route,
            "age_band": age_band,
            "name": name,
            "age": age,
            "pronouns": pronouns,
            "gender": gender,
            "carer": {"name": carer_name, "role": carer_role},
            "home_type": home_type,
            "comfort_object": comfort_object,
            "slots": slots,
            "dedication_text": dedication_text,
            "order_id": order_id,
            "start_mode": start_mode,
            "font_id": normalize_font_id(None),
            "page_count": CONTENT_PAGE_COUNT,
        }
        if story_override:
            inputs["story_override"] = story_override
        if art_notes:
            inputs["art_notes"] = art_notes

        write_json(job_dir / "inputs.json", inputs)
        book_title = title["title_template"].replace("{name}", name)
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": "queued",
                "run_mode": "prepare",
                "preferred_next": "preview" if start_mode == "preview" else "full",
                "book_title": book_title,
                "name": name,
                "title_id": title_id,
                "band": title.get("band"),
                "order_id": order_id,
                "age_band": age_band,
                "age_warning": age_warning,
                "page_count": CONTENT_PAGE_COUNT,
                "spreads_done": 0,
                "spreads_total": SPREAD_COUNT,
                "preview_requested": False,
                "preview_ready": False,
                "pdf": None,
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            },
        )
        append_event(
            job_dir,
            "job_created",
            "Job created. Preparing character sheets for hero approval.",
            inputs=inputs,
            preferred_next="preview" if start_mode == "preview" else "full",
            age_warning=age_warning,
        )

        job_executor.submit(_safe_run_job, job_id, job_dir, "prepare")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/approve-character")
    def approve_character(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if job.get("status") != "character_ready":
            flash("Hero sheet approval is only available when sheets are ready.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        approver = (request.form.get("approved_by") or "operator").strip() or "operator"
        write_json(
            job_dir / "job.json",
            {
                **job,
                "character_approved_at": time.time(),
                "character_approved_by": approver,
                "status": "queued",
                "error": None,
            },
        )
        append_event(
            job_dir,
            "character_approved",
            f"Hero sheet approved by {approver}. Continuing to story.",
            approved_by=approver,
        )
        job_executor.submit(_safe_run_job, job_id, job_dir, "prepare")
        flash("Hero sheet approved — generating story and page map.", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/jobs/<job_id>/character/sheet")
    def character_sheet_image(job_id):
        path = _job_dir(job_id) / "character" / "character.png"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.get("/jobs/<job_id>/anchor/sheet")
    def anchor_sheet_image(job_id):
        path = _job_dir(job_id) / "anchor" / "anchor.png"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.post("/jobs/<job_id>/retry")
    def retry_job(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if job.get("status") not in {"failed", "validation_failed"}:
            flash("Only failed or validation-failed jobs can be retried.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if not (job_dir / "inputs.json").is_file():
            flash("Job inputs are missing; create a new book instead.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        # Pick up a corrected .env without restarting the server.
        load_dotenv(BASE_DIR / ".env", override=True)
        if not os.getenv("_OPENAI_API_KEY") or os.getenv("_OPENAI_API_KEY", "").startswith(
            "replace-"
        ):
            flash("Set a real _OPENAI_API_KEY in .env before retrying.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        # Force validation to run again on content retries.
        validation_path = job_dir / "story" / "validation_report.json"
        if job.get("status") == "validation_failed" and validation_path.is_file():
            validation_path.unlink()

        # Retry continues the intended path; failed preview retries preview.
        if job.get("preview_ready") or job.get("status") == "validation_failed":
            retry_mode = "full"
        elif job.get("preview_requested") and not job.get("preview_ready"):
            retry_mode = "preview"
        else:
            retry_mode = job.get("run_mode") or "full"

        write_json(
            job_dir / "job.json",
            {
                **job,
                "status": "queued",
                "run_mode": retry_mode,
                "error": None,
                "pdf": None,
                "validation_passed": None,
                "updated_at": time.time(),
            },
        )
        append_event(
            job_dir,
            "job_retry",
            f"Retry requested ({retry_mode}). Existing page art is kept.",
        )
        job_executor.submit(_safe_run_job, job_id, job_dir, retry_mode)
        flash("Retry queued. Progress continues from what already finished.", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/content")
    def save_content(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if job.get("status") not in {
            "content_ready",
            "preview_ready",
            "validation_failed",
            "failed",
        }:
            flash("Content can be edited after preparation, before or after preview.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        try:
            result = apply_content_edits(job_dir, request.form)
        except (FileNotFoundError, ValueError) as error:
            flash(str(error), "error")
            return redirect(url_for("job_page", job_id=job_id))

        if result.get("needs_rebuild"):
            load_dotenv(BASE_DIR / ".env", override=True)
            job_executor.submit(
                _safe_rebuild_cast,
                job_id,
                job_dir,
                result.get("approved_characters") or [],
                result.get("removed_characters") or [],
                result.get("preserve") or {},
            )
            removed = result.get("removed_characters") or []
            added = result.get("added_characters") or []
            flash(
                "Cast changed — rebuilding the full story and all page briefs to match. "
                f"Removed: {', '.join(removed) or 'none'}. "
                f"Added: {', '.join(added) or 'none'}. "
                "Wait for Content Ready, review the new text, then Preview again.",
                "success",
            )
            return redirect(url_for("job_page", job_id=job_id))

        job = read_json(job_dir / "job.json") or {}
        if job.get("status") not in {"content_ready", "preview_ready", "validation_failed"}:
            job["status"] = "content_ready"
            write_json(job_dir / "job.json", job)

        invalidated = result.get("invalidated_spreads") or []
        parts = ["Content saved."]
        if invalidated:
            parts.append(
                f"Cleared art for spreads {', '.join(map(str, invalidated))} — run Preview again."
            )
        else:
            parts.append("Review content, then Preview or Validate & build.")
        flash(" ".join(parts), "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/preview")
    def preview_job(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if job.get("status") not in {"content_ready", "failed", "validation_failed"}:
            flash("Preview is available after content is ready, or from a stopped job.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if job.get("preview_ready") and job.get("status") != "failed":
            flash("Preview spreads already exist. Use Validate & build to continue.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        load_dotenv(BASE_DIR / ".env", override=True)
        write_json(
            job_dir / "job.json",
            {
                **job,
                "status": "queued",
                "run_mode": "preview",
                "preview_requested": True,
                "error": None,
                "updated_at": time.time(),
            },
        )
        append_event(
            job_dir,
            "preview_requested",
            f"Operator requested the first {PREVIEW_SPREAD_COUNT} spreads (pages 2\u2013{PREVIEW_PAGE_COUNT + 1}, no cover).",
        )
        job_executor.submit(_safe_run_job, job_id, job_dir, "preview")
        flash(
            f"Preview started: first {PREVIEW_SPREAD_COUNT} spreads (pages 2\u2013{PREVIEW_PAGE_COUNT + 1}, not the cover).",
            "success",
        )
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/validate-build")
    def validate_and_build_job(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if job.get("status") not in {
            "content_ready",
            "preview_ready",
            "failed",
            "validation_failed",
        }:
            flash("Validate & build is available after content or preview is ready.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        load_dotenv(BASE_DIR / ".env", override=True)
        validation_path = job_dir / "story" / "validation_report.json"
        if job.get("status") == "validation_failed" and validation_path.is_file():
            validation_path.unlink()

        write_json(
            job_dir / "job.json",
            {
                **job,
                "status": "queued",
                "run_mode": "full",
                "validation_passed": None,
                "error": None,
                "pdf": None,
                "updated_at": time.time(),
            },
        )
        append_event(
            job_dir,
            "validate_build_requested",
            "Operator chose validate & build. Existing preview spreads will not be regenerated.",
        )
        job_executor.submit(_safe_run_job, job_id, job_dir, "full")
        flash(
            "Validate & build started. Already generated spreads are kept.",
            "success",
        )
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/jobs/<job_id>")
    def job_page(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        inputs = read_json(job_dir / "inputs.json", default={})
        story = read_json(job_dir / "story" / "rebuilt_story.json")
        pages_payload = read_json(job_dir / "story" / "pages.json")
        pages = (pages_payload or {}).get("pages") or []
        events = list(reversed(read_events(job_dir, limit=80)))
        spread_cards = _spread_cards(job_id, job_dir, story, pages)
        last_line_problem = _last_line_problem(inputs, story)
        cover_front = job_dir / "cover" / "front_art.png"
        cover_print = job_dir / "print" / "cover.jpg"
        cover_preview = job_dir / "cover" / "front_preview.png"
        cover_layout = (read_json(job_dir / "cover" / "cover.json") or {}).get("layout") or {}
        validation = read_json(job_dir / "story" / "validation_report.json")
        change_queue = job.get("change_queue") or []
        queued_changes = [
            item for item in change_queue if item.get("status") in {"queued", "running"}
        ]
        pages_ready = (job_dir / "story" / "pages.json").is_file()
        content_editable = job.get("status") in {
            "content_ready",
            "preview_ready",
            "validation_failed",
            "failed",
        } and pages_ready and story is not None
        anchor_sheet_exists = (_job_dir(job_id) / "anchor" / "anchor.png").is_file()
        return render_template(
            "job.html",
            job=job,
            inputs=inputs,
            story=story,
            pages=pages,
            spreads=spread_cards,
            last_line_problem=last_line_problem,
            read_aloud_spreads=READ_ALOUD_SPREADS,
            gutter_mm=GUTTER_BAND_MM,
            spread_width_mm=SPREAD_WIDTH_MM,
            events=events,
            validation=validation,
            change_progress=job.get("change_progress"),
            queued_changes=queued_changes,
            cover_front_exists=cover_front.is_file(),
            cover_print_exists=cover_print.is_file(),
            cover_front_url=url_for("cover_front", job_id=job_id),
            cover_print_url=url_for("cover_print", job_id=job_id),
            cover_back_exists=(job_dir / "cover" / "back_art.png").is_file(),
            cover_back_url=url_for("cover_back", job_id=job_id),
            spread_edit_enabled=job.get("status") in SPREAD_EDITABLE_STATUSES,
            cover_preview_exists=cover_preview.is_file(),
            cover_preview_url=url_for("cover_front_preview", job_id=job_id),
            cover_layout_failures=cover_layout.get("failures") or [],
            preview_page_count=PREVIEW_PAGE_COUNT,
            preview_spread_count=PREVIEW_SPREAD_COUNT,
            spreads_done=count_completed_spreads(job_dir),
            spreads_total=SPREAD_COUNT,
            content_editable=content_editable and job.get("status") != "rebuilding_content",
            preferred_next=job.get("preferred_next") or inputs.get("start_mode") or "preview",
            # Preview only when content exists and we have not already previewed
            # (or failed before finishing preview). Never regenerates existing art.
            preview_available=(
                job.get("status") == "content_ready"
                or (
                    job.get("status") in {"failed", "validation_failed"}
                    and pages_ready
                    and not job.get("preview_ready")
                )
            ),
            validate_build_available=job.get("status")
            in {"content_ready", "preview_ready", "failed", "validation_failed"},
            retry_available=job.get("status") in {"failed", "validation_failed", "rebuilding_content"},
            change_enabled=job.get("status")
            in {
                "complete",
                "failed",
                "validation_failed",
                "applying_changes",
                "self_revise_ready",
            },
            overlay_rebuild_available=job.get("status")
            in {"complete", "preview_ready", "failed", "self_revise_ready"},
            self_revise_available=job.get("status")
            in {"complete", "failed", "self_revise_ready"}
            and (
                bool(job.get("pdf"))
                or (job_dir / "print" / "pages").exists()
            ),
            self_revise_plan_ready=job.get("status") == "self_revise_ready"
            or (
                (job.get("self_revise") or {}).get("phase") == "awaiting_approval"
            ),
            self_revise=job.get("self_revise"),
            template_available=(
                bool(job.get("pdf"))
                or job.get("status") in {"complete", "self_revise_ready"}
            )
            and (job_dir / "story" / "pages.json").is_file(),
            existing_template_id=job.get("template_id"),
            anchor_sheet_exists=anchor_sheet_exists,
            qa_report=qa_checks.load_qa_report(job_dir),
            human_check=qa_checks.load_human_check(job_dir),
            human_check_items=qa_checks.HUMAN_CHECK_ITEMS,
            send_blockers=qa_checks.send_blockers(job_dir) if job.get("pdf") else [],
            min_effective_ppi=job.get("min_effective_ppi"),
            ppi_warn=PPI_WARN,
            ppi_fail=PPI_FAIL,
        )

    @app.post("/jobs/<job_id>/qa")
    def run_job_qa(job_id):
        job_dir = _job_dir(job_id)
        if not read_json(job_dir / "job.json"):
            abort(404)
        report = qa_checks.run_qa(job_dir)
        flash(
            "QA passed." if report["passed"] else f"QA found {report['errors']} error(s).",
            "success" if report["passed"] else "error",
        )
        return redirect(url_for("job_page", job_id=job_id) + "#human-check")

    @app.post("/jobs/<job_id>/human-check")
    def save_human_check(job_id):
        job_dir = _job_dir(job_id)
        if not read_json(job_dir / "job.json"):
            abort(404)
        record = qa_checks.record_human_check(
            job_dir,
            request.form.get("reviewer", ""),
            request.form.getlist("checked"),
        )
        flash(
            "Human check complete." if record["complete"] else "Saved - every item and a reviewer name are still required.",
            "success" if record["complete"] else "error",
        )
        return redirect(url_for("job_page", job_id=job_id) + "#human-check")

    @app.post("/jobs/<job_id>/send-preview")
    def send_preview_job(job_id):
        job_dir = _job_dir(job_id)
        if not read_json(job_dir / "job.json"):
            abort(404)
        try:
            qa_checks.send_preview(job_dir)
        except PermissionError as error:
            flash(f"Send blocked: {error}", "error")
            return redirect(url_for("job_page", job_id=job_id) + "#human-check"), 303
        flash("Preview released.", "success")
        return redirect(url_for("job_page", job_id=job_id) + "#human-check")

    @app.post("/jobs/<job_id>/make-template")
    def make_template(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if _job_is_busy(job):
            flash("Wait until the job finishes before saving a template.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if not (
            job.get("pdf")
            or job.get("status") in {"complete", "self_revise_ready"}
        ):
            flash("Only a finished book can be saved as a template.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if job.get("template_id") and get_template(job["template_id"]):
            flash("This job is already saved as a template.", "error")
            return redirect(url_for("template_list"))
        label = (request.form.get("label") or "").strip() or None
        try:
            meta = save_job_as_template(job_dir, label=label)
        except Exception as error:
            flash(f"Could not save template: {error}", "error")
            return redirect(url_for("job_page", job_id=job_id))
        flash(f"Template saved: {meta['label']}. Reuse it with a new child photo + name.", "success")
        return redirect(url_for("template_list"))

    @app.get("/templates")
    def template_list():
        return render_template(
            "templates.html",
            templates=list_templates(),
        )

    @app.get("/templates/<template_id>/reuse")
    def reuse_template_form(template_id):
        meta = get_template(template_id)
        if not meta:
            abort(404)
        return render_template(
            "reuse_template.html",
            template=meta,
            standard_slots=STANDARD_SLOTS,
            title_slots=[s for s in meta.get("slots") or [] if s["id"] not in {x["id"] for x in STANDARD_SLOTS}],
        )

    @app.post("/templates/<template_id>/reuse")
    def reuse_template(template_id):
        meta = get_template(template_id)
        if not meta:
            abort(404)
        back = redirect(url_for("reuse_template_form", template_id=template_id))

        # Every slot is re-asked: nothing from the source child is carried over.
        values = {s["id"]: (request.form.get(f"slot_{s['id']}") or "").strip() for s in meta.get("slots") or []}
        reference = request.files.get("reference_image")
        missing = missing_slots(meta["title_id"], values)
        if missing:
            flash("Please fill: " + ", ".join(missing), "error")
            return back
        if not validate_order_id(values.get("order_id", "")):
            flash("Order ID is required (letters, numbers and hyphens only).", "error")
            return back
        if not reference or not reference.filename:
            flash("Upload the new child reference photo.", "error")
            return back
        if not os.getenv("_OPENAI_API_KEY"):
            flash("_OPENAI_API_KEY is not configured.", "error")
            return back
        extension = Path(secure_filename(reference.filename)).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            flash("Reference image must be PNG, JPG, JPEG, or WEBP.", "error")
            return back

        job_id = uuid.uuid4().hex
        job_dir = JOB_ROOT / job_id
        try:
            packed = materialize_template_into_job(template_id, job_dir, values=values)
        except Exception as error:
            shutil.rmtree(job_dir, ignore_errors=True)
            flash(f"Could not start from template: {error}", "error")
            return back
        character_dir = job_dir / "character"
        character_dir.mkdir(parents=True, exist_ok=True)
        # PARKED: data protection (spec §14) - consent, retention, redaction
        reference.save(character_dir / f"reference{extension}")

        inputs = packed["inputs"]
        story = packed["story"]
        pages = packed["pages"]
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": "queued",
                "run_mode": "from_template",
                "from_template_id": template_id,
                "book_title": story.get("title"),
                "name": inputs["name"],
                "title_id": inputs["title_id"],
                "band": meta.get("band"),
                "order_id": inputs["order_id"],
                "age_band": inputs["age_band"],
                "page_count": CONTENT_PAGE_COUNT,
                "spreads_done": 0,
                "spreads_total": SPREAD_COUNT,
                "preview_requested": False,
                "preview_ready": False,
                "validation_passed": True,
                "pdf": None,
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            },
        )
        append_event(
            job_dir,
            "job_created_from_template",
            f"Created from template {template_id}; text locked, regenerating sheets, art and PDF for {inputs['name']}.",
            template_id=template_id,
            inputs=inputs,
        )
        job_executor.submit(_safe_run_job, job_id, job_dir, "from_template")
        flash(f"Building \u201c{story.get('title')}\u201d for {inputs['name']} from template.", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/templates/<template_id>/delete")
    def remove_template(template_id):
        try:
            delete_template(template_id)
        except FileNotFoundError:
            abort(404)
        flash("Template deleted.", "success")
        return redirect(url_for("template_list"))

    @app.post("/jobs/<job_id>/self-revise")
    def self_revise_job(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        inputs = read_json(job_dir / "inputs.json") or {}
        if not job:
            abort(404)
        if job.get("status") not in {"complete", "failed", "self_revise_ready"}:
            flash("Self-revise is available after the book has finished generating.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if not job.get("pdf") and not (job_dir / "print" / "pages").exists():
            flash("Composited pages are not ready yet.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if _job_is_busy(job):
            flash("Wait until the current run finishes, then try self-revise.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        job_executor.submit(_safe_self_revise, job_id, job_dir, inputs)
        flash(
            "Self-revise started — AI will propose a plan. You can edit it, then click Apply.",
            "success",
        )
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/self-revise/apply")
    def apply_self_revise_job(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        inputs = read_json(job_dir / "inputs.json") or {}
        if not job:
            abort(404)
        revise = job.get("self_revise") or {}
        if job.get("status") not in {"self_revise_ready", "complete"} and revise.get(
            "phase"
        ) != "awaiting_approval":
            flash("No self-revise plan is waiting for approval.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if _job_is_busy(job):
            flash("Wait until the current run finishes.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        actions = parse_actions_from_form(request.form)
        summary = (request.form.get("plan_summary") or "").strip() or None
        job_executor.submit(
            _safe_apply_self_revise, job_id, job_dir, inputs, actions, summary
        )
        flash(
            f"Applying {len(actions)} approved self-revise action(s)…"
            if actions
            else "Dismissing plan with no actions…",
            "success",
        )
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        # Live count from disk: the job file lags while spreads are being drawn in parallel.
        return {**job, **_spread_progress(job_dir)}

    @app.get("/api/jobs/<job_id>/events")
    def job_events(job_id):
        job_dir = _job_dir(job_id)
        if not (job_dir / "job.json").is_file():
            abort(404)
        return jsonify(read_events(job_dir, limit=200))

    @app.post("/jobs/<job_id>/changes")
    def job_changes(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        demand = request.form.get("demand", "").strip()
        if not demand:
            flash("Describe the change you want.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if job.get("status") not in {
            "complete",
            "failed",
            "validation_failed",
            "applying_changes",
        }:
            flash("Wait until generation finishes before requesting changes.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        if not (job_dir / "story" / "pages.json").is_file():
            flash("Pages are not ready yet; finish or retry generation first.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        enqueue_change(job_dir, demand)
        job_executor.submit(_safe_process_change_queue, job_id, job_dir)
        queue = read_json(job_dir / "job.json").get("change_queue") or []
        pending = sum(1 for entry in queue if entry.get("status") in {"queued", "running"})
        if pending > 1:
            flash(
                f"Change queued (#{pending} in line). Progress updates live below.",
                "success",
            )
        else:
            flash("Change started. Progress updates live below.", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/jobs/<job_id>/spreads/<int:n>/art")
    def spread_art(job_id, n):
        if not 1 <= n <= SPREAD_COUNT:
            abort(404)
        path = spread_art_path(_job_dir(job_id), n)
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.get("/jobs/<job_id>/spreads/<int:n>/composed")
    def spread_composed(job_id, n):
        """Composed spread (art + live text), stitched from the PDF page previews; raw art until a PDF exists."""
        if not 1 <= n <= SPREAD_COUNT:
            abort(404)
        job_dir = _job_dir(job_id)
        path = job_dir / "print" / "spreads" / f"spread-{n:02d}.jpg"
        if not path.is_file():
            from pipeline.pdf_build import stitch_spread_previews

            stitch_spread_previews(job_dir)
        if path.is_file():
            return send_file(path, mimetype="image/jpeg")
        art = spread_art_path(job_dir, n)
        if art.is_file():
            return send_file(art, mimetype="image/png")
        abort(404)

    @app.get("/jobs/<job_id>/spreads/<int:n>/page/<side>")
    def spread_half_image(job_id, n, side):
        """One derived half (page-left.png / page-right.png), cut from art.png on first use."""
        if not 1 <= n <= SPREAD_COUNT or side not in {"left", "right"}:
            abort(404)
        job_dir = _job_dir(job_id)
        if not ensure_spread_halves(job_dir, n):
            abort(404)
        return send_file(spread_page_path(job_dir, n, side), mimetype="image/png")

    @app.post("/jobs/<job_id>/spreads/<int:n>/regenerate")
    def spread_regenerate(job_id, n):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job or not 1 <= n <= SPREAD_COUNT:
            abort(404)
        if job.get("status") not in SPREAD_EDITABLE_STATUSES:
            flash("Wait until the current run finishes before regenerating a spread.", "error")
            return redirect(url_for("job_page", job_id=job_id) + f"#spread-{n}")
        if not (job_dir / "story" / "pages.json").is_file():
            flash("Pages are not ready yet.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        load_dotenv(BASE_DIR / ".env", override=True)
        instruction = (request.form.get("edit_instruction") or "").strip() or None
        write_json(
            job_dir / "job.json",
            {**job, "status": "regenerating_spread", "error": None, "updated_at": time.time()},
        )
        job_executor.submit(_safe_regenerate_spread, job_id, job_dir, n, instruction)
        flash(f"Regenerating spread {n} (pages {2 * n}\u2013{2 * n + 1}).", "success")
        return redirect(url_for("job_page", job_id=job_id) + f"#spread-{n}")

    @app.post("/jobs/<job_id>/spreads/<int:n>/edit")
    def spread_edit(job_id, n):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job or not 1 <= n <= SPREAD_COUNT:
            abort(404)
        if job.get("status") not in SPREAD_EDITABLE_STATUSES:
            flash("Spreads can be edited once the content is ready and nothing is running.", "error")
            return redirect(url_for("job_page", job_id=job_id) + f"#spread-{n}")
        try:
            result = edit_spread(
                job_dir,
                n,
                text=request.form.get("text"),
                scene=request.form.get("scene"),
                calm_zone=request.form.get("calm_zone"),
            )
        except (FileNotFoundError, ValueError) as error:
            flash(str(error), "error")
            return redirect(url_for("job_page", job_id=job_id) + f"#spread-{n}")
        job = read_json(job_dir / "job.json") or {}
        job["validation_passed"] = None
        if result.get("picture_changed") and job.get("preview_ready"):
            job["preview_ready"] = False
            if job.get("status") == "preview_ready":
                job["status"] = "content_ready"
        job.update(_spread_progress(job_dir))
        write_json(job_dir / "job.json", job)
        flash(
            f"Spread {n} saved. Its picture was cleared: regenerate it or run Preview again."
            if result.get("picture_changed")
            else f"Spread {n} saved.",
            "success",
        )
        return redirect(url_for("job_page", job_id=job_id) + f"#spread-{n}")

    @app.post("/jobs/<job_id>/last-line")
    def spread_insert_last_line(job_id):
        job_dir = _job_dir(job_id)
        if not read_json(job_dir / "job.json"):
            abort(404)
        try:
            result = insert_last_line(job_dir)
        except (FileNotFoundError, ValueError) as error:
            flash(str(error), "error")
            return redirect(url_for("job_page", job_id=job_id) + "#spread-14")
        flash(
            "Last line set on spread 14." if result["changed"] else "Spread 14 already ends with the last line.",
            "success",
        )
        return redirect(url_for("job_page", job_id=job_id) + "#spread-14")

    # Deprecated: per-page image routes. A page is half of a spread, so they redirect to the spread.
    @app.get("/jobs/<job_id>/pages/<int:page_number>/art")
    def page_art(job_id, page_number):
        located = page_spread_side(page_number)
        if located is None:
            abort(404)
        return redirect(url_for("spread_art", job_id=job_id, n=located[0]), code=302)

    @app.get("/jobs/<job_id>/pages/<int:page_number>/composed")
    def page_composed(job_id, page_number):
        located = page_spread_side(page_number)
        if located is None:
            abort(404)
        return redirect(url_for("spread_composed", job_id=job_id, n=located[0]), code=302)

    @app.get("/jobs/<job_id>/cover/front")
    def cover_front(job_id):
        path = _job_dir(job_id) / "cover" / "front_art.png"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.get("/jobs/<job_id>/cover/back")
    def cover_back(job_id):
        path = _job_dir(job_id) / "cover" / "back_art.png"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.post("/jobs/<job_id>/cover/regenerate")
    def cover_regenerate(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if job.get("status") not in SPREAD_EDITABLE_STATUSES:
            flash("Wait until the current run finishes before regenerating the cover.", "error")
            return redirect(url_for("job_page", job_id=job_id) + "#cover-card")
        load_dotenv(BASE_DIR / ".env", override=True)
        write_json(
            job_dir / "job.json",
            {**job, "status": "generating_cover", "error": None, "updated_at": time.time()},
        )
        job_executor.submit(_safe_regenerate_cover, job_id, job_dir, job.get("status"))
        flash("Regenerating the front and back cover art.", "success")
        return redirect(url_for("job_page", job_id=job_id) + "#cover-card")

    @app.get("/jobs/<job_id>/cover/front-preview")
    def cover_front_preview(job_id):
        path = _job_dir(job_id) / "cover" / "front_preview.png"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.get("/jobs/<job_id>/cover/print")
    def cover_print(job_id):
        path = _job_dir(job_id) / "print" / "cover.jpg"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/jpeg")

    @app.post("/jobs/<job_id>/rebuild-overlays")
    def rebuild_overlays(job_id):
        """Re-render text overlays on existing art (no new AI images)."""
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        inputs = read_json(job_dir / "inputs.json") or {}
        story = read_json(job_dir / "story" / "rebuilt_story.json")
        pages_payload = read_json(job_dir / "story" / "pages.json")
        if not job or not story or not pages_payload:
            abort(404)
        if job.get("status") not in {"complete", "preview_ready", "failed"}:
            flash("Wait until pages exist, then rebuild overlays.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        font_id = normalize_font_id(None)
        inputs["font_id"] = font_id
        write_json(job_dir / "inputs.json", inputs)

        pages = pages_payload.get("pages") or []
        if not count_completed_spreads(job_dir):
            flash("No spread art yet - finish preview or build first.", "error")
            return redirect(url_for("job_page", job_id=job_id))

        try:
            if not (
                (job_dir / "cover" / "front_art.png").is_file()
                and (job_dir / "cover" / "back_art.png").is_file()
            ):
                flash("Cover art is missing - generate the full build first.", "error")
                return redirect(url_for("job_page", job_id=job_id))
            pdf_path = build_pdf(job_dir, story, pages, inputs)
            job["pdf"] = pdf_path.relative_to(job_dir).as_posix()
            job["status"] = "complete"
            write_json(job_dir / "job.json", job)
            append_event(
                job_dir,
                "overlays_rebuilt",
                f"Text layer and PDF rebuilt with font {font_id} (no new AI images).",
                font_id=font_id,
            )
            flash("Text layer re-rendered with Raised Ready brand fonts.", "success")
        except Exception as error:
            logger.exception("Overlay rebuild failed")
            flash(f"Overlay rebuild failed: {error}", "error")
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/jobs/<job_id>/download")
    def download_job(job_id):
        job = read_json(_job_dir(job_id) / "job.json")
        if not job or not job.get("pdf"):
            abort(404)
        path = _job_dir(job_id) / job["pdf"]
        if not path.is_file():
            abort(404)
        filename = secure_filename(f"{job.get('name', 'book')}-{job_id[:8]}.pdf")
        return send_file(path, as_attachment=True, download_name=filename)

    @app.post("/jobs/<job_id>/archive")
    def archive_job(job_id):
        # PARKED: data protection (spec §14) - retention/redaction on archive (not implemented in this pass)
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json")
        if not job:
            abort(404)
        if _job_is_busy(job):
            flash("Wait until generation/changes finish before archiving.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        try:
            ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
            dest = ARCHIVE_ROOT / job_id
            if dest.exists():
                flash("An archived copy of this job already exists.", "error")
                return redirect(url_for("job_page", job_id=job_id))
            job["archived_at"] = time.time()
            write_json(job_dir / "job.json", job)
            append_event(job_dir, "job_archived", "Job moved to archive.")
            shutil.move(str(job_dir), str(dest))
            flash("Job archived.", "success")
            return redirect(url_for("archived_jobs"))
        except Exception as error:
            logger.exception("Archive failed for %s", job_id)
            flash(f"Archive failed: {error}", "error")
            return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/unarchive")
    def unarchive_job(job_id):
        archived_dir = _archived_job_dir(job_id)
        job = read_json(archived_dir / "job.json")
        if not job:
            abort(404)
        try:
            dest = JOB_ROOT / job_id
            if dest.exists():
                flash("An active job with this id already exists.", "error")
                return redirect(url_for("archived_jobs"))
            job.pop("archived_at", None)
            write_json(archived_dir / "job.json", job)
            append_event(archived_dir, "job_unarchived", "Job restored from archive.")
            shutil.move(str(archived_dir), str(dest))
            flash("Job restored to active jobs.", "success")
            return redirect(url_for("job_page", job_id=job_id))
        except Exception as error:
            logger.exception("Unarchive failed for %s", job_id)
            flash(f"Restore failed: {error}", "error")
            return redirect(url_for("archived_jobs"))

    @app.post("/jobs/<job_id>/delete")
    def delete_job(job_id):
        # PARKED: data protection (spec §14) - deletion must also purge photo, sheets, art and PDF per retention policy
        # Prefer active job; fall back to archived.
        if (_job_dir(job_id) / "job.json").is_file():
            job_dir = _job_dir(job_id)
            redirect_to = "index"
            job_page_fallback = True
        elif (_archived_job_dir(job_id) / "job.json").is_file():
            job_dir = _archived_job_dir(job_id)
            redirect_to = "archived_jobs"
            job_page_fallback = False
        else:
            abort(404)

        job = read_json(job_dir / "job.json") or {}
        if _job_is_busy(job):
            flash("Wait until generation/changes finish before deleting.", "error")
            return redirect(
                url_for("job_page", job_id=job_id)
                if job_page_fallback
                else url_for("archived_jobs")
            )
        try:
            append_event(job_dir, "job_deleted", "Job permanently deleted.")
            shutil.rmtree(job_dir)
            flash("Job deleted permanently.", "success")
            return redirect(url_for(redirect_to))
        except Exception as error:
            logger.exception("Delete failed for %s", job_id)
            flash(f"Delete failed: {error}", "error")
            return redirect(
                url_for("job_page", job_id=job_id)
                if job_page_fallback
                else url_for("archived_jobs")
            )

    @app.get("/memory")
    def memory_page():
        memory = load_memory()
        return render_template("memory.html", memory=memory)

    return app


BUSY_JOB_STATUSES = {
    "queued",
    "starting",
    "generating_character",
    "rebuilding_story",
    "humanizing_story",
    "compiling_pages",
    "humanizing_pages",
    "validating_content",
    "generating_preview",
    "generating_cover",
    "generating_pages",
    "building_pdf",
    "applying_changes",
    "rebuilding_content",
    "self_revising",
    "regenerating_spread",
}


READ_ALOUD_SPREADS = (1, 7, 14)  # the human reads these aloud; every spread still gets a row
SPREAD_EDITABLE_STATUSES = {
    "content_ready",
    "preview_ready",
    "validation_failed",
    "failed",
    "complete",
    "self_revise_ready",
}


def _spread_progress(job_dir: Path) -> dict:
    return {"spreads_done": count_completed_spreads(job_dir), "spreads_total": SPREAD_COUNT}


def _safe_regenerate_spread(job_id, job_dir, n, instruction):
    try:
        run_regenerate_spread(job_id, job_dir, n, instruction)
    except Exception:
        logger.exception("Spread %s regenerate failed for %s", n, job_id)


def _safe_regenerate_cover(job_id, job_dir, previous_status):
    """Redraw front + back cover art (and the PDF when one was built), then restore the job status."""
    try:
        from pipeline.illustrate import generate_cover_panels

        inputs = read_json(job_dir / "inputs.json") or {}
        story = read_json(job_dir / "story" / "rebuilt_story.json") or {}
        character_path = job_dir / "character" / "character.png"
        for name in ("front_art.png", "back_art.png"):
            (job_dir / "cover" / name).unlink(missing_ok=True)
        generate_cover_panels(create_client(), job_dir, inputs, story, character_path)
        job = read_json(job_dir / "job.json") or {}
        updates = {"status": previous_status or "complete", "error": None}
        if job.get("pdf") and count_completed_spreads(job_dir) == SPREAD_COUNT:
            pages = (read_json(job_dir / "story" / "pages.json") or {}).get("pages") or []
            updates["pdf"] = build_pdf(job_dir, story, pages, inputs).relative_to(job_dir).as_posix()
        job.update(updates)
        write_json(job_dir / "job.json", job)
    except Exception as error:
        logger.exception("Cover regenerate failed for %s", job_id)
        job = read_json(job_dir / "job.json") or {}
        job.update({"status": "failed", "error": f"Cover regenerate failed: {error}"})
        write_json(job_dir / "job.json", job)
        append_event(job_dir, "job_failed", str(error))


def _spread_cards(job_id: str, job_dir: Path, story, pages: list) -> list[dict]:
    """One card per spread (14): art, text, brief, anchor, ppi and fold / calm-zone warnings."""
    briefs = spread_pages(pages)
    story_spreads = {int(s.get("n") or 0): s for s in (story or {}).get("spreads") or []}
    cards = []
    for n in range(1, SPREAD_COUNT + 1):
        brief = briefs.get(n)
        if brief is None:
            continue
        meta = read_json(job_dir / "spreads" / f"spread-{n:02d}" / "spread.json") or {}
        spread = story_spreads.get(n) or {}
        art = spread_art_path(job_dir, n)
        composed = job_dir / "print" / "spreads" / f"spread-{n:02d}.jpg"
        zone = brief.get("calm_zone") or ("left" if n % 2 == 1 else "right")
        cards.append(
            {
                "n": n,
                "pages": (2 * n, 2 * n + 1),
                "text": spread_text(pages, n),
                "scene": spread.get("scene") or brief.get("scene") or "",
                "calm_zone": zone,
                "anchor_state": brief.get("anchor_state"),
                "art_exists": art.is_file(),
                "composed_exists": composed.is_file(),
                "art_url": url_for("spread_art", job_id=job_id, n=n),
                "composed_url": url_for("spread_composed", job_id=job_id, n=n),
                "effective_ppi": meta.get("effective_ppi"),
                "resolution_status": meta.get("resolution_status"),
                "size_received": meta.get("size_received"),
                "fold_failures": meta.get("fold_failures") or [],
                "no_face_warning": bool(meta.get("no_face_warning")),
                "calm_zone_busy": bool(meta.get("calm_zone_busy")),
                "anchor_state_meta": meta.get("anchor_state"),
                "edit_instruction": meta.get("edit_instruction"),
            }
        )
    return cards


def _last_line_problem(inputs: dict, story) -> str | None:
    """Guard message when spread 14 does not carry the catalogue last line; None when fine."""
    if not story or not inputs.get("title_id"):
        return None
    try:
        last_line = (get_title(inputs["title_id"]).get("last_line") or "").replace("{name}", inputs.get("name", ""))
    except Exception:  # noqa: BLE001
        return None
    final = next((s for s in story.get("spreads") or [] if int(s.get("n") or 0) == 14), None)
    if not last_line or final is None:
        return None
    text = final.get("text") or ""
    return None if last_line_present(text, last_line) else last_line_message(text, last_line)


def _job_is_busy(job: dict) -> bool:
    return (job or {}).get("status") in BUSY_JOB_STATUSES


def _valid_job_id(job_id: str) -> bool:
    return len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id)


def _job_dir(job_id: str) -> Path:
    if not _valid_job_id(job_id):
        abort(404)
    return JOB_ROOT / job_id


def _archived_job_dir(job_id: str) -> Path:
    if not _valid_job_id(job_id):
        abort(404)
    return ARCHIVE_ROOT / job_id


def _count_jobs(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.iterdir() if path.is_dir() and (path / "job.json").is_file())


def _list_jobs(root: Path, limit=40):
    if not root.exists():
        return []
    jobs = []
    for path in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir():
            continue
        job = read_json(path / "job.json")
        if job:
            jobs.append(job)
        if len(jobs) >= limit:
            break
    return jobs


def _safe_run_job(job_id, job_dir, mode=None):
    try:
        run_job(job_id, job_dir, mode=mode)
    except Exception:
        logger.exception("Background job %s failed", job_id)


def _safe_rebuild_cast(job_id, job_dir, approved_characters, removed_names, preserve):
    try:
        load_dotenv(BASE_DIR / ".env", override=True)
        inputs = read_json(job_dir / "inputs.json")
        client = create_client()
        rebuild_content_for_cast(
            client,
            job_dir,
            inputs,
            approved_characters,
            removed_names,
            preserve,
        )
    except Exception as error:
        logger.exception("Cast rebuild failed for %s", job_id)
        job = read_json(job_dir / "job.json") or {}
        job.update({"status": "failed", "error": f"Cast rebuild failed: {error}"})
        write_json(job_dir / "job.json", job)
        append_event(job_dir, "cast_rebuild_failed", str(error))


def _safe_process_change_queue(job_id, job_dir):
    if not change_lock.acquire(blocking=False):
        # Another worker is already draining the queue.
        return
    try:
        load_dotenv(BASE_DIR / ".env", override=True)
        inputs = read_json(job_dir / "inputs.json")
        client = create_client()
        process_change_queue(client, job_dir, inputs)
    except Exception:
        logger.exception("Change queue failed for %s", job_id)
    finally:
        change_lock.release()
        # If new items arrived while we were finishing, drain again.
        job = read_json(job_dir / "job.json") or {}
        pending = [
            item
            for item in (job.get("change_queue") or [])
            if item.get("status") == "queued"
        ]
        if pending:
            job_executor.submit(_safe_process_change_queue, job_id, job_dir)


def _safe_self_revise(job_id, job_dir, inputs):
    if not change_lock.acquire(blocking=False):
        job = read_json(job_dir / "job.json") or {}
        job["error"] = "Another change is running; try self-revise again when it finishes."
        write_json(job_dir / "job.json", job)
        return
    try:
        load_dotenv(BASE_DIR / ".env", override=True)
        client = create_client()
        run_self_revise(
            client,
            job_dir,
            inputs or read_json(job_dir / "inputs.json"),
            force=True,
        )
    except Exception as error:
        logger.exception("Self-revise failed for %s", job_id)
        job = read_json(job_dir / "job.json") or {}
        job.update(
            {
                "status": "complete",
                "error": f"Self-revise failed: {error}",
                "self_revise": {"phase": "failed", "summary": str(error)},
            }
        )
        write_json(job_dir / "job.json", job)
        append_event(job_dir, "self_revise_failed", str(error))
    finally:
        change_lock.release()


def _safe_apply_self_revise(job_id, job_dir, inputs, actions, summary):
    if not change_lock.acquire(blocking=False):
        job = read_json(job_dir / "job.json") or {}
        job["error"] = "Another change is running; try applying the plan again when it finishes."
        write_json(job_dir / "job.json", job)
        return
    try:
        load_dotenv(BASE_DIR / ".env", override=True)
        client = create_client()
        apply_self_revise_plan(
            client,
            job_dir,
            inputs or read_json(job_dir / "inputs.json"),
            actions,
            summary=summary,
        )
    except Exception as error:
        logger.exception("Self-revise apply failed for %s", job_id)
        job = read_json(job_dir / "job.json") or {}
        job.update(
            {
                "status": "self_revise_ready",
                "error": f"Applying self-revise failed: {error}",
                "self_revise": {
                    **(job.get("self_revise") or {}),
                    "phase": "awaiting_approval",
                    "summary": f"Apply failed: {error}",
                },
            }
        )
        write_json(job_dir / "job.json", job)
        append_event(job_dir, "self_revise_apply_failed", str(error))
    finally:
        change_lock.release()


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=False)
