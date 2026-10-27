"""Raised Ready preview defects: real story beats, one continuous picture per spread, front-cover layout.

Everything here is offline: OpenAI image / vision calls are replaced by fixed PNGs and fake detectors.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from emma_data import emma_bundle
from pipeline import illustrate, imagecheck, pages as pages_mod, pdf_build, qa
from pipeline.catalogue import (
    STORIES_DIR,
    get_title,
    list_titles,
    load_catalogue,
    load_story_beats,
    resolve_carer_text,
)
from pipeline.job_log import read_events, read_json, write_json

FIXTURES = Path(__file__).parent / "fixtures"


# --- Defect 1: story beats + catalogue patch ------------------------------------------------------------


def _header(title_id: str, key: str) -> str:
    for line in (STORIES_DIR / f"{title_id}.md").read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{title_id}.md has no '{key}:' header")


def test_every_catalogue_last_line_equals_story_header_last_line():
    titles = load_catalogue()["titles"]
    assert len(titles) == 10
    for title_id, entry in titles.items():
        # raw template strings, {name} placeholders untouched
        assert entry["last_line"] == _header(title_id, "last_line"), title_id
        assert entry["last_line"] == load_story_beats(title_id)["meta"]["last_line"]


def test_story_beats_meta_parses_carer_signature_sets_last_line_and_stays_unapproved():
    for entry in list_titles():
        meta = load_story_beats(entry["title_id"])["meta"]
        assert meta["approved"] is False  # until Taoufik signs
        assert meta["sets"], entry["title_id"]
        assert meta["carer_signature"], entry["title_id"]
        assert meta["last_line"], entry["title_id"]
    big = load_story_beats("big_kid_list")["meta"]
    assert big["carer_signature"] == "Mum's yellow scarf"
    assert big["sets"] == ["kitchen", "hallway", "garden", "nursery_door"]


def test_catalogue_patch_applied_to_big_kid_list():
    entry = get_title("big_kid_list")
    assert entry["last_line"] == "I'm the one who decides. And I decided."
    parent = entry["parent_spread"]
    assert parent["try_together"] == "Which big-kid thing shall we tick next?"
    assert "{name}" in parent["in_real_life"]
    assert parent["you_could_say"].startswith("It's your turn to choose.")


def test_locked_parent_spread_fills_name_placeholder():
    from pipeline.story import _build_locked

    locked = _build_locked(get_title("big_kid_list"), {"name": "Emma", "dedication_text": ""})
    assert "Emma" in locked["parent_spread"]["in_real_life"]
    assert "{name}" not in json.dumps(locked)


def test_resolve_carer_text_prefers_name_then_role():
    assert resolve_carer_text("{carer}'s scarf", {"carer": {"name": "Sam", "role": "Mum"}}) == "Sam's scarf"
    assert resolve_carer_text("{carer}'s scarf", {"carer": {"role": "Mum"}}) == "Mum's scarf"
    assert resolve_carer_text("{carer}'s scarf", {}) == "the carer's scarf"


def test_cast_block_carries_carer_signature(tmp_path):
    inputs, story, _pages = emma_bundle(tmp_path / "job")
    block = illustrate._cast_block(inputs, story)
    assert "The carer always wears/carries: Mum's yellow scarf" in block
    assert "identical on every page" in block
    # a signature using {carer} resolves to the order's carer name
    other = {**inputs, "title_id": "bed_boat"}
    assert "Sam's yellow scarf" in illustrate._cast_block(other, {**story, "carer_signature": ""})
    # no signature, no line
    assert "always wears/carries" not in illustrate._cast_block(inputs, story, include_carer=False)


# --- Defect 2: one continuous picture per spread (true spreads) --------------------------------------------------------


@pytest.fixture
def bundle(tmp_path):
    return emma_bundle(tmp_path / "job")


def test_compile_pages_text_only_on_calm_half_and_one_scene_on_both(bundle):
    _inputs, story, pages = bundle
    for n in range(1, 15):
        halves = pages_mod.spread_halves(pages, n)
        assert len(halves) == 2
        calm = halves[0]["calm_zone"]
        assert calm == ("left" if n % 2 == 1 else "right")
        with_text = [h for h in halves if h["text"].strip()]
        assert [h["side"] for h in with_text] == [calm], f"spread {n}"
        assert with_text[0]["text"] == story["spreads"][n - 1]["text"]
        assert [h["text"] for h in halves if h["side"] != calm] == [""]
        for half in halves:
            assert half["scene"] == story["spreads"][n - 1]["scene"]
            assert "left" in half["scene"].lower() and "right" in half["scene"].lower()
            assert half["art_path"] == f"spreads/spread-{n:02d}/art.png"
            assert "scene_left" not in half and "scene_right" not in half


def test_spread_pages_brief_still_carries_the_spread_text(bundle):
    _inputs, story, pages = bundle
    briefs = pages_mod.spread_pages(pages)
    assert briefs[2]["text"] == story["spreads"][1]["text"]  # spread 2 is calm-right, brief is the left half


def test_set_spread_text_moves_text_when_calm_zone_changes(bundle):
    _inputs, _story, pages = bundle
    pages_mod.set_spread_text(pages, 1, "New words.", "right")
    halves = {h["side"]: h for h in pages_mod.spread_halves(pages, 1)}
    assert halves["right"]["text"] == "New words." and halves["left"]["text"] == ""
    assert halves["left"]["calm_zone"] == halves["right"]["calm_zone"] == "right"
    assert pages_mod.spread_text(pages, 1) == "New words."


def _spread(pages, n):
    return {**pages_mod.spread_pages(pages)[n], "spread": n}


def test_prompt_carries_the_one_scene_for_the_whole_spread(bundle):
    inputs, story, pages = bundle
    spread = _spread(pages, 1)
    prompt = illustrate._spread_prompt(spread, inputs, story)
    assert spread["scene"] in prompt
    assert "ONE scene across the whole frame" in prompt
    assert "Do not repeat" not in prompt and "The other page of this spread" not in prompt


def _write_plain(path: Path, colour=(240, 230, 210), size=256):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), colour).save(path)


def test_pair_duplicate_check_is_gone():
    """Halves of one image can't be duplicates of each other: the check and its hashing are removed."""
    for name in ("pair_duplicates", "pair_duplicate_issues", "pair_duplicate_actions"):
        assert not hasattr(qa, name)
    for name in ("pair_distance", "is_duplicate_pair", "grey_hash", "hash_distance"):
        assert not hasattr(imagecheck, name)


