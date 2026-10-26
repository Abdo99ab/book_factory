"""Step 9 - ReportLab text layer, page order, brand fonts, cover geometry."""

from __future__ import annotations

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader

from pipeline import pdf_build
from pipeline.constants import (
    COVER_HEIGHT_MM,
    COVER_WIDTH_MM,
    PAGE_WITH_BLEED_MM,
    PRINTER,
    mm_to_pt,
)
from emma_data import build_emma_job

CREAM = (255, 250, 240)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    job_dir = tmp_path_factory.mktemp("pdf") / "job"
    inputs, story, pages = build_emma_job(job_dir, dark_spreads=(2,))
    pdf = pdf_build.build_pdf(job_dir, story, pages, inputs)
    return job_dir, inputs, story, pages, pdf


def _text(pdf_path, index):
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return doc[index].get_textpage().get_text_bounded()
    finally:
        doc.close()


def _offset():
    return 2 if PRINTER.get("endpapers_in_pdf") and PRINTER["packaging"] == "single_pdf_cover_flat_first" else (
        1 if PRINTER["packaging"] == "single_pdf_cover_first_back_last" else 1
    )


def test_file_name_carries_order_id(built):
    job_dir, inputs, _s, _p, pdf = built
    assert pdf.name == "EMMA-TEST-001-final.pdf"
    assert pdf.parent == job_dir / "print"


def test_page_count_and_sizes(built):
    _j, _i, _s, _p, pdf = built
    reader = PdfReader(str(pdf))
    sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]
    if PRINTER["packaging"] == "single_pdf_cover_flat_first":
        assert len(sizes) == 32 + 1 + (2 if PRINTER["endpapers_in_pdf"] else 0)
        assert sizes[0] == pytest.approx((mm_to_pt(COVER_WIDTH_MM), mm_to_pt(COVER_HEIGHT_MM)), abs=0.5)
        interior = sizes[1:]
    else:
        assert len(sizes) == 34
        interior = sizes
    for w, h in interior:
        assert w == pytest.approx(mm_to_pt(PAGE_WITH_BLEED_MM), abs=0.5)
        assert h == pytest.approx(mm_to_pt(PAGE_WITH_BLEED_MM), abs=0.5)


def test_page_order_title_spreads_parent_dedication(built):
    _j, inputs, story, _p, pdf = built
    first = _offset()
    assert "Emma" in _text(pdf, first)  # p1 title page (band + title)
    assert "Illustrated from Emma" in _text(pdf, first)
    assert "Emma sees the list" in _text(pdf, first + 1)  # spread 1 left = p2
    assert "Emma sees the list" not in _text(pdf, first + 2)  # right half carries no text
    assert "WORDS FOR THE REAL MOMENT" in _text(pdf, first + 29)  # p30
    assert "YOU COULD SAY" in _text(pdf, first + 29)
    assert "READ IT AGAIN" in _text(pdf, first + 30)  # p31
    last = _text(pdf, first + 31)  # p32
    assert "For Emma, from Mum and Dad" in last
    assert "EMMA-TEST-001" in last


def test_cover_has_locked_copy_and_no_starring(built):
    _j, _i, story, _p, pdf = built
    cover = _text(pdf, 0)
    assert story["locked"]["front_band"].upper() in cover
    assert "Emma" in cover
    assert "starring" not in cover.lower()
    assert story["locked"]["back_question"] in cover
    assert "raisedready.co.uk" in cover
    # The front never carries the question form
    front_question = story["locked"]["back_question"]
    assert cover.count(front_question) == 1


def test_only_brand_fonts_embedded(built):
    _j, _i, _s, _p, pdf = built
    used = pdf_build.assert_brand_fonts_only(pdf)
    assert used
    assert not any("Helvetica" in name for name in used)
    assert used <= pdf_build.brand_font_names()


def test_no_cream_background_anywhere(built):
    _j, _i, _s, _p, pdf = built
    doc = pdfium.PdfDocument(str(pdf))
    try:
        for index in (0, _offset() + 1, _offset() + 29):
            image = doc[index].render(scale=0.25).to_pil().convert("RGB")
            assert CREAM not in set(image.getdata()), f"page index {index} has a cream pixel"
    finally:
        doc.close()


def test_images_embedded_at_native_pixels_no_upscale(built):
    _j, _i, _s, _p, pdf = built
    reader = PdfReader(str(pdf))
    page = reader.pages[_offset() + 1]
    sizes = [(im.image.width, im.image.height) for im in page.images]
    assert (1400, 1400) in sizes


def test_text_plate_used_only_when_contrast_requires(built):
    job_dir, *_ = built
    import json

    layout = json.loads((job_dir / "print" / "layout.json").read_text(encoding="utf-8"))
    carrying = {r["page"]: r for r in layout["pages"] if r.get("kind") == "spread_half" and r["text"]}
    assert len(carrying) == 14  # exactly one text-carrying page per spread
    dark = carrying[4] if 4 in carrying else carrying[5]  # spread 2 has dark art
    assert dark["plate"] is True
    light = [r for page, r in carrying.items() if page not in (4, 5)]
    assert light and all(r["plate"] is False for r in light)


def test_contrast_ratio_helper():
    assert pdf_build.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0, rel=0.01)
    assert pdf_build.contrast_ratio(pdf_build.INK, pdf_build.PAPER) > 9


def test_page_map_assertion_rejects_wrong_count(built):
    _j, _i, _s, pages, _p = built
    with pytest.raises(ValueError):
        pdf_build.assert_page_map(pages[:-1])


def test_cover_spine_and_wrap_geometry():
    assert COVER_WIDTH_MM == pytest.approx(472.0)
    assert COVER_HEIGHT_MM == pytest.approx(248.0)


def test_previews_rendered_from_pdf(built):
    job_dir, *_ = built
    assert (job_dir / "print" / "pages" / "page-01.jpg").is_file()
    assert (job_dir / "print" / "pages" / "page-32.jpg").is_file()
