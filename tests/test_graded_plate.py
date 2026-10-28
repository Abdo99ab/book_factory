"""Graded story-text plates: always present; opacity from calm-zone contrast."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pipeline.constants import STORY_PLATE_ALPHA_MAX, STORY_PLATE_ALPHA_MIN
from pipeline.pdf_build import (
    CONTRAST_TARGET,
    _Art,
    contrast_with_white_plate,
    resolve_story_plate_alpha,
)


def _art_from_colour(tmp_path: Path, rgb: tuple[int, int, int], tag: str) -> _Art:
    path = tmp_path / f"{tag}.png"
    Image.new("RGB", (200, 200), rgb).save(path)
    return _Art(path, tmp_path / "work", None, tag=tag)


def test_plate_alpha_constants_in_faint_to_strong_range():
    assert 0.15 <= STORY_PLATE_ALPHA_MIN <= 0.25
    assert 0.60 <= STORY_PLATE_ALPHA_MAX <= 0.75
    assert STORY_PLATE_ALPHA_MIN < STORY_PLATE_ALPHA_MAX


def test_high_contrast_calm_zone_gets_minimum_plate(tmp_path: Path):
    # Near-white art → navy ink already passes; plate stays at the faint minimum.
    art = _art_from_colour(tmp_path, (250, 246, 239), "light")
    art.rect_mm = (0, 0, 210, 210)
    box = (20, 40, 190, 120)
    alpha, raw = resolve_story_plate_alpha(art, box)
    assert raw >= CONTRAST_TARGET
    assert abs(alpha - STORY_PLATE_ALPHA_MIN) < 1e-6
    assert contrast_with_white_plate(art.stats(box)["p10"], alpha) >= CONTRAST_TARGET


def test_low_contrast_calm_zone_gets_stronger_plate(tmp_path: Path):
    # Dark teal art → navy fails bare; plate opacity rises and contrast passes.
    art = _art_from_colour(tmp_path, (15, 82, 87), "dark")
    art.rect_mm = (0, 0, 210, 210)
    box = (20, 40, 190, 120)
    alpha, raw = resolve_story_plate_alpha(art, box)
    assert raw < CONTRAST_TARGET
    assert alpha > STORY_PLATE_ALPHA_MIN
    assert alpha <= STORY_PLATE_ALPHA_MAX + 1e-9
    assert contrast_with_white_plate(art.stats(box)["p10"], alpha) >= CONTRAST_TARGET


def test_contrast_with_white_plate_improves_with_opacity():
    p10 = 0.15
    assert contrast_with_white_plate(p10, 0.2) < contrast_with_white_plate(p10, 0.7)
    assert contrast_with_white_plate(p10, 1.0) > contrast_with_white_plate(p10, 0.0)