def test_qa_text_guard_hard_fails_a_scene_that_skips_a_half(bundle):
    inputs, story, _pages = bundle
    bad = json.loads(json.dumps(story))
    bad["spreads"][3]["scene"] = "Emma sits by the door with her shoes."  # no left / right
    report = qa.text_guard_report(inputs, bad)
    assert not report["passed"]
    assert any(i["code"] == "scene_halves" and i["spread"] == 4 for i in report["issues"])
    assert qa.text_guard_report(inputs, story)["passed"]


def test_non_calm_page_has_no_text_in_the_pdf(tmp_path):
    from emma_data import build_emma_job

    job = tmp_path / ("a" * 32)
    inputs, story, pages = build_emma_job(job)
    pdf = pdf_build.build_pdf(job, story, pages, inputs)
    layout = read_json(job / "print" / "layout.json")["pages"]
    halves = [r for r in layout if r.get("kind") == "spread_half"]
    assert len(halves) == 28
    by_page = {r["page"]: r for r in halves}
    page_map = {p["number"]: p for p in pages}
    for number, record in by_page.items():
        page = page_map[number]
        assert record["text"] is (page["side"] == page["calm_zone"]), f"page {number}"
        if page["side"] != page["calm_zone"]:
            assert page["text"] == ""
            assert "box_mm" not in record

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        first = 1 + (1 if pdf_build.PRINTER.get("endpapers_in_pdf") and pdf_build.PRINTER["packaging"] == "single_pdf_cover_flat_first" else 0)
        for number, page in page_map.items():
            if page.get("kind") != "spread_half":
                continue
            drawn = doc[first + number - 1].get_textpage().get_text_bounded().strip()
            assert bool(drawn) == (page["side"] == page["calm_zone"]), f"page {number} text={drawn!r}"
    finally:
        doc.close()


# --- Defect 3: front cover layout ------------------------------------------------------------------------


def test_front_prompt_keeps_carer_off_and_states_layout_rules(bundle):
    inputs, story, _pages = bundle
    front = illustrate._cover_prompt("front", inputs, story)
    assert "never on the front cover" in front
    assert "always wears/carries" not in front  # no carer on the front, so no scarf rule either
    assert "50-62%" in front and "TOP 30%" in front and "BOTTOM 15%" in front
    assert "NO EDGE CUTS" in front and "calm" in front.lower()
    back = illustrate._cover_prompt("back", inputs, story)
    assert "UPPER TWO THIRDS" in back and "QR KEEP-CLEAR" in back
    assert "never on the front cover" not in back
    retry = illustrate._cover_prompt("front", inputs, story, fix_zones=["top band/title zone (top 30%)"])
    assert "RETRY" in retry and "top band/title zone" in retry


