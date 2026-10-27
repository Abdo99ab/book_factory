"""Composed spread preview includes calm-half story text (operator UI)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pipeline.content import calm_zone_for
from pipeline.pdf_build import render_spread_preview


def test_render_spread_preview_writes_composed_png(tmp_path: Path):
    job_dir = tmp_path / "job"
    art_dir = job_dir / "spreads" / "spread-01"
    art_dir.mkdir(parents=True)
    # Wide landscape: calm left (light) + busy right (dark) so plate/text differ from raw art.
    art = Image.new("RGB", (1200, 600), (240, 230, 210))
    for x in range(600, 1200):
        for y in range(600):
            art.putpixel((x, y), (80, 120, 90))
    art.save(art_dir / "art.png")

    story = {
        "title": "Emma and the Big-Kid List",
        "spreads": [
            {
                "n": 1,
                "text": "Emma sees the list on the fridge.",
                "scene": "kitchen",
                "calm_zone": calm_zone_for(1),
            }
        ],
    }
    pages = [
        {
            "number": 2,
            "kind": "spread_half",
            "spread": 1,
            "side": "left",
            "calm_zone": "left",
            "text": "Emma sees the list on the fridge.",
            "scene": "kitchen",
        },
        {
            "number": 3,
            "kind": "spread_half",
            "spread": 1,
            "side": "right",
            "calm_zone": "left",
            "text": "",
            "scene": "kitchen",
        },
    ]
    inputs = {
        "title_id": "big_kid_list",
        "name": "Emma",
        "route": "morning_light",
    }

    out = render_spread_preview(job_dir, inputs, story, 1, pages=pages, scale=0.6)
    assert out is not None and out.is_file()
    with Image.open(out) as composed, Image.open(art_dir / "art.png") as raw:
        assert composed.size[0] > composed.size[1]
        # Text plate / ink changes the calm half vs bare art.
        assert list(composed.convert("RGB").getdata()) != list(
            raw.convert("RGB").resize(composed.size).getdata()
        )
