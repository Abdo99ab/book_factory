"""QA reports and the human-check gate (spec §8.3, §13).

Four deterministic reports run on a built job (no LLM, no network):
  * structure      - 32-page map, 28 art files, locked copy, PDF page count / sizes / brand fonts, ppi
  * text_guard     - the Step 5 text guards re-run on the final spread text
  * anchor_state   - the anchor object follows the catalogue state schedule (never resets)
  * face_position  - no face in the vision review overlaps the live text box

``review_record`` comes from the self-revise vision pass (``qa/review_record.json``); when it has not
run, anchor/face checks warn "not reviewed" instead of passing silently.

Send is blocked until: a PDF exists, the QA report for THAT PDF has no errors, and a named human has
ticked every human-check item for THAT PDF (rebuilding the PDF makes both stale).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import imagecheck
from .catalogue import get_title
from .constants import (
    CONTENT_PAGE_COUNT,
    PAGE_WITH_BLEED_MM,
    PPI_FAIL,
    PPI_WARN,
    PRINTER,
    SPREAD_COUNT,
)
from .job_log import append_event, read_json, write_json
from .pages import spread_art_path
from .text_guards import check_spreads

QA_DIR = "qa"

HUMAN_CHECK_ITEMS = [
    ("likeness", "The child in the book is recognisably the child in the photo (hair, skin tone, glasses, marks)."),
    ("consistency", "Same child, outfit and anchor object on every spread."),
    ("anchor_state", "The anchor object follows its schedule and never resets."),
    ("text_read", "Every page's text read aloud: UK English, natural, correct names."),
    ("text_clear", "No text sits over a face or a key object, and all of it is legible."),
    ("cover", "Cover, spine and back checked: band, title, blurb, logo, QR keep-clear."),
    ("sensitive", "Sensitive scenes are age-appropriate and respectful of a real child's likeness."),
]


# --- paths / hashing ------------------------------------------------------------------------------


def qa_dir(job_dir: Path) -> Path:
    path = Path(job_dir) / QA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def pdf_sha256(job_dir: Path) -> str | None:
    job = read_json(Path(job_dir) / "job.json") or {}
    rel = job.get("pdf")
    if not rel:
        return None
    path = Path(job_dir) / rel
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue(code: str, severity: str, message: str, **extra) -> dict:
    return {"code": code, "severity": severity, "message": message, **extra}


# --- image checks used by structure: duplicate pages + cover layout ---------------------------------


def pair_duplicates(job_dir: Path, spread_numbers=range(1, SPREAD_COUNT + 1)) -> list[dict]:
    """Spreads whose left and right art are perceptually the same picture (16x16 grey MAD)."""
    found = []
    for n in spread_numbers:
        left, right = spread_art_path(job_dir, n, "left"), spread_art_path(job_dir, n, "right")
        if not (left.is_file() and right.is_file()):
            continue
        duplicate, distance = imagecheck.is_duplicate_pair(left, right)
        if duplicate:
            found.append({"spread": n, "right_page": 2 * n + 1, "distance": round(distance, 4)})
    return found


def pair_duplicate_issues(job_dir: Path) -> list[dict]:
    return [
        _issue(
            "pair_duplicate",
            "warning",
            f"Spread {d['spread']}: left and right pictures are almost identical "
            f"(difference {d['distance']:.3f} < {imagecheck.PAIR_DUPLICATE_THRESHOLD}). "
            f"Regenerate the right page (page {d['right_page']}) with a different framing.",
            spread=d["spread"],
            page=d["right_page"],
            distance=d["distance"],
            proposed_action="regen_page_art",
        )
        for d in pair_duplicates(Path(job_dir))
    ]


def pair_duplicate_actions(job_dir: Path) -> list[dict]:
    """Self-revise style actions: regenerate the RIGHT page of each duplicate pair."""
    return [
        {
            "type": "regen_page_art",
            "page": d["right_page"],
            "reason": (
                f"pair_duplicate: spread {d['spread']} shows the same picture on both pages "
                f"(difference {d['distance']:.3f}); redraw the right page from a clearly different view."
            ),
            "edit_instruction": (
                "Draw this page from a clearly different framing than the left page "
                "(different camera distance and angle); do not copy the left composition."
            ),
            "force_character_lock": False,
            "scene": None,
            "text": None,
            "text_placement": None,
        }
        for d in pair_duplicates(Path(job_dir))
    ]


def cover_layout_issues(job_dir: Path) -> list[dict]:
    cover = read_json(Path(job_dir) / "cover" / "cover.json") or {}
    layout = cover.get("layout") or {}
    if not layout.get("failures"):
        return []
    return [
        _issue(
            "cover_layout_warning",
            "warning",
            "Front cover art breaks the layout rules (soft plate forced): " + "; ".join(layout["failures"]),
            failures=layout["failures"],
        )
    ]


_FACE_SYSTEM = (
    "You locate faces in a children's book cover illustration. Return JSON "
    '{"faces": [{"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}]} with one box per visible face, '
    "normalised 0-1 (x, y = top-left corner as a fraction of the image width / height). "
    'Return {"faces": []} when no face is visible.'
)


def detect_faces(client, image_path: Path) -> list[dict] | None:
    """Vision face boxes (same normalised format as ``review_record`` faces); None = could not run."""
    import base64
    import io
    import json

    from PIL import Image

    from .openai_util import chat_json_vision

    try:
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((640, 640))
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=75)
        url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        raw = chat_json_vision(
            client,
            _FACE_SYSTEM,
            [
                {"type": "text", "text": "Find every face in this front-cover illustration."},
                {"type": "image_url", "image_url": {"url": url}},
            ],
            description="Cover face position",
            temperature=0.0,
        )
        faces = json.loads(raw).get("faces")
        if not isinstance(faces, list):
            return None
        return [
            {k: float(f[k]) for k in ("x", "y", "w", "h")}
            for f in faces
            if isinstance(f, dict) and all(k in f for k in ("x", "y", "w", "h"))
        ]
    except Exception:  # noqa: BLE001 - detection is advisory; never break the build
        return None


# --- 1. structure ---------------------------------------------------------------------------------


def structure_report(job_dir: Path, inputs: dict, story: dict, pages: list) -> dict:
    from . import pdf_build
    from .illustrate import min_effective_ppi

    job_dir = Path(job_dir)
    issues: list[dict] = []

    try:
        pdf_build.assert_page_map(pages)
    except ValueError as error:
        issues.append(_issue("page_map", "error", str(error)))

    missing = [
        f"spread {n} {side}"
        for n in range(1, SPREAD_COUNT + 1)
        for side in ("left", "right")
        if not spread_art_path(job_dir, n, side).is_file()
    ]
    if missing:
        issues.append(_issue("art_missing", "error", f"{len(missing)} page image(s) missing: {', '.join(missing[:4])}…"))

    locked = story.get("locked") or {}
    for key in ("title", "front_band", "back_question"):
        if not (locked.get(key) or "").strip():
            issues.append(_issue("locked_missing", "error", f"Locked copy '{key}' is empty."))
    if not locked.get("parent_spread"):
        issues.append(_issue("locked_missing", "error", "Locked parent spread is empty."))

    from .constants import ENFORCE_PRINT_PPI

    lowest = min_effective_ppi(job_dir)
    if lowest is not None:
        if lowest < PPI_FAIL and ENFORCE_PRINT_PPI:
            issues.append(_issue("resolution", "error", f"Lowest page is {lowest} ppi (< {PPI_FAIL}); no upscaler - regenerate larger."))
        elif lowest < PPI_FAIL:
            issues.append(_issue("resolution", "warning", f"Lowest page is {lowest} ppi (< {PPI_FAIL}); continuing (ENFORCE_PRINT_PPI off)."))
        elif lowest < PPI_WARN:
            issues.append(_issue("resolution", "warning", f"Lowest page is {lowest} ppi (< {PPI_WARN} target, >= {PPI_FAIL} minimum)."))

    pdf_info: dict = {}
    job = read_json(job_dir / "job.json") or {}
    rel = job.get("pdf")
    pdf_path = job_dir / rel if rel else None
    if not pdf_path or not pdf_path.is_file():
        issues.append(_issue("pdf_missing", "error", "No print PDF has been built yet."))
    else:
        try:
            from pypdf import PdfReader

            count = len(PdfReader(str(pdf_path)).pages)
            expected = CONTENT_PAGE_COUNT
            if PRINTER["packaging"] == "single_pdf_cover_flat_first":
                expected += 1 + (2 if PRINTER.get("endpapers_in_pdf") else 0)
            else:
                expected += 2
            pdf_info = {"pages": count, "expected_pages": expected, "name": pdf_path.name}
            if count != expected:
                issues.append(_issue("pdf_page_count", "error", f"PDF has {count} pages, printer expects {expected}."))
            pdf_build.assert_brand_fonts_only(pdf_path)
        except RuntimeError as error:
            issues.append(_issue("fonts", "error", str(error)))
        except Exception as error:  # unreadable PDF
            issues.append(_issue("pdf_unreadable", "error", f"PDF could not be read: {error}"))

    issues.extend(pair_duplicate_issues(job_dir))
    issues.extend(cover_layout_issues(job_dir))

    return {
        "name": "structure",
        "passed": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "min_effective_ppi": lowest,
        "page_box_mm": PAGE_WITH_BLEED_MM,
        "pdf": pdf_info,
    }


# --- 2. text guards -------------------------------------------------------------------------------


def text_guard_report(inputs: dict, story: dict) -> dict:
    title = get_title(inputs["title_id"])
    locked = story.get("locked") or {}
    candidate = {"spreads": story.get("spreads") or [], "locked": locked}
    issues = check_spreads(
        candidate,
        candidate,
        locked,
        child_name=inputs["name"],
        age_band=inputs.get("age_band") or title["age_band"],
        last_line=(title.get("last_line") or "").replace("{name}", inputs["name"]),
    )
    out = [_issue(i["code"], "error", i["message"], spread=i.get("spread")) for i in issues]
    return {"name": "text_guard", "passed": not out, "issues": out}


# --- 3. anchor state ------------------------------------------------------------------------------


def anchor_state_report(job_dir: Path, inputs: dict, review: dict | None) -> dict:
    title = get_title(inputs["title_id"])
    anchor = title.get("anchor") or {}
    schedule = list(anchor.get("state_schedule") or [])
    issues: list[dict] = []
    if len(schedule) != SPREAD_COUNT:
        issues.append(_issue("schedule_length", "error", f"Anchor schedule has {len(schedule)} entries, expected {SPREAD_COUNT}."))
        return {"name": "anchor_state", "passed": False, "issues": issues}

    if anchor.get("state_kind") == "ticks":
        for n in range(1, SPREAD_COUNT):
            if schedule[n] < schedule[n - 1]:
                issues.append(_issue("anchor_reset", "error", f"Schedule drops from {schedule[n - 1]} to {schedule[n]} at spread {n + 1}.", spread=n + 1))

    seen = ((review or {}).get("pages") or {})
    reviewed = 0
    for n in range(1, SPREAD_COUNT + 1):
        meta = read_json(Path(job_dir) / "spreads" / f"spread-{n:02d}" / "spread.json") or {}
        if meta.get("anchor_state") is not None and meta["anchor_state"] != schedule[n - 1]:
            issues.append(_issue("anchor_state_mismatch", "error", f"Spread {n} was generated for state {meta['anchor_state']}, schedule says {schedule[n - 1]}.", spread=n))
        for page in (2 * n, 2 * n + 1):
            observed = (seen.get(str(page)) or {}).get("anchor_state_seen")
            if observed is None:
                continue
            reviewed += 1
            if int(observed) != schedule[n - 1]:
                issues.append(_issue("anchor_state_seen", "error", f"Page {page}: reviewer saw state {observed}, schedule says {schedule[n - 1]}.", spread=n, page=page))
    if not reviewed:
        issues.append(_issue("anchor_not_reviewed", "warning", "No vision review of the anchor object yet (run Self revise)."))
    return {"name": "anchor_state", "passed": not any(i["severity"] == "error" for i in issues), "issues": issues}


# --- 4. face position -----------------------------------------------------------------------------


def _rects_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def face_position_report(job_dir: Path, review: dict | None) -> dict:
    layout = read_json(Path(job_dir) / "print" / "layout.json") or {}
    text_boxes = {
        r["page"]: r["box_mm"]
        for r in layout.get("pages") or []
        if r.get("kind") == "spread_half" and r.get("box_mm")
    }
    seen = ((review or {}).get("pages") or {})
    issues: list[dict] = []
    checked = 0
    for page, box in text_boxes.items():
        record = seen.get(str(page))
        if record is None:
            continue
        checked += 1
        for face in record.get("faces") or []:
            rect = tuple(float(face.get(k, 0)) * PAGE_WITH_BLEED_MM for k in ("x", "y", "w", "h"))
            if _rects_overlap(rect, tuple(box)):
                issues.append(_issue("text_over_face", "error", f"Page {page}: text box overlaps a face.", page=page))
                break
    if not checked:
        issues.append(_issue("faces_not_reviewed", "warning", "No vision review of face positions yet (run Self revise)."))
    return {"name": "face_position", "passed": not any(i["severity"] == "error" for i in issues), "issues": issues}


# --- orchestration --------------------------------------------------------------------------------


def load_review_record(job_dir: Path) -> dict | None:
    return read_json(Path(job_dir) / QA_DIR / "review_record.json")


def run_qa(job_dir: Path) -> dict:
    """Run all four reports, save ``qa/qa_report.json`` keyed to the current PDF hash."""
    job_dir = Path(job_dir)
    inputs = read_json(job_dir / "inputs.json") or {}
    story = read_json(job_dir / "story" / "rebuilt_story.json") or {}
    pages = (read_json(job_dir / "story" / "pages.json") or {}).get("pages") or []
    review = load_review_record(job_dir)

    reports = [
        structure_report(job_dir, inputs, story, pages),
        text_guard_report(inputs, story) if story else {"name": "text_guard", "passed": False, "issues": [_issue("story_missing", "error", "No story to check.")]},
        anchor_state_report(job_dir, inputs, review),
        face_position_report(job_dir, review),
    ]
    errors = sum(1 for r in reports for i in r["issues"] if i["severity"] == "error")
    warnings = sum(1 for r in reports for i in r["issues"] if i["severity"] == "warning")
    report = {
        "passed": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "generated_at": time.time(),
        "pdf_sha256": pdf_sha256(job_dir),
        "min_effective_ppi": reports[0].get("min_effective_ppi"),
        "reports": {r["name"]: r for r in reports},
    }
    write_json(qa_dir(job_dir) / "qa_report.json", report)
    append_event(
        job_dir,
        "qa_report",
        f"QA {'passed' if report['passed'] else 'FAILED'}: {errors} error(s), {warnings} warning(s).",
        passed=report["passed"],
        errors=errors,
        warnings=warnings,
    )
    return report


def load_qa_report(job_dir: Path) -> dict | None:
    return read_json(Path(job_dir) / QA_DIR / "qa_report.json")


# --- human check ----------------------------------------------------------------------------------


def load_human_check(job_dir: Path) -> dict | None:
    return read_json(Path(job_dir) / QA_DIR / "human_check.json")


def record_human_check(job_dir: Path, reviewer: str, ticked) -> dict:
    """Store a human sign-off for the CURRENT pdf. Incomplete ticks are saved but do not pass."""
    reviewer = (reviewer or "").strip()
    keys = [k for k, _ in HUMAN_CHECK_ITEMS]
    ticked = {k for k in ticked if k in keys}
    record = {
        "reviewer": reviewer,
        "items": {k: (k in ticked) for k in keys},
        "complete": bool(reviewer) and len(ticked) == len(keys),
        "pdf_sha256": pdf_sha256(job_dir),
        "at": time.time(),
    }
    write_json(qa_dir(job_dir) / "human_check.json", record)
    append_event(
        Path(job_dir),
        "human_check_saved",
        f"Human check by {reviewer or 'nobody'}: {len(ticked)}/{len(keys)} items ticked.",
        complete=record["complete"],
    )
    return record


# --- send gate ------------------------------------------------------------------------------------


def send_blockers(job_dir: Path) -> list[str]:
    """Reasons Send is blocked. Empty list means the preview may be sent."""
    job_dir = Path(job_dir)
    current = pdf_sha256(job_dir)
    if current is None:
        return ["No print PDF yet - finish the build."]
    blockers: list[str] = []
    report = load_qa_report(job_dir)
    if report is None:
        blockers.append("QA has not been run.")
    elif report.get("pdf_sha256") != current:
        blockers.append("QA is out of date (the PDF changed) - run QA again.")
    elif not report.get("passed"):
        blockers.append(f"QA failed with {report.get('errors', 0)} error(s).")
    check = load_human_check(job_dir)
    if check is None:
        blockers.append("Human check not done.")
    elif check.get("pdf_sha256") != current:
        blockers.append("Human check is out of date (the PDF changed) - tick it again.")
    elif not check.get("complete"):
        blockers.append("Human check is incomplete (every item and a reviewer name are required).")
    return blockers


def send_preview(job_dir: Path) -> dict:
    """Mark the preview as sent if the gate is open. Raises PermissionError when blocked."""
    job_dir = Path(job_dir)
    blockers = send_blockers(job_dir)
    if blockers:
        raise PermissionError("; ".join(blockers))
    job = read_json(job_dir / "job.json") or {}
    job["sent_at"] = time.time()
    write_json(job_dir / "job.json", job)
    append_event(job_dir, "preview_sent", "Preview released after QA pass and human check.")
    return job
