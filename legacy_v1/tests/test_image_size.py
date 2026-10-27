"""Spread image size: landscape ladder, crop-never-resize, effective_ppi events."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from emma_data import emma_bundle
from mock_openai import FakeImageEdit, image_result
from pipeline import constants, openai_util
from pipeline.constants import effective_ppi, resolution_status
from pipeline.illustrate import (
    PrintResolutionError,
    assert_print_resolution,
    generate_spread_art,
    min_effective_ppi,
)
from pipeline.job_log import read_events, read_json
from pipeline.pages import fit_to_spread, spread_art_path, spread_pages
from pipeline.constants import SPREAD_ASPECT, SPREAD_WIDTH_MM


@pytest.fixture(autouse=True)
def _reset_cache():
    openai_util.reset_size_cache()
    yield
    openai_util.reset_size_cache()


def test_fallback_order_is_largest_landscape_first():
    assert openai_util.IMAGE_SIZE_FALLBACK == ("2048x1024", "1792x1024", "1536x1024")
    assert "1024x1024" not in openai_util.IMAGE_SIZE_FALLBACK  # squares never on the spread ladder
    widths = [int(s.split("x")[0]) for s in openai_util.IMAGE_SIZE_FALLBACK]
    assert widths == sorted(widths, reverse=True)
    assert all(int(s.split("x")[0]) > int(s.split("x")[1]) for s in openai_util.IMAGE_SIZE_FALLBACK)  # landscape
    assert openai_util.SQUARE_SIZES[0] == "2048x2048"  # sheets / covers stay square


def test_largest_image_size_picks_largest_supported():
    assert openai_util.largest_image_size(requested="auto") == "2048x1024"
    assert openai_util.largest_image_size(requested="auto", supported={"1536x1024", "1792x1024"}) == "1792x1024"
    assert openai_util.largest_image_size(requested="auto", supported={"1536x1024"}) == "1536x1024"
    with pytest.raises(ValueError):
        openai_util.largest_image_size(requested="auto", supported={"512x512"})
    assert openai_util.largest_image_size(aspect="square") == "2048x2048"


def test_explicit_size_wins_over_fallback():
    assert openai_util.largest_image_size(requested="1536x1024") == "1536x1024"
    assert openai_util.image_size_candidates("1792x1024") == ["1792x1024"]


def test_square_requests_are_square():
    with patch.object(openai_util, "IMAGE_SIZE", "auto"):
        assert openai_util.square_image_size() == "square"
    with patch.object(openai_util, "IMAGE_SIZE", "1536x1536"):
        assert openai_util.square_image_size() == "1536x1536"
    with patch.object(openai_util, "IMAGE_SIZE", "2048x1024"):
        assert openai_util.square_image_size() == "square"


def test_image_quality_never_low_by_default():
    assert openai_util.image_quality(None) == constants.IMAGE_QUALITY == "high"
    assert openai_util.image_quality("bogus") == "high"
    assert openai_util.image_quality("medium") == "medium"


def test_image_quality_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("IMAGE_QUALITY", "medium")
    assert openai_util.image_quality(None) == "medium"
    monkeypatch.delenv("IMAGE_QUALITY")
    assert openai_util.image_quality(None) == "high"


class _SizeRejected(Exception):
    status_code = 400


def test_image_edit_steps_down_on_invalid_size(tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    seen = []

    def edit(**kwargs):
        seen.append(kwargs["size"])
        if int(kwargs["size"].split("x")[0]) > 1536:
            raise _SizeRejected(f"Invalid value for size: {kwargs['size']}")
        return image_result(64)

    client = MagicMock()
    client.images.edit.side_effect = edit
    with patch.object(openai_util, "IMAGE_SIZE", "auto"):
        openai_util.image_edit(client, ref, "prompt", size="auto", description="t")
    assert seen == ["2048x1024", "1792x1024", "1536x1024"]
    # the working size is remembered: no re-probing of the big ones
    seen.clear()
    openai_util.image_edit(client, ref, "prompt", size="auto", description="t")
    assert seen == ["1536x1024"]


def test_image_edit_accepts_multiple_references(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for path in (a, b):
        Image.new("RGB", (8, 8)).save(path)
    client = MagicMock()
    client.images.edit.return_value = image_result(64)
    openai_util.image_edit(client, [a, b], "p", size="1024x1024")
    assert isinstance(client.images.edit.call_args.kwargs["image"], list)
    assert len(client.images.edit.call_args.kwargs["image"]) == 2


def test_effective_ppi_in_216mm_box():
    assert constants.PAGE_WITH_BLEED_MM == 216.0
    assert effective_ppi(2551) == pytest.approx(300.0, abs=0.1)
    assert effective_ppi(2048) == pytest.approx(240.8, abs=0.1)
    assert effective_ppi(1024) == pytest.approx(120.4, abs=0.1)
    assert resolution_status(effective_ppi(2551)) == "ok"
    assert resolution_status(effective_ppi(2048)) == "warning"
    assert resolution_status(effective_ppi(1024)) == "fail"
    assert constants.PPI_WARN == 300 and constants.PPI_FAIL == 150


def test_fit_to_spread_crops_the_centre_and_never_resizes():
    from pipeline.pages import SpreadCropError

    # 1792x1024 needs ~11 % height crop (under the 15 % cap)
    frame = Image.new("RGB", (1792, 1024), (200, 200, 200))
    fitted = fit_to_spread(frame)
    assert fitted.size[0] == 1792
    assert fitted.size[1] == round(1792 / SPREAD_ASPECT)
    # 1536x1024 would need ~24 % — refused
    with pytest.raises(SpreadCropError):
        fit_to_spread(Image.new("RGB", (1536, 1024)))
    # a frame that is already the spread aspect (or wider) is not stretched or upscaled
    same = Image.new("RGB", (2840, 1440))
    assert fit_to_spread(same).size == (2840, 1440)


def _generate(tmp_path, size, n=1, monkeypatch=None):
    from pipeline import imagecheck, qa as qa_mod

    job_dir = tmp_path / "job"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    if monkeypatch is not None:
        monkeypatch.setattr(
            qa_mod, "detect_faces", lambda client, path: [{"x": 0.62, "y": 0.4, "w": 0.08, "h": 0.12}]
        )
        monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    else:
        # Module-level patch for fixtures that don't pass monkeypatch.
        pass
    fake = FakeImageEdit(size=size)
    with patch("pipeline.illustrate.image_edit", fake), patch(
        "pipeline.qa.detect_faces", lambda client, path: [{"x": 0.62, "y": 0.4, "w": 0.08, "h": 0.12}]
    ), patch("pipeline.imagecheck.head_cut_heuristic", lambda path: False):
        meta = generate_spread_art(
            MagicMock(), job_dir, {**spread_pages(pages)[n], "spread": n}, inputs, story, character
        )
    return job_dir, meta, fake


def test_one_image_call_per_spread_with_sheets_only(tmp_path):
    job_dir, meta, fake = _generate(tmp_path, 3408)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["description"] == "Spread 1"
    assert call["names"] == ["character.png"]
    assert "ONE continuous double-page" in call["prompt"]
    assert spread_art_path(job_dir, 1).is_file()
    assert not (job_dir / "spreads" / "spread-01" / "art-left.png").exists()
    assert meta["attempts"] == 1


def test_native_pixels_cropped_never_resized_and_ppi_recorded(tmp_path):
    job_dir, meta, _fake = _generate(tmp_path, 3408)  # model frame 3408 x 1917
    with Image.open(spread_art_path(job_dir, 1)) as image:
        width, height = image.size
    assert width == 3408  # full width kept: no upscale, no downscale
    assert width / height == pytest.approx(SPREAD_ASPECT, abs=0.01)
    saved = read_json(job_dir / "spreads" / "spread-01" / "spread.json")
    assert saved["effective_ppi"] == pytest.approx(effective_ppi(3408, SPREAD_WIDTH_MM), abs=0.1)
    assert saved["effective_ppi"] == pytest.approx(203.2, abs=0.2)
    assert saved["size_received"] == "3408x1917"
    assert saved["crop_box"][0] == 0 and saved["crop_box"][2] == 3408
    assert saved["status"] == "generated" and saved["resolution_status"] == "warning"
    assert min_effective_ppi(job_dir, [1]) == pytest.approx(saved["effective_ppi"], abs=0.1)


def test_warning_event_below_300_ppi(tmp_path):
    job_dir, _meta, _fake = _generate(tmp_path, 3408)
    types = [e["type"] for e in read_events(job_dir)]
    assert types.count("print_resolution_warning") == 1
    assert "print_resolution_fail" not in types
    assert assert_print_resolution(job_dir, [1]) == pytest.approx(203.2, abs=0.2)


def test_no_warning_at_300_ppi(tmp_path):
    job_dir, _meta, _fake = _generate(tmp_path, 5200)  # 5200 px over 426 mm = 310 ppi
    types = [e["type"] for e in read_events(job_dir)]
    assert "print_resolution_warning" not in types and "print_resolution_fail" not in types


def test_fail_event_and_gate_below_150_ppi(tmp_path, monkeypatch):
    job_dir, meta, _fake = _generate(tmp_path, 2048)  # 2048 px over 426 mm = 122 ppi
    types = [e["type"] for e in read_events(job_dir)]
    assert types.count("print_resolution_fail") == 1
    assert meta["resolution_status"] == "fail"
    # Default: gate is off - still records fail status but does not raise.
    assert assert_print_resolution(job_dir, [1]) == pytest.approx(122.1, abs=0.2)
    monkeypatch.setattr("pipeline.constants.ENFORCE_PRINT_PPI", True)
    with pytest.raises(PrintResolutionError):
        assert_print_resolution(job_dir, [1])
