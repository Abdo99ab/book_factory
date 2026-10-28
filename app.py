"""Raised Ready Book Factory v2 — create → preview → build → edit."""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

# Load repo-root .env before any pipeline import (constants reads models at import time).
load_dotenv(Path(__file__).resolve().parent / ".env")

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from pipeline.catalogue import get_title, list_factory_titles
from pipeline.constants import (
    ALLOWED_IMAGE_EXTENSIONS,
    ARCHIVE_ROOT,
    CONTENT_PAGE_COUNT,
    JOB_CONCURRENCY,
    JOB_ROOT,
    PRONOUNS,
    SPREAD_COUNT,
)
from pipeline.job_log import append_event, read_events, read_json, write_json
from pipeline.content import calm_zone_for
from pipeline.runner import (
    compose_missing_spread_ui_previews,
    run_build,
    run_edit_cover,
    run_edit_spread,
    run_edit_text,
    run_prepare,
    run_preview,
    spreads_missing_composed_preview,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

job_executor = ThreadPoolExecutor(max_workers=JOB_CONCURRENCY)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    @app.get("/")
    def home():
        return redirect(url_for("create_form"))

    @app.get("/create")
    def create_form():
        # Only FINAL locked stories (doc 00 → story 01 standard). Drafts stay out of the UI.
        return render_template("create.html", titles=list_factory_titles())

    @app.post("/jobs")
    def create_job():
        title_id = (request.form.get("title_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        age = (request.form.get("age") or "").strip()
        pronouns = (request.form.get("pronouns") or "").strip().lower()
        carer_name = (request.form.get("carer_name") or "").strip()
        carer_role = (request.form.get("carer_role") or "").strip()
        giver = (request.form.get("giver") or "").strip() or carer_name
        home_type = (request.form.get("home_type") or "house").strip().lower()
        comfort_object = (request.form.get("comfort_object") or "").strip()
        dedication_text = (request.form.get("dedication_text") or "").strip()
        order_id = (request.form.get("order_id") or "").strip()
        reference = request.files.get("reference_image")

        if not title_id or not name or not age or pronouns not in PRONOUNS:
            flash("Title, name, age and pronouns are required.", "error")
            return redirect(url_for("create_form"))
        if not carer_name or not carer_role or not comfort_object or not order_id:
            flash("Carer, comfort object and order id are required.", "error")
            return redirect(url_for("create_form"))
        if not reference or not reference.filename:
            flash("Upload a child photo.", "error")
            return redirect(url_for("create_form"))
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("_OPENAI_API_KEY")):
            flash("OPENAI_API_KEY is not configured.", "error")
            return redirect(url_for("create_form"))
        try:
            title = get_title(title_id)
        except Exception:
            flash("Unknown title.", "error")
            return redirect(url_for("create_form"))
        if not title.get("approved") or title_id not in {t["title_id"] for t in list_factory_titles()}:
            flash("That title is not ready for Book Factory yet (needs a FINAL locked story).", "error")
            return redirect(url_for("create_form"))

        slots = {}
        for slot in title.get("personalisation_slots") or []:
            sid = slot["id"]
            val = (request.form.get(f"slot_{sid}") or "").strip()
            if slot.get("required") and not val:
                flash(f"Slot required: {slot.get('label') or sid}", "error")
                return redirect(url_for("create_form"))
            if val:
                slots[sid] = val

        ext = Path(secure_filename(reference.filename)).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            flash("Photo must be PNG, JPG or WEBP.", "error")
            return redirect(url_for("create_form"))

        job_id = uuid.uuid4().hex
        job_dir = JOB_ROOT / job_id
        char_dir = job_dir / "character"
        char_dir.mkdir(parents=True, exist_ok=True)
        reference.save(char_dir / f"reference{ext}")

        inputs = {
            "title_id": title_id,
            "name": name,
            "age": age,
            "pronouns": pronouns,
            "carer": {"name": carer_name, "role": carer_role},
            "giver": giver,
            "home_type": home_type if home_type in {"flat", "house"} else "house",
            "comfort_object": comfort_object,
            "slots": slots,
            "dedication_text": dedication_text,
            "order_id": order_id,
            "page_count": CONTENT_PAGE_COUNT,
        }
        write_json(job_dir / "inputs.json", inputs)
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": "queued",
                "book_title": title["title_template"].replace("{name}", name),
                "name": name,
                "title_id": title_id,
                "order_id": order_id,
                "spreads_total": SPREAD_COUNT,
                "preview_ready": False,
                "pdf": None,
                "error": None,
                "created_at": time.time(),
            },
        )
        append_event(job_dir, "job_created", "Job created (v2).")
        job_executor.submit(_safe, run_prepare, job_id, job_dir)
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/jobs/<job_id>")
    def job_page(job_id):
        job_dir = _job_dir(job_id)
        job = read_json(job_dir / "job.json") or {}
        inputs = read_json(job_dir / "inputs.json") or {}
        story = read_json(job_dir / "story" / "manuscript.json") or {}
        events = read_events(job_dir)[-40:]
        # Backfill composed previews (art + story text) for any spread that still lacks one.
        busy = job.get("status") in {
            "queued",
            "generating_content",
            "generating_character",
            "generating_preview",
            "building",
            "building_pdf",
            "editing",
        }
        if not busy and inputs.get("title_id") and spreads_missing_composed_preview(job_dir):
            job_executor.submit(_safe, compose_missing_spread_ui_previews, job_dir, inputs, story)

        spreads = []
        for n in range(1, SPREAD_COUNT + 1):
            art = job_dir / "spreads" / f"spread-{n:02d}" / "art.png"
            preview = job_dir / "spreads" / f"spread-{n:02d}" / "preview.png"
            printed = job_dir / "print" / "spreads" / f"spread-{n:02d}.jpg"
            meta = read_json(job_dir / "spreads" / f"spread-{n:02d}" / "spread.json") or {}
            sp = next((s for s in story.get("spreads") or [] if int(s["n"]) == n), {}) or {}
            # Prefer composed preview (art + calm-half text), then print stitch, then raw art.
            if preview.is_file():
                show = preview
            elif printed.is_file():
                show = printed
            else:
                show = art
            spreads.append(
                {
                    "n": n,
                    "has_art": art.is_file() or preview.is_file() or printed.is_file(),
                    "art_rel": show.relative_to(job_dir).as_posix() if show.is_file() else f"spreads/spread-{n:02d}/art.png",
                    "has_composed": preview.is_file() or printed.is_file(),
                    "calm_zone": meta.get("calm_zone") or sp.get("calm_zone") or calm_zone_for(n),
                    "text": sp.get("text") or "",
                }
            )
        title_meta = None
        try:
            if inputs.get("title_id"):
                title_meta = get_title(inputs["title_id"])
        except Exception:
            title_meta = None
        return render_template(
            "job.html",
            job=job,
            inputs=inputs,
            story=story,
            events=events,
            spreads=spreads,
            title_meta=title_meta,
            has_character=(job_dir / "character" / "character.png").is_file(),
            has_front=(job_dir / "cover" / "front_art.png").is_file(),
            has_back=(job_dir / "cover" / "back_art.png").is_file(),
            front_preview=(job_dir / "cover" / "front_preview.png").is_file(),
        )

    @app.post("/jobs/<job_id>/preview")
    def start_preview(job_id):
        job_dir = _job_dir(job_id)
        job_executor.submit(_safe, run_preview, job_id, job_dir)
        flash("Preview started (front cover + 2 spreads).", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/build")
    def start_build(job_id):
        job_dir = _job_dir(job_id)
        job_executor.submit(_safe, run_build, job_id, job_dir)
        flash("Full build started (keeps existing art).", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/edit/spread/<int:n>")
    def edit_spread(job_id, n):
        job_dir = _job_dir(job_id)
        instruction = (request.form.get("instruction") or "").strip()
        text = request.form.get("text")
        if text is not None and request.form.get("save_text"):
            run_edit_text(job_dir, f"spread:{n}", text)
            flash(f"Spread {n} text saved.", "success")
            return redirect(url_for("job_page", job_id=job_id))
        if not instruction:
            flash("Add an edit instruction for the art.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        job_executor.submit(_safe, run_edit_spread, job_id, job_dir, n, instruction)
        flash(f"Regenerating spread {n}…", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.post("/jobs/<job_id>/edit/cover/<kind>")
    def edit_cover(job_id, kind):
        if kind not in {"front", "back"}:
            abort(404)
        job_dir = _job_dir(job_id)
        instruction = (request.form.get("instruction") or "").strip()
        if not instruction:
            flash("Add an edit instruction.", "error")
            return redirect(url_for("job_page", job_id=job_id))
        job_executor.submit(_safe, run_edit_cover, job_id, job_dir, kind, instruction)
        flash(f"Regenerating {kind} cover…", "success")
        return redirect(url_for("job_page", job_id=job_id))

    @app.get("/jobs/<job_id>/file/<path:rel>")
    def job_file(job_id, rel):
        job_dir = _job_dir(job_id)
        path = (job_dir / rel).resolve()
        if not str(path).startswith(str(job_dir.resolve())) or not path.is_file():
            abort(404)
        return send_file(path)

    @app.get("/jobs")
    def list_jobs():
        jobs = []
        for path in sorted(JOB_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.is_dir():
                job = read_json(path / "job.json")
                if job:
                    jobs.append(job)
        return render_template("jobs.html", jobs=jobs[:50])

    return app


def _job_dir(job_id: str) -> Path:
    path = JOB_ROOT / job_id
    if not path.is_dir():
        abort(404)
    return path


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        logger.exception("Background job failed")


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
