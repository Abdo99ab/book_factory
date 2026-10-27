"""Round-3 PDF defects: landscape-only spreads, face/head-cut, must_include, centred text."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from emma_data import emma_bundle
from mock_openai import FakeImageEdit, image_result
from pipeline import imagecheck, openai_util, qa
from pipeline.catalogue import load_story_beats, must_include_map
from pipeline.illustrate import SpreadSizeError, generate_spread_art
from pipeline.job_log import read_events
from pipeline.pages import SpreadCropError, fit_crop_box, fit_to_spread, spread_pages
from pipeline.pdf_build import fit_lines_for_story, story_text_box_mm
from pipeline.text_guards import check_spreads, forbidden_prop_issues, must_include_issues
from pipeline.fonts import register_brand_fonts, reportlab_name
from pipeline.constants import PAGE_WITH_BLEED_MM, BLEED_MM
from pipeline.brand import SAFE_MM


@pytest.fixture(autouse=True)
def _reset_cache():
    openai_util.reset_size_cache()
    yield
    openai_util.reset_size_cache()


# --- MAJOR 1: landscape only ----------------------------------------------------------------------


def test_landscape_ladder_has_no_square():
    assert "1024x1024" not in openai_util.LANDSCAPE_SIZES
    assert all(not openai_util._is_square_size(s) for s in openai_util.LANDSCAPE_SIZES)
    assert openai_util.IMAGE_SIZE_FALLBACK == ("2048x1024", "1792x1024", "1536x1024")


def test_square_is_never_cached_as_landscape_working_size(tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    seen = []

    def edit(**kwargs):
        seen.append(kwargs["size"])
        if kwargs["size"] != "1536x1024":
            err = Exception(f"Invalid value for size: {kwargs['size']}")
            err.status_code = 400  # type: ignore[attr-defined]
            raise err
        return image_result(64)

    client = MagicMock()
    client.images.edit.side_effect = edit
    with patch.object(openai_util, "IMAGE_SIZE", "auto"):
        openai_util.image_edit(client, ref, "p", size="auto", description="t")
    assert "1024x1024" not in seen
    assert openai_util._WORKING_SIZE.get((openai_util.IMAGE_MODEL, "landscape")) == "1536x1024"
    events = openai_util.take_size_events()
    assert events and events[0]["type"] == "image_size_stepdown"


def test_fit_to_spread_refuses_heavy_height_crop():
    # 1536x1024 -> ~24% height crop to 1.97:1 → raise
    with pytest.raises(SpreadCropError):
        fit_crop_box((1536, 1024))
    # 1792x1024 is ~11% → ok
    box = fit_crop_box((1792, 1024))
    assert box[0] == 0 and box[2] == 1792
    fitted = fit_to_spread(Image.new("RGB", (1792, 1024)))
    assert fitted.size[0] == 1792


def test_square_frame_is_rejected_as_spread_size(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    monkeypatch.setattr(qa, "detect_faces", lambda client, path: [{"x": 0.7, "y": 0.4, "w": 0.1, "h": 0.12}])
    monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    fake = FakeImageEdit(size=1024)  # square 1024x1024 from mock when size is square-ish
    # Force the fake to return a square PNG regardless of requested size.
    buf_calls = {"n": 0}

    def square_edit(client, image_path, prompt, size=None, quality=None, description=""):
        buf_calls["n"] += 1
        from io import BytesIO
        import base64
        from types import SimpleNamespace

        image = Image.new("RGB", (1024, 1024), (200, 180, 160))
        raw = BytesIO()
        image.save(raw, format="PNG")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw.getvalue()).decode())])

    with patch("pipeline.illustrate.image_edit", square_edit):
        with pytest.raises(SpreadSizeError):
            generate_spread_art(
                MagicMock(),
                job_dir,
                {**spread_pages(pages)[1], "spread": 1},
                inputs,
                story,
                character,
            )
    types = [e["type"] for e in read_events(job_dir)]
    assert "spread_size_rejected" in types
    assert buf_calls["n"] >= 3


# --- MAJOR 2: faces / head_cut --------------------------------------------------------------------


def _face_fixture(tmp_path, *, top_frac=0.4, with_face=True) -> Path:
    path = tmp_path / "frame.png"
    image = Image.new("RGB", (400, 200), (240, 230, 210))
    draw = ImageDraw.Draw(image)
    if with_face:
        y0 = int(200 * top_frac)
        # Skin-tone blob where a head would be.
        draw.ellipse((160, y0, 240, y0 + 50), fill=(210, 160, 130))
        draw.ellipse((175, y0 + 15, 190, y0 + 28), fill=(40, 30, 30))
        draw.ellipse((210, y0 + 15, 225, y0 + 28), fill=(40, 30, 30))
    image.save(path)
    return path


def test_head_cut_heuristic_flags_mass_in_top_band(tmp_path):
    # Skin-tone band on the top edge against a cool (non-skin) wall — a cut-off head.
    high = tmp_path / "cut.png"
    image = Image.new("RGB", (400, 200), (180, 200, 210))  # cool wall, not skin
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 400, 12), fill=(210, 150, 120))
    image.save(high)
    assert imagecheck.head_cut_heuristic(high) is True
    plain = tmp_path / "plain.png"
    Image.new("RGB", (400, 200), (180, 200, 210)).save(plain)
    assert imagecheck.head_cut_heuristic(plain) is False


def test_no_face_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    job_dir = tmp_path / "noface"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    monkeypatch.setattr(qa, "detect_faces", lambda client, path: [])
    monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    fake = FakeImageEdit(size=3408)
    with patch("pipeline.illustrate.image_edit", fake):
        meta = generate_spread_art(
            MagicMock(), job_dir, {**spread_pages(pages)[1], "spread": 1}, inputs, story, character
        )
    assert len(fake.calls) == 2  # one regen
    assert "no_face" in meta["fold_failures"]
    assert meta["no_face_warning"] is True
    assert "no_face_warning" in [e["type"] for e in read_events(job_dir)]


def test_clean_face_passes_layout(tmp_path, monkeypatch):
    job_dir = tmp_path / "ok"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    monkeypatch.setattr(
        qa, "detect_faces", lambda client, path: [{"x": 0.62, "y": 0.4, "w": 0.08, "h": 0.12}]
    )
    monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    fake = FakeImageEdit(size=3408)
    with patch("pipeline.illustrate.image_edit", fake):
        meta = generate_spread_art(
            MagicMock(), job_dir, {**spread_pages(pages)[1], "spread": 1}, inputs, story, character
        )
    assert meta["fold_failures"] == [] and meta.get("no_face_warning") is False


# --- MAJOR 3: must_include ------------------------------------------------------------------------


def test_must_include_parsed_from_beats():
    beats = load_story_beats("big_kid_list")
    by_n = {b["n"]: b for b in beats["beats"]}
    assert "Not today" in by_n[6]["must_include"]
    assert "potty" in by_n[11]["must_include"]
    assert must_include_map("big_kid_list")[11]


def test_spread_11_text_without_potty_fails():
    required = must_include_map("big_kid_list")
    issues = must_include_issues(
        [{"n": 11, "text": "Emma and Mum sing the hand-wash song together to twenty."}],
        required,
        field="text",
    )
    assert any(i["code"] == "must_include" and "potty" in i["message"] for i in issues)


def test_dialogue_must_include_skipped_on_scene_but_prop_still_required():
    """Spoken tokens (quoted in the beat) are text-only; drawable props stay scene-hard."""
    from pipeline.catalogue import beat_text_map
    from pipeline.text_guards import drawable_must_include, is_dialogue_must_include

    beats = {b["n"]: b for b in load_story_beats("big_kid_list")["beats"]}
    beat6 = beats[6]["text"]
    assert is_dialogue_must_include("Not today", beat6) is True
    assert is_dialogue_must_include("potty", beat6) is False
    assert drawable_must_include(["Not today", "potty"], beat6) == ["potty"]

    required = must_include_map("big_kid_list")
    beat_texts = beat_text_map("big_kid_list")
    # Scene without the spoken line — not an error.
    no_dialogue = must_include_issues(
        [
            {
                "n": 6,
                "scene": "On the left a quiet bathroom; on the right Emma looks at the potty.",
            }
        ],
        required,
        field="scene",
        beat_texts=beat_texts,
    )
    assert no_dialogue == []
    # Scene missing the drawable prop — still an error.
    no_potty = must_include_issues(
        [
            {
                "n": 6,
                "scene": "On the left a quiet hall; on the right Emma shakes her head.",
            }
        ],
        required,
        field="scene",
        beat_texts=beat_texts,
    )
    assert any(i["code"] == "must_include" and "potty" in i["message"] for i in no_potty)
    assert all("Not today" not in i["message"] for i in no_potty)
    # Text still requires the dialogue token.
    text_miss = must_include_issues(
        [{"n": 6, "text": "Emma looks at the potty."}],
        required,
        field="text",
    )
    assert any("Not today" in i["message"] for i in text_miss)


def test_scene_with_guitar_fails():
    issues = forbidden_prop_issues(
        [{"n": 11, "scene": "Mum plays a guitar in the living room on the right half."}]
    )
    assert issues and issues[0]["code"] == "forbidden_prop" and "guitar" in issues[0]["message"]


# --- Story text layout ----------------------------------------------------------------------------


def test_story_text_box_is_centred_on_calm_page():
    for lines_h in (18.0, 42.0):  # ~3-line and ~6-line blocks
        for side in ("left", "right"):
            left, top, width, height = story_text_box_mm(side, lines_h)
            top_safe = BLEED_MM + SAFE_MM["trim"]
            bottom_safe = PAGE_WITH_BLEED_MM - BLEED_MM - SAFE_MM["trim"]
            centre_y = top + height / 2
            safe_centre = (top_safe + bottom_safe) / 2
            assert abs(centre_y - safe_centre) <= 1.0
            box_left, box_w = left, width
            from pipeline.pdf_build import _page_text_box

            col_l, col_w = _page_text_box(side)
            assert abs((box_left + box_w / 2) - (col_l + col_w / 2)) <= 0.1


def test_line_fit_rebalances_orphan_on_long_sentence():
    register_brand_fonts()
    font = reportlab_name("body")
    text = "It has five pictures: shoes, a coat, a cup, hands and a potty."
    width_pt = 180 * 72 / 25.4  # 180 mm
    lines = fit_lines_for_story(text, font, 23, width_pt)
    assert len(lines) == 2
    assert all(len(line.split()) >= 3 for line in lines)


def test_calm_zone_box_is_centred():
    left = imagecheck.calm_zone_box("left")
    right = imagecheck.calm_zone_box("right")
    # Centre of left half ≈ 0.25; box spans ~0.1..0.4
    assert abs((left[0] + left[2]) / 2 - 0.25) < 0.02
    assert abs((right[0] + right[2]) / 2 - 0.75) < 0.02
    assert abs((left[1] + left[3]) / 2 - 0.5) < 0.02