def test_layout_check_rules(tmp_path):
    plain = tmp_path / "plain.png"
    _write_plain(plain)
    centred = {"x": 0.38, "y": 0.45, "w": 0.24, "h": 0.22}
    assert imagecheck.check_front_cover_layout(plain, [centred])["passed"]

    top_third = {"x": 0.38, "y": 0.05, "w": 0.24, "h": 0.22}
    result = imagecheck.check_front_cover_layout(plain, [top_third])
    assert not result["passed"] and result["failures"][0].startswith("face")
    off_side = {"x": 0.0, "y": 0.45, "w": 0.2, "h": 0.2}
    assert not imagecheck.check_front_cover_layout(plain, [off_side])["passed"]
    assert not imagecheck.check_front_cover_layout(plain, [])["passed"]  # no face at all
    unchecked = imagecheck.check_front_cover_layout(plain, None)  # detector unavailable
    assert unchecked["passed"] and unchecked["face_checked"] is False

    busy = tmp_path / "busy.png"
    image = Image.new("RGB", (256, 256), (240, 230, 210))
    for x in range(0, 256, 8):
        for y in range(0, 60, 8):
            if (x + y) // 8 % 2:
                image.paste((20, 30, 60), (x, y, x + 8, y + 8))
        for y in range(220, 256, 8):
            if (x + y) // 8 % 2:
                image.paste((20, 30, 60), (x, y, x + 8, y + 8))
    image.save(busy)
    failures = imagecheck.check_front_cover_layout(busy, [centred])["failures"]
    assert any(f.startswith("top band/title zone") for f in failures)
    assert any(f.startswith("logo zone") for f in failures)


class PlainCoverImageEdit:
    def __init__(self, colour=(240, 230, 210)):
        self.calls: list[dict] = []
        buffer = io.BytesIO()
        Image.new("RGB", (512, 512), colour).save(buffer, format="PNG")
        self._data = base64.b64encode(buffer.getvalue()).decode("ascii")

    def __call__(self, client, image_path, prompt, size=None, quality=None, description=""):
        self.calls.append({"prompt": prompt, "description": description})
        return SimpleNamespace(data=[SimpleNamespace(b64_json=self._data)])


@pytest.fixture
def cover_job(tmp_path, monkeypatch):
    job = tmp_path / ("c" * 32)
    inputs, story, _pages = emma_bundle(job)
    (job / "character").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "page_light.png", job / "character" / "character.png")
    fake = PlainCoverImageEdit()
    monkeypatch.setattr(illustrate, "image_edit", fake)
    return SimpleNamespace(job=job, inputs=inputs, story=story, fake=fake, character=job / "character" / "character.png")


def _front_calls(fake):
    return [c for c in fake.calls if c["description"].startswith("Front")]


def test_face_in_top_third_regenerates_once_then_warns_and_forces_plate(cover_job, monkeypatch):
    seen_faces = []

    def detector(client, path):
        face = [{"x": 0.38, "y": 0.05, "w": 0.24, "h": 0.22}]
        seen_faces.append(face)
        return face

    monkeypatch.setattr(qa, "detect_faces", detector)
    illustrate.generate_cover_panels(
        MagicMock(), cover_job.job, cover_job.inputs, cover_job.story, cover_job.character
    )
    front = _front_calls(cover_job.fake)
    assert len(front) == 2  # one regeneration, no more
    assert "RETRY" in front[1]["prompt"] and "face:" in front[1]["prompt"]  # failing zone named
    assert "RETRY" not in front[0]["prompt"]
    assert len(seen_faces) == 2

    events = read_events(cover_job.job)
    types = [e["type"] for e in events]
    assert types.count("cover_layout_retry") == 1
    assert types.count("cover_layout_warning") == 1
    cover = read_json(cover_job.job / "cover" / "cover.json")
    assert cover["layout"]["force_soft_plate"] is True and cover["layout"]["regenerated"] is True
    assert (cover_job.job / "cover" / "front_art.png").is_file()  # art kept
    assert pdf_build._cover_forces_plate(cover_job.job)
    issues = qa.cover_layout_issues(cover_job.job)
    assert [i["code"] for i in issues] == ["cover_layout_warning"] and issues[0]["severity"] == "warning"


