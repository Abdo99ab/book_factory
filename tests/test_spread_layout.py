"""Centre-fold face clearance."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pipeline.spread_layout import (
    check_fold_clear,
    faces_in_fold,
    shift_needed_to_clear_fold,
)
from pipeline.image_extend import apply_shift_only


def test_faces_in_fold_detected():
    faces = [{"x": 0.46, "y": 0.3, "w": 0.08, "h": 0.1}]
    assert faces_in_fold(faces)
    check = check_fold_clear(faces)
    assert check["passed"] is False


def test_faces_clear_of_fold_pass():
    faces = [{"x": 0.15, "y": 0.3, "w": 0.12, "h": 0.12}]
    assert check_fold_clear(faces)["passed"] is True


def test_shift_needed_moves_face_off_fold():
    faces = [{"x": 0.46, "y": 0.3, "w": 0.08, "h": 0.1}]
    shift, too_far = shift_needed_to_clear_fold(faces, calm_zone="right")
    assert not too_far
    assert abs(shift) > 0.01
    moved = [{"x": faces[0]["x"] + shift, "y": faces[0]["y"], "w": faces[0]["w"], "h": faces[0]["h"]}]
    assert check_fold_clear(moved)["passed"] is True


def test_apply_shift_only_no_scale(tmp_path: Path):
    path = tmp_path / "art.png"
    im = Image.new("RGB", (200, 100), (90, 120, 90))
    # Distinct left/right colours so a horizontal shift is visible.
    for x in range(100, 200):
        for y in range(100):
            im.putpixel((x, y), (200, 80, 60))
    im.save(path)
    before = path.read_bytes()
    result = apply_shift_only(path, 0.1, 0.0, client=None)
    assert result["shifted"] is True
    assert path.read_bytes() != before
    with Image.open(path) as out:
        assert out.size == (200, 100)
