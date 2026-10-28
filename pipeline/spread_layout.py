"""Keep faces clear of the centre fold (middle 10% of spread width)."""

from __future__ import annotations

from pathlib import Path

from .cover_layout import detect_faces_local, union_face_box
from .image_extend import apply_shift_only
from .job_log import append_event

FOLD_HALF = 0.05  # middle 10% → 45%–55%
MAX_FOLD_SHIFT = 0.20
CALM_MARGIN = 0.08  # don't shove faces into the outer calm strip


def fold_zone() -> tuple[float, float]:
    return 0.5 - FOLD_HALF, 0.5 + FOLD_HALF


def faces_in_fold(faces: list[dict]) -> list[dict]:
    left, right = fold_zone()
    hit = []
    for f in faces or []:
        x0 = float(f["x"])
        x1 = x0 + float(f["w"])
        if x1 > left and x0 < right:
            hit.append(f)
    return hit


def check_fold_clear(faces: list[dict] | None) -> dict:
    if faces is None:
        return {"passed": True, "failures": [], "face_checked": False, "in_fold": []}
    in_fold = faces_in_fold(faces)
    failures = []
    if in_fold:
        failures.append(f"face: {len(in_fold)} face(s) overlap the centre fold (middle 10%)")
    return {
        "passed": not failures,
        "failures": failures,
        "face_checked": True,
        "in_fold": in_fold,
        "union": union_face_box(faces) if faces else None,
    }


def shift_needed_to_clear_fold(faces: list[dict], calm_zone: str) -> tuple[float, bool]:
    """Return (shift_x fraction, too_far). Prefer moving faces away from the calm side."""
    in_fold = faces_in_fold(faces)
    if not in_fold:
        return 0.0, False
    union = union_face_box(in_fold)
    assert union is not None
    left, right = fold_zone()
    cx = float(union["x"]) + float(union["w"]) / 2
    # Move the union fully outside the fold band.
    if calm_zone == "right":
        # Prefer shifting left (faces toward left page), away from right calm text.
        target_right = left - 0.01
        shift = target_right - (float(union["x"]) + float(union["w"]))
        # If that would push into the left edge badly, try the other way.
        new_left = float(union["x"]) + shift
        if new_left < CALM_MARGIN:
            target_left = right + 0.01
            shift = target_left - float(union["x"])
    else:
        # Calm on left → prefer shift right.
        target_left = right + 0.01
        shift = target_left - float(union["x"])
        new_right = float(union["x"]) + float(union["w"]) + shift
        if new_right > 1.0 - CALM_MARGIN:
            target_right = left - 0.01
            shift = target_right - (float(union["x"]) + float(union["w"]))
    if abs(shift) > MAX_FOLD_SHIFT:
        return shift, True
    # Also bail if centre barely moves but still overlapping after tiny nudge.
    if abs(shift) < 0.005:
        # Nudge based on which half of the fold the centre sits in.
        shift = -0.08 if cx >= 0.5 else 0.08
        if abs(shift) > MAX_FOLD_SHIFT:
            return shift, True
    return shift, False


def enforce_spread_fold(
    client,
    job_dir: Path,
    path: Path,
    calm_zone: str,
    *,
    regenerate_fn,
    n: int,
) -> dict:
    """Detect fold overlap → sideways extend-shift → one regen → warn. Never fails the job."""
    faces = detect_faces_local(path)
    append_event(
        job_dir,
        "spread_faces",
        f"Spread {n}: detected {len(faces)} face box(es).",
        spread=n,
        faces=faces,
    )
    check = check_fold_clear(faces)
    layout = {"passed": check["passed"], "warning": False, "attempts": [{"stage": "initial", "faces": faces, "check": check}]}
    if check["passed"]:
        return layout

    if faces:
        shift_x, too_far = shift_needed_to_clear_fold(faces, calm_zone)
        if not too_far and abs(shift_x) >= 0.005:
            result = apply_shift_only(path, shift_x, 0.0, client=client)
            faces = detect_faces_local(path)
            check = check_fold_clear(faces)
            layout["attempts"].append(
                {
                    "stage": "fold_shift",
                    "faces": faces,
                    "check": check,
                    "shift_x": shift_x,
                    "extended": result.get("extended"),
                }
            )
            append_event(
                job_dir,
                "spread_fold_shift",
                f"Spread {n}: shifted dx={shift_x:.2f} to clear fold.",
                spread=n,
            )
            if check["passed"]:
                layout["passed"] = True
                layout["shifted"] = True
                return layout
        elif too_far:
            append_event(
                job_dir,
                "spread_fold_too_far",
                f"Spread {n}: need dx={shift_x:.2f}; regenerating once.",
                spread=n,
            )

    regenerate_fn(path)
    faces = detect_faces_local(path)
    check = check_fold_clear(faces)
    layout["attempts"].append({"stage": "regen", "faces": faces, "check": check})
    append_event(job_dir, "spread_faces", f"Spread {n} after regen: {len(faces)} face(s).", spread=n, faces=faces)
    if not check["passed"] and faces:
        shift_x, too_far = shift_needed_to_clear_fold(faces, calm_zone)
        if not too_far and abs(shift_x) >= 0.005:
            apply_shift_only(path, shift_x, 0.0, client=client)
            faces = detect_faces_local(path)
            check = check_fold_clear(faces)
            layout["attempts"].append({"stage": "shift_after_regen", "faces": faces, "check": check})
    if check["passed"]:
        layout["passed"] = True
        layout["regenerated"] = True
        return layout

    layout["passed"] = False
    layout["warning"] = True
    append_event(
        job_dir,
        "spread_layout_warning",
        f"Spread {n}: face still on the centre fold after shift/regen — human check needed. "
        f"Failures: {'; '.join(check.get('failures') or [])}",
        spread=n,
        failures=check.get("failures"),
    )
    return layout
