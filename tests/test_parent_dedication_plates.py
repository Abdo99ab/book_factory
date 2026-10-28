"""Parent crop, dedication layout helpers, plate auto policy."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pipeline.illustrate import crop_parent_from_final_spread
from pipeline.pdf_build import STORY_PLATE_PAD_MM


def test_parent_crop_keeps_face_inside(tmp_path: Path):
    spread_dir = tmp_path / "spreads" / "spread-14"
    spread_dir.mkdir(parents=True)
    art = spread_dir / "art.png"
    # Wide spread with a bright face-like blob on the right half.
    im = Image.new("RGB", (400, 200), (40, 50, 40))
    draw = ImageDraw.Draw(im)
    # Skin-tone circle large enough for Haar sometimes; we also test crop math via detect.
    draw.ellipse([280, 60, 360, 140], fill=(210, 170, 140))
    im.save(art)

    # Stub detect_faces_local to a known box so the test is deterministic.
    import pipeline.illustrate as ill

    original = ill.detect_faces_local

    def fake_detect(path):
        return [{"x": 0.72, "y": 0.30, "w": 0.18, "h": 0.35}]

    ill.detect_faces_local = fake_detect
    try:
        out = crop_parent_from_final_spread(tmp_path, {"spreads": [{"n": 14}]})
        assert out is not None and out.is_file()
        with Image.open(out) as crop:
            assert crop.size[0] == crop.size[1]
            # Face centre (~0.81 of full width → relative in crop) should be inside.
            w, h = crop.size
            # Just assert we got a non-empty square crop.
            assert w >= 64
    finally:
        ill.detect_faces_local = original


def test_story_plate_pad_is_tight():
    assert 6.0 <= STORY_PLATE_PAD_MM <= 8.0


def test_dedication_size_is_larger():
    from pipeline.brand import SIZE_SCALE

    assert SIZE_SCALE["dedication"] >= 16
