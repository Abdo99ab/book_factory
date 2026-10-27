"""Step 8 — largest_image_size fallback order, native pixels, effective_ppi events."""

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
from pipeline.pages import spread_art_path, spread_pages


@pytest.fixture(autouse=True)
def _reset_cache():
    openai_util.reset_size_cache()
    yield
    openai_util.reset_size_cache()


def test_fallback_order_is_largest_first():
    assert openai_util.IMAGE_SIZE_FALLBACK[0] == "4096x4096"
    assert openai_util.IMAGE_SIZE_FALLBACK[-1] == "1024x1024"
    sizes = [int(s.split("x")[0]) for s in openai_util.IMAGE_SIZE_FALLBACK]
    assert sizes == sorted(sizes, reverse=True)


def test_largest_image_size_picks_largest_supported():
    assert openai_util.largest_image_size("auto") == "4096x4096"
    assert openai_util.largest_image_size("auto", supported={"1024x1024", "2048x2048"}) == "2048x2048"
    assert openai_util.largest_image_size("auto", supported={"1024x1024"}) == "1024x1024"
    with pytest.raises(ValueError):
        openai_util.largest_image_size("auto", supported={"512x512"})


def test_explicit_size_wins_over_fallback():
    assert openai_util.largest_image_size("1536x1536") == "1536x1536"


def test_image_quality_never_low_by_default():
    assert openai_util.image_quality(None) == constants.IMAGE_QUALITY == "high"
    assert openai_util.image_quality("bogus") == "high"
    assert openai_util.image_quality("medium") == "medium"


class _SizeRejected(Exception):
    status_code = 400


def test_image_edit_steps_down_on_invalid_size(tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    seen = []

    def edit(**kwargs):
        seen.append(kwargs["size"])
        if int(kwargs["size"].split("x")[0]) > 2048:
            raise _SizeRejected(f"Invalid value for size: {kwargs['size']}")
        return image_result(64)

    client = MagicMock()
    client.images.edit.side_effect = edit
    with patch.object(openai_util, "IMAGE_SIZE", "auto"):
        openai_util.image_edit(client, ref, "prompt", size="auto", description="t")
    assert seen == ["4096x4096", "3072x3072", "2048x2048"]
    # the working size is remembered: no re-probing of the big ones
    seen.clear()
    openai_util.image_edit(client, ref, "prompt", size="auto", description="t")
    assert seen == ["2048x2048"]


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


def _generate(tmp_path, size, n=1):
    job_dir = tmp_path / "job"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    fake = FakeImageEdit(size=size)
    with patch("pipeline.illustrate.image_edit", fake):
        meta = generate_spread_art(
            MagicMock(), job_dir, {**spread_pages(pages)[n], "spread": n}, inputs, story, character
        )
    return job_dir, meta, fake


def test_pair_generated_left_then_right_with_left_as_reference(tmp_path):
    job_dir, meta, fake = _generate(tmp_path, 2048)
    assert len(fake.calls) == 2
    left_call, right_call = fake.calls
    assert "This is the LEFT page" in left_call["prompt"]
    assert "This is the RIGHT page" in right_call["prompt"]
    assert "art-left.png" not in left_call["names"]
    assert right_call["names"][0] == "character.png"
    assert "art-left.png" in right_call["names"]
    assert spread_art_path(job_dir, 1, "left").is_file()
    assert spread_art_path(job_dir, 1, "right").is_file()


def test_native_pixels_never_resized_and_ppi_recorded(tmp_path):
    job_dir, meta, _fake = _generate(tmp_path, 2048)
    for side in ("left", "right"):
        with Image.open(spread_art_path(job_dir, 1, side)) as image:
            assert image.size == (2048, 2048)  # no upscale, no downscale
    saved = read_json(job_dir / "spreads" / "spread-01" / "spread.json")
    assert saved["effective_ppi"] == pytest.approx(240.8, abs=0.1)
    assert saved["pages"]["left"]["effective_ppi"] == saved["pages"]["right"]["effective_ppi"]
    assert saved["pages"]["left"]["width"] == 2048
    assert saved["status"] == "generated" and saved["resolution_status"] == "warning"
    assert min_effective_ppi(job_dir, [1]) == saved["effective_ppi"]


def test_warning_event_below_300_ppi(tmp_path):
    job_dir, _meta, _fake = _generate(tmp_path, 2048)
    types = [e["type"] for e in read_events(job_dir)]
    assert types.count("print_resolution_warning") == 2
    assert "print_resolution_fail" not in types
    assert assert_print_resolution(job_dir, [1]) == pytest.approx(240.8, abs=0.1)


def test_no_warning_at_300_ppi(tmp_path):
    job_dir, _meta, _fake = _generate(tmp_path, 2551)
    types = [e["type"] for e in read_events(job_dir)]
    assert "print_resolution_warning" not in types and "print_resolution_fail" not in types


def test_fail_event_and_gate_below_150_ppi(tmp_path, monkeypatch):
    job_dir, meta, _fake = _generate(tmp_path, 1024)
    types = [e["type"] for e in read_events(job_dir)]
    assert types.count("print_resolution_fail") == 2
    assert meta["resolution_status"] == "fail"
    # Default: gate is off — still records fail status but does not raise.
    assert assert_print_resolution(job_dir, [1]) == pytest.approx(120.4, abs=0.1)
    monkeypatch.setattr("pipeline.constants.ENFORCE_PRINT_PPI", True)
    with pytest.raises(PrintResolutionError):
        assert_print_resolution(job_dir, [1])
