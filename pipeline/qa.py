"""QA reports and the human-check gate (spec §8.3, §13).

Four deterministic reports run on a built job (no LLM, no network):
  * structure      - 32-page map, 14 spread art files, locked copy, PDF page count / sizes / brand fonts, ppi
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
    SPREAD_HEIGHT_MM,
    SPREAD_WIDTH_MM,
    PPI_FAIL,
    PPI_WARN,
    PRINTER,
    SPREAD_COUNT,
)
from .job_log import append_event, read_json, write_json
from .pages import spread_art_path
from .text_guards import check_spreads

QA_DIR = "qa"

# The human reads these spreads aloud (opening, middle, close); every spread still gets its own row.
READ_ALOUD_SPREADS = (1, 7, 14)


def _human_check_items() -> list[tuple[str, str]]:
    """One row for the child's likeness, one for the cover, and one per spread."""
    items = [
        ("likeness", "The child in the book is recognisably the child in the photo (hair, skin tone, glasses, marks)."),
        ("cover", "Cover, spine and back checked: band, title, blurb, logo, QR keep-clear."),
    ]
    for n in range(1, SPREAD_COUNT + 1):
        label = (
            f"Spread {n} (pages {2 * n}-{2 * n + 1}): same child, outfit and anchor state; "
            "nothing important on the fold; text clear of faces and legible."
        )
        if n in READ_ALOUD_SPREADS:
            label += " Read the text aloud: UK English, natural, correct names."
        items.append((f"spread_{n:02d}", label))
    items.append(("sensitive", "Sensitive scenes are age-appropriate and respectful of a real child's likeness."))
    return items


HUMAN_CHECK_ITEMS = _human_check_items()


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


# --- image checks used by structure: fold / calm zone + cover layout ---------------------------------


def spread_layout_issues(job_dir: Path, spread_numbers=range(1, SPREAD_COUNT + 1)) -> list[dict]:
    """Warnings recorded at generation time: a face on the fold / edge / crop band, a busy calm half."""
    issues: list[dict] = []
    for n in spread_numbers:
        meta = read_json(Path(job_dir) / "spreads" / f"spread-{n:02d}" / "spread.json") or {}
        zones = meta.get("fold_failures") or []
        if zones:
            issues.append(
                _issue(
                    "fold_warning",
                    "warning",
                    f"Spread {n}: a face breaks the {', '.join(zones)} rule after one retry; "
                    "check the fold on the composed spread.",
                    spread=n,
                    zones=zones,
                    proposed_action="regen_spread_art",
                )
            )
        if meta.get("calm_zone_busy"):
            issues.append(
                _issue(
                    "calm_zone_warning",
                    "warning",
                    f"Spread {n}: the {meta.get('calm_zone')} half is busy where the text sits.",
                    spread=n,
                    proposed_action="regen_spread_art",
                )
            )
    return issues


def cover_layout_issues(job_dir: Path) -> list[dict]:
    cover = read_json(Path(job_dir) / "cover" / "cover.json") or {}
    layout = cover.get("layout") or {}
    issues: list[dict] = []
    if layout.get("title_overlap") or cover.get("status") == "cover_title_overlap":
        issues.append(
            _issue(
                "cover_title_overlap",
                "error",
                "Front title would print over the child's face; stopped for the operator.",
            )
        )
    failures = list(layout.get("failures") or []) + list(layout.get("back_failures") or [])
    if failures:
        issues.append(
            _issue(
                "cover_layout_warning",
                "warning",
                "Cover art breaks the layout rules: " + "; ".join(failures),
                failures=failures,
            )
        )
    return issues


_FACE_SYSTEM = (
    "You locate faces in a children's book illustration (a cover or a wide double-page spread). Return JSON "
    '{"faces": [{"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}]} with one box per visible face, '
    "normalised 0-1 (x, y = top-left corner as a fraction of the image width / height). "
    'Return {"faces": []} when no face is visible.'
)


