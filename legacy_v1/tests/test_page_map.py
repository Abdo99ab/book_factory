"""Step 6 — deterministic 32-page map."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.constants import CONTENT_PAGE_COUNT, SPREAD_COUNT
from pipeline.pages import compile_pages


def test_page_map_shape_no_llm(tmp_path):
    job_dir = tmp_path / "job"
    (job_dir / "story").mkdir(parents=True)
    inputs = {"title_id": "big_kid_list"}
    spreads = [
        {"n": i, "text": f"Spread {i}", "scene": f"Scene {i}"}
        for i in range(1, SPREAD_COUNT + 1)
    ]
    story = {
        "spreads": spreads,
        "locked": {
            "title": "Emma and the Big-Kid List",
            "parent_spread": {
                "you_could_say": "Say",
                "try_together": "Try",
                "in_real_life": "Life",
                "read_it_again": "Again",
            },
            "dedication": "For Emma",
        },
    }
    pages = compile_pages(None, job_dir, inputs, story)

    assert len(pages) == CONTENT_PAGE_COUNT
    assert pages[0]["kind"] == "title"
    assert pages[1]["kind"] == "spread_half" and pages[1]["side"] == "left"
    assert pages[2]["spread"] == 1 and pages[2]["side"] == "right"
    assert pages[29]["number"] == 30 and pages[29]["kind"] == "parent_left"
    assert pages[31]["kind"] == "dedication"
    # both halves of a spread point at the ONE wide image
    assert pages[1]["art_path"] == pages[2]["art_path"] == "spreads/spread-01/art.png"
    assert "art_path_left" not in pages[1] and "art_path_right" not in pages[2]
    assert pages[1]["spread"] == 1 and pages[28]["spread"] == 14


def test_split_spread_cuts_216mm_halves_with_a_6mm_overlap():
    from PIL import Image

    from pipeline.pages import split_box_px, split_spread

    spread = Image.new("RGB", (1972, 1000), (240, 230, 210))
    spread.putpixel((999, 10), (255, 0, 0))  # last column of the left half
    spread.putpixel((972, 20), (0, 255, 0))  # first column of the right half
    left, right = split_spread(spread)
    assert split_box_px(1972) == 1000
    assert left.size == right.size == (1000, 1000)
    assert left.getpixel((999, 10)) == (255, 0, 0)  # left = x in [0, 1000)
    assert right.getpixel((0, 20)) == (0, 255, 0)  # right = x in [972, 1972)
    assert right.getpixel((27, 10)) == (255, 0, 0)  # the 28 px overlap holds the same pixels in both
