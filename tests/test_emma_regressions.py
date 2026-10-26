"""Step 13 - Emma-build regressions (spec section 16) with mocked OpenAI and a fictional order.

Runs the real illustrate -> PDF -> QA path end to end. Only the OpenAI image call is replaced by
fixed PNGs (tests/fixtures); no network, no real child photo, no real order.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader

from emma_data import emma_bundle, persist_emma_job
from pipeline import constants, illustrate, pdf_build, qa, runner
from pipeline.catalogue import get_title
from pipeline.constants import (
    BLEED_MM,
    COVER_HEIGHT_MM,
    COVER_WIDTH_MM,
    PAGE_PX,
    PAGE_WITH_BLEED_MM,
    PPI_FAIL,
    SPINE_MM,
    WRAP_MM,
    effective_ppi,
    mm_to_pt,
)
from pipeline.job_log import read_json, write_json
from pipeline.openai_util import reset_size_cache

FIXTURES = Path(__file__).parent / "fixtures"
CREAM = (255, 250, 240)
RUST = (169, 80, 47)  # next_steps family colour (Big-Kid List)


class FixedPngImageEdit:
    """Stands in for openai_util.image_edit: returns committed fixture PNGs, records every call."""

    def __init__(self):
        self.calls: list[dict] = []
        self._cycle = [
            (FIXTURES / "page_light.png").read_bytes(),
            (FIXTURES / "page_warm.png").read_bytes(),
            (FIXTURES / "page_dark_lower.png").read_bytes(),
        ]
        self._cover = (FIXTURES / "cover_art.png").read_bytes()

    def __call__(self, client, image_path, prompt, size=None, quality=None, description=""):
        paths = list(image_path) if isinstance(image_path, (list, tuple)) else [image_path]
        self.calls.append({"names": [Path(p).name for p in paths], "prompt": prompt, "description": description})
        data = self._cover if "cover" in description.lower() else self._cycle[len(self.calls) % 3]
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data).decode("ascii"))])


@pytest.fixture(scope="module")
def emma_build(tmp_path_factory):
    """Run the real spread/cover/PDF pipeline on the fictional Emma order, OpenAI mocked."""
    mp = pytest.MonkeyPatch()
    reset_size_cache()
    fake = FixedPngImageEdit()
    mp.setattr(illustrate, "image_edit", fake)
    mp.setattr(illustrate, "IMAGE_SIZE", "auto")

    job_dir = tmp_path_factory.mktemp("emma") / ("f" * 32)
    inputs, story, pages = emma_bundle(job_dir)
    (job_dir / "character").mkdir(parents=True, exist_ok=True)
    (job_dir / "character" / "character.png").write_bytes((FIXTURES / "page_light.png").read_bytes())
    write_json(job_dir / "inputs.json", inputs)
    write_json(job_dir / "job.json", {"job_id": job_dir.name, "name": "Emma", "status": "generating_pages", "pdf": None})
    write_json(job_dir / "story" / "rebuilt_story.json", story)

    pdf = runner._illustrate_and_build(MagicMock(), job_dir, inputs, story, pages, job_dir / "character" / "character.png")
    built_job = read_json(job_dir / "job.json")  # carries min_effective_ppi from the runner
    persist_emma_job(job_dir, inputs, story, pages, pdf)
    write_json(job_dir / "job.json", {**built_job, **read_json(job_dir / "job.json")})
    yield SimpleNamespace(job_dir=job_dir, inputs=inputs, story=story, pages=pages, pdf=pdf, fake=fake)
    mp.undo()


def _text(pdf, index):
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return doc[index].get_textpage().get_text_bounded()
    finally:
        doc.close()


def _first_interior(pdf) -> int:
    return 1 + (1 if constants.PRINTER.get("endpapers_in_pdf") and constants.PRINTER["packaging"] == "single_pdf_cover_flat_first" else 0)


# --- 1. page map (Emma bug 2: dedication on page 2) ---------------------------------------------------


def test_page_map_is_deterministic_32_pages(emma_build):
    pages = {p["number"]: p for p in emma_build.pages}
    assert sorted(pages) == list(range(1, 33))
    assert pages[1]["kind"] == "title"
    for n in range(1, 15):
        assert pages[2 * n]["kind"] == pages[2 * n + 1]["kind"] == "spread_half"
        assert (pages[2 * n]["spread"], pages[2 * n]["side"]) == (n, "left")
        assert (pages[2 * n + 1]["spread"], pages[2 * n + 1]["side"]) == (n, "right")
    assert (pages[30]["kind"], pages[31]["kind"]) == ("parent_left", "parent_right")
    assert pages[32]["kind"] == "dedication"
    assert pages[2]["kind"] != "dedication"


def test_dedication_is_page_32_and_real_not_placeholder(emma_build):
    start = _first_interior(emma_build.pdf)
    assert "For Emma, from Mum and Dad" in _text(emma_build.pdf, start + 31)
    assert "with love" not in _text(emma_build.pdf, start + 1)


# --- 2. 14 spreads as 28 images in pairs (Emma bug 3) --------------------------------------------------------------


def test_28_images_generated_as_14_left_right_pairs(emma_build):
    for n in range(1, 15):
        for side in ("left", "right"):
            assert (emma_build.job_dir / "spreads" / f"spread-{n:02d}" / f"art-{side}.png").is_file()
    spread_calls = [c for c in emma_build.fake.calls if c["description"].startswith("Spread ")]
    assert len(spread_calls) == 28
    for index, call in enumerate(spread_calls):
        n, side = divmod(index, 2)
        assert call["description"] == f"Spread {n + 1} {'left' if side == 0 else 'right'}"
        if side == 1:  # right page is drawn with the left page as a reference (style / scene pairing)
            assert "art-left.png" in call["names"]
        else:
            assert "art-left.png" not in call["names"]


# --- 3. anchor state never resets (Emma bug 1: fridge list back to zero) ------------------------------------------


def test_anchor_ticks_follow_schedule_and_never_reset(emma_build):
    schedule = get_title("big_kid_list")["anchor"]["state_schedule"]
    spread_calls = [c for c in emma_build.fake.calls if c["description"].startswith("Spread ")]
    seen = []
    for index, call in enumerate(spread_calls):
        found = re.search(r"exactly (\d+) tick\(s\)", call["prompt"])
        assert found, f"call {index} has no anchor state"
        seen.append(int(found.group(1)))
    assert seen == [schedule[i // 2] for i in range(28)]
    assert all(b >= a for a, b in zip(seen, seen[1:]))
    report = qa.anchor_state_report(emma_build.job_dir, emma_build.inputs, None)
    assert report["passed"]


# --- 4. geometry: 3 mm bleed -> 216 mm / 2551 px; cover 472 x 248 (Emma bug 5) ---------------------------------------


def test_bleed_and_page_geometry_constants():
    assert BLEED_MM == 3.0
    assert PAGE_WITH_BLEED_MM == pytest.approx(216.0)
    assert PAGE_PX == 2551
    assert (COVER_WIDTH_MM, COVER_HEIGHT_MM) == (pytest.approx(472.0), pytest.approx(248.0))
    assert 2 * WRAP_MM + 2 * PAGE_WITH_BLEED_MM + SPINE_MM == pytest.approx(472.0)


def test_pdf_page_sizes_216mm_interior_472x248_cover(emma_build):
    reader = PdfReader(str(emma_build.pdf))
    if constants.PRINTER["packaging"] == "single_pdf_cover_flat_first":
        cover = reader.pages[0]
        assert float(cover.mediabox.width) == pytest.approx(mm_to_pt(472), abs=0.5)
        assert float(cover.mediabox.height) == pytest.approx(mm_to_pt(248), abs=0.5)
        interior = reader.pages[1:]
    else:
        interior = reader.pages
    for page in interior:
        assert float(page.mediabox.width) == pytest.approx(mm_to_pt(216), abs=0.5)
        assert float(page.mediabox.height) == pytest.approx(mm_to_pt(216), abs=0.5)


# --- 5. cover rules (Emma bug 4: question front, cream box, starring, no logo, blank spine) -----------------------------


def test_cover_front_back_spine_rules(emma_build):
    if constants.PRINTER["packaging"] != "single_pdf_cover_flat_first":
        pytest.skip("flat-cover assertions apply to the TJ flat packaging")
    locked = emma_build.story["locked"]
    cover_text = _text(emma_build.pdf, 0)
    assert locked["front_band"].upper() in cover_text  # band, caps
    assert locked["title"] in cover_text.replace("\r\n", " ").replace("\n", " ") or "Big-Kid List" in cover_text
    assert "starring" not in cover_text.lower()
    assert cover_text.count(locked["back_question"]) == 1  # the question lives on the back only

    doc = pdfium.PdfDocument(str(emma_build.pdf))
    try:
        scale = 2.0
        image = doc[0].render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()
    mm = lambda v: int(v / 25.4 * 72 * scale)
    spine_px = image.getpixel((mm(WRAP_MM + PAGE_WITH_BLEED_MM + SPINE_MM / 2), mm(COVER_HEIGHT_MM / 2)))
    assert spine_px == RUST, "spine is the family colour, not blank / cream"
    assert CREAM not in set(image.resize((200, 105)).getdata())

    # Logo and mini R are real image XObjects on the cover page (wordmark never retyped).
    reader = PdfReader(str(emma_build.pdf))
    assert len(reader.pages[0].images) >= 3  # cover art + full-colour logo + mini R (identical art is de-duplicated)


def test_cover_text_is_live_vector_text(emma_build):
    assert len(_text(emma_build.pdf, 0).strip()) > 40
    assert not hasattr(pdf_build, "overlay_text_on_image")  # Pillow raster overlay is gone


# --- 6. fonts (Emma bug 6: Helvetica) --------------------------------------------------------------------------


def test_only_brand_fonts_no_helvetica(emma_build):
    used = pdf_build.pdf_font_names(emma_build.pdf)
    assert used and used <= pdf_build.brand_font_names()
    assert not any("helvetica" in name.lower() for name in used)
    assert {"NotoSerif-SemiBold", "Nunito-Regular", "DMMono-Medium"} <= used


# --- 7. parent spread heading + no cream (Emma bugs 7, 8) ------------------------------------------------------------


def test_words_for_the_real_moment_on_page_30(emma_build):
    start = _first_interior(emma_build.pdf)
    p30 = _text(emma_build.pdf, start + 29)
    assert "WORDS FOR THE REAL MOMENT" in p30
    assert "YOU COULD SAY" in p30 and "TRY TOGETHER" in p30
    assert "READ IT AGAIN" in _text(emma_build.pdf, start + 30)


def test_no_cream_text_box_and_story_text_is_live(emma_build):
    start = _first_interior(emma_build.pdf)
    doc = pdfium.PdfDocument(str(emma_build.pdf))
    try:
        for index in range(start, start + 32):
            small = doc[index].render(scale=0.2).to_pil().convert("RGB")
            assert CREAM not in set(small.getdata()), f"cream pixel on PDF page index {index}"
    finally:
        doc.close()
    texts = [emma_build.story["spreads"][n - 1]["text"] for n in (1, 7, 14)]
    for n, text in zip((1, 7, 14), texts):
        carrying = [_text(emma_build.pdf, start + 2 * n - 1 + k) for k in (0, 1)]
        first_sentence = re.split(r"(?<=[.!?])\s", text)[0]
        assert any(first_sentence in c.replace("\r\n", " ") for c in carrying), f"spread {n} text not live in PDF"


# --- 8. native resolution recorded and gated (Emma bug 3 / no upscaler) -----------------------------------------------


def test_effective_ppi_recorded_per_spread_and_never_upscaled(emma_build):
    expected = effective_ppi(1600, PAGE_WITH_BLEED_MM)
    assert PPI_FAIL <= expected < constants.PPI_WARN
    for n in range(1, 15):
        meta = read_json(emma_build.job_dir / "spreads" / f"spread-{n:02d}" / "spread.json")
        assert meta["effective_ppi"] == pytest.approx(expected, abs=0.1)
        assert meta["resolution_status"] == constants.resolution_status(expected)
        for side in ("left", "right"):
            from PIL import Image

            with Image.open(emma_build.job_dir / "spreads" / f"spread-{n:02d}" / f"art-{side}.png") as im:
                assert im.size == (1600, 1600)  # exactly what the model returned
    assert illustrate.min_effective_ppi(emma_build.job_dir) == pytest.approx(expected, abs=0.1)
    job = read_json(emma_build.job_dir / "job.json")
    assert job["min_effective_ppi"] == pytest.approx(expected, abs=0.1)


def test_images_embedded_without_resampling(emma_build):
    start = _first_interior(emma_build.pdf)
    page = PdfReader(str(emma_build.pdf)).pages[start + 1]
    assert (1600, 1600) in [(i.image.width, i.image.height) for i in page.images]


def test_below_150_ppi_refuses_to_build(emma_build, tmp_path):
    import shutil

    from PIL import Image

    copy = tmp_path / "copy"
    shutil.copytree(emma_build.job_dir, copy)
    Image.new("RGB", (1000, 1000), (240, 230, 210)).save(copy / "spreads" / "spread-05" / "art-right.png")
    with pytest.raises(illustrate.PrintResolutionError):
        pdf_build.build_pdf(copy, emma_build.story, emma_build.pages, emma_build.inputs)


# --- 9. file naming + QA over the finished build ---------------------------------------------------------------------


def test_pdf_named_with_order_id(emma_build):
    assert emma_build.pdf.name == "EMMA-TEST-001-final.pdf"
    assert emma_build.pdf.parent.name == "print"


def test_qa_passes_on_the_emma_build_and_send_needs_human(emma_build):
    report = qa.run_qa(emma_build.job_dir)
    assert report["passed"], json.dumps(report["reports"], indent=1)[:800]
    assert qa.send_blockers(emma_build.job_dir)  # human check still required
