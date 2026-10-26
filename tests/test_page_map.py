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
    assert pages[1]["art_path_left"] == "spreads/spread-01/art-left.png"
    assert pages[2]["art_path_right"] == "spreads/spread-01/art-right.png"