def detect_faces_local(image_path: Path) -> list[dict]:
    """Always-on OpenCV Haar face boxes (frontal + profile), normalised 0-1 on the full frame.

    Returns ``[]`` when no face is found (never ``None``). Raises only if the image cannot be opened;
    a missing OpenCV install returns ``[]`` so callers can still treat "no face" as a failure.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover - optional until opencv is installed
        return []
    path = str(image_path)
    image = cv2.imread(path)
    if image is None:
        from PIL import Image
        import numpy as np

        with Image.open(image_path) as pil:
            rgb = np.array(pil.convert("RGB"))
        image = rgb[:, :, ::-1].copy()  # RGB -> BGR
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    if w < 1 or h < 1:
        return []
    cascades = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        cascade_path = cv2.data.haarcascades + name
        cascade = cv2.CascadeClassifier(cascade_path)
        if not cascade.empty():
            cascades.append(cascade)
    boxes: list[tuple[int, int, int, int]] = []
    for cascade in cascades:
        for x, y, bw, bh in cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24)):
            boxes.append((int(x), int(y), int(bw), int(bh)))
    # Deduplicate heavily overlapping boxes (keep the larger).
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for box in boxes:
        x, y, bw, bh = box
        if any(
            abs(x - kx) < bw * 0.4 and abs(y - ky) < bh * 0.4
            for kx, ky, kbw, kbh in kept
        ):
            continue
        kept.append(box)
    return [
        {"x": x / w, "y": y / h, "w": bw / w, "h": bh / h}
        for x, y, bw, bh in kept
    ]


def detect_faces_vision(client, image_path: Path) -> list[dict] | None:
    """Vision face boxes; ``None`` = could not run. Second opinion only — local Haar is primary."""
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
                {"type": "text", "text": "Find every face in this illustration."},
                {"type": "image_url", "image_url": {"url": url}},
            ],
            description="Face position",
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
    except Exception:  # noqa: BLE001 - vision is advisory
        return None


def detect_faces(client, image_path: Path) -> list[dict] | None:
    """Face boxes for layout checks: local Haar first, vision as a second opinion.

    When OpenCV is available the local result is authoritative (including ``[]`` = no face,
    which is a FAILURE on a story spread, not a pass). Vision is only used when local found
    faces (merge extras) or when OpenCV is missing. Returns ``None`` only when both are
    unavailable.
    """
    try:
        import cv2  # noqa: F401

        has_local = True
    except ImportError:
        has_local = False
    local = detect_faces_local(image_path) if has_local else []
    vision = detect_faces_vision(client, image_path) if client is not None else None
    if has_local:
        if not local:
            return []  # definitive no-face; do not let vision override into a false pass
        if vision:
            # Second opinion: keep local boxes, append vision boxes that don't overlap heavily.
            merged = list(local)
            for face in vision:
                if not any(
                    abs(float(face.get("x", 0)) - float(m.get("x", 0))) < 0.08
                    and abs(float(face.get("y", 0)) - float(m.get("y", 0))) < 0.08
                    for m in merged
                ):
                    merged.append(face)
            return merged
        return local
    return vision


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
        f"spread {n}"
        for n in range(1, SPREAD_COUNT + 1)
        if not spread_art_path(job_dir, n).is_file()
    ]
    if missing:
        issues.append(_issue("art_missing", "error", f"{len(missing)} spread image(s) missing: {', '.join(missing[:4])}…"))

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
            issues.append(_issue("resolution", "error", f"Lowest spread is {lowest} ppi (< {PPI_FAIL}); no upscaler - regenerate larger."))
        elif lowest < PPI_FAIL:
            issues.append(_issue("resolution", "warning", f"Lowest spread is {lowest} ppi (< {PPI_FAIL}); continuing (ENFORCE_PRINT_PPI off)."))
        elif lowest < PPI_WARN:
            issues.append(_issue("resolution", "warning", f"Lowest spread is {lowest} ppi (< {PPI_WARN} target, >= {PPI_FAIL} minimum)."))

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

    issues.extend(spread_layout_issues(job_dir))
    issues.extend(cover_layout_issues(job_dir))

    return {
        "name": "structure",
        "passed": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "min_effective_ppi": lowest,
        "spread_box_mm": SPREAD_WIDTH_MM,
        "pdf": pdf_info,
    }


# --- 2. text guards -------------------------------------------------------------------------------


def text_guard_report(inputs: dict, story: dict) -> dict:
    title = get_title(inputs["title_id"])
    locked = story.get("locked") or {}
    candidate = {"spreads": story.get("spreads") or [], "locked": locked}
    from .catalogue import must_include_map

    issues = check_spreads(
        candidate,
        candidate,
        locked,
        child_name=inputs["name"],
        age_band=inputs.get("age_band") or title["age_band"],
        last_line=(title.get("last_line") or "").replace("{name}", inputs["name"]),
        must_include_by_spread=must_include_map(inputs["title_id"]),
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

    seen = ((review or {}).get("spreads") or {})
    reviewed = 0
    for n in range(1, SPREAD_COUNT + 1):
        meta = read_json(Path(job_dir) / "spreads" / f"spread-{n:02d}" / "spread.json") or {}
        if meta.get("anchor_state") is not None and meta["anchor_state"] != schedule[n - 1]:
            issues.append(_issue("anchor_state_mismatch", "error", f"Spread {n} was generated for state {meta['anchor_state']}, schedule says {schedule[n - 1]}.", spread=n))
        observed = (seen.get(str(n)) or {}).get("anchor_state_seen")
        if observed is None:
            continue
        reviewed += 1
        if int(observed) != schedule[n - 1]:
            issues.append(_issue("anchor_state_seen", "error", f"Spread {n}: reviewer saw state {observed}, schedule says {schedule[n - 1]}.", spread=n))
    if not reviewed:
        issues.append(_issue("anchor_not_reviewed", "warning", "No vision review of the anchor object yet (run Self revise)."))
    return {"name": "anchor_state", "passed": not any(i["severity"] == "error" for i in issues), "issues": issues}


# --- 4. face position -----------------------------------------------------------------------------


def _rects_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def face_position_report(job_dir: Path, review: dict | None) -> dict:
    """Text boxes (page mm, from layout.json) vs the reviewer's face boxes (composed-spread fractions).

    A face box is normalised on the whole spread; a text box lives on one page, so the right page's
    box is shifted by (spread width - page width) = 210 mm into spread coordinates.
    """
    layout = read_json(Path(job_dir) / "print" / "layout.json") or {}
    seen = ((review or {}).get("spreads") or {})
    shift = {"left": 0.0, "right": SPREAD_WIDTH_MM - PAGE_WITH_BLEED_MM}
    issues: list[dict] = []
    checked: set[int] = set()
    flagged: set[int] = set()
    for record in layout.get("pages") or []:
        box = record.get("box_mm")
        if record.get("kind") != "spread_half" or not box:
            continue
        n = int(record.get("spread") or 0)
        reviewed = seen.get(str(n))
        if reviewed is None:
            continue
        checked.add(n)
        if n in flagged:
            continue
        text_rect = (float(box[0]) + shift.get(record.get("side"), 0.0), float(box[1]), float(box[2]), float(box[3]))
        for face in reviewed.get("faces") or []:
            rect = (
                float(face.get("x", 0)) * SPREAD_WIDTH_MM,
                float(face.get("y", 0)) * SPREAD_HEIGHT_MM,
                float(face.get("w", 0)) * SPREAD_WIDTH_MM,
                float(face.get("h", 0)) * SPREAD_HEIGHT_MM,
            )
            if _rects_overlap(rect, text_rect):
                issues.append(_issue("text_over_face", "error", f"Spread {n}: text box overlaps a face.", spread=n))
                flagged.add(n)
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