def test_plain_top_and_centred_face_gives_no_warning_and_a_composed_preview(cover_job, monkeypatch):
    monkeypatch.setattr(qa, "detect_faces", lambda client, path: [{"x": 0.38, "y": 0.45, "w": 0.24, "h": 0.22}])
    illustrate.generate_cover_panels(
        MagicMock(), cover_job.job, cover_job.inputs, cover_job.story, cover_job.character
    )
    assert len(_front_calls(cover_job.fake)) == 1
    types = [e["type"] for e in read_events(cover_job.job)]
    assert "cover_layout_warning" not in types and "cover_layout_retry" not in types
    cover = read_json(cover_job.job / "cover" / "cover.json")
    assert cover["layout"]["failures"] == [] and cover["layout"]["force_soft_plate"] is False
    assert not pdf_build._cover_forces_plate(cover_job.job)
    assert qa.cover_layout_issues(cover_job.job) == []

    preview = cover_job.job / "cover" / "front_preview.png"
    assert preview.is_file()
    with Image.open(preview) as composed:
        assert composed.width > 500
        # the family band is painted over the top of the art, so the composed image differs from it
        with Image.open(cover_job.job / "cover" / "front_art.png") as art:
            top_art = art.convert("RGB").resize((8, 8)).getpixel((4, 0))
        assert composed.convert("RGB").resize((8, 8)).getpixel((4, 0)) != top_art
    assert not (cover_job.job / "cover" / "_preview_work").exists()


def test_undetectable_face_only_checks_detail_zones(cover_job, monkeypatch):
    monkeypatch.setattr(qa, "detect_faces", lambda client, path: None)
    illustrate.generate_cover_panels(
        MagicMock(), cover_job.job, cover_job.inputs, cover_job.story, cover_job.character
    )
    assert len(_front_calls(cover_job.fake)) == 1
    assert "cover_layout_warning" not in [e["type"] for e in read_events(cover_job.job)]


def test_detect_faces_returns_none_when_the_vision_call_fails(tmp_path):
    path = tmp_path / "x.png"
    _write_plain(path)
    assert qa.detect_faces(MagicMock(), path) is None  # MagicMock reply is not JSON -> advisory None
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"faces": [{"x": 0.4, "y": 0.5, "w": 0.2, "h": 0.2}]}'))]
    )
    assert qa.detect_faces(client, path) == [{"x": 0.4, "y": 0.5, "w": 0.2, "h": 0.2}]


def test_forced_plate_is_used_when_the_cover_is_drawn(cover_job, monkeypatch):
    """With force_soft_plate the title/logo plate is drawn even on calm art (checked via plate layers)."""
    monkeypatch.setattr(qa, "detect_faces", lambda client, path: [{"x": 0.38, "y": 0.05, "w": 0.24, "h": 0.22}])
    illustrate.generate_cover_panels(
        MagicMock(), cover_job.job, cover_job.inputs, cover_job.story, cover_job.character
    )
    forced = (cover_job.job / "cover" / "front_preview.png").read_bytes()
    cover = read_json(cover_job.job / "cover" / "cover.json")
    cover["layout"]["force_soft_plate"] = False
    write_json(cover_job.job / "cover" / "cover.json", cover)
    pdf_build.render_front_preview(cover_job.job, cover_job.inputs, cover_job.story)
    plain = (cover_job.job / "cover" / "front_preview.png").read_bytes()
    assert forced != plain  # the forced soft plate changes what is drawn


def test_job_page_shows_composed_front_next_to_raw_art(cover_job, monkeypatch, tmp_path):
    from app import create_app

    monkeypatch.setattr(qa, "detect_faces", lambda client, path: [{"x": 0.38, "y": 0.45, "w": 0.24, "h": 0.22}])
    illustrate.generate_cover_panels(
        MagicMock(), cover_job.job, cover_job.inputs, cover_job.story, cover_job.character
    )
    write_json(cover_job.job / "inputs.json", cover_job.inputs)
    write_json(cover_job.job / "job.json", {"job_id": cover_job.job.name, "name": "Emma", "status": "content_ready"})
    write_json(cover_job.job / "story" / "rebuilt_story.json", cover_job.story)
    monkeypatch.setattr("app.JOB_ROOT", cover_job.job.parent)
    monkeypatch.setattr("app.ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr("app.TEMPLATE_ROOT", tmp_path / "templates")
    monkeypatch.setattr("app.job_executor", MagicMock())
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    page = client.get(f"/jobs/{cover_job.job.name}")
    assert page.status_code == 200
    assert b"Composed front" in page.data and b"/cover/front-preview" in page.data
    served = client.get(f"/jobs/{cover_job.job.name}/cover/front-preview")
    assert served.status_code == 200 and served.mimetype == "image/png"
