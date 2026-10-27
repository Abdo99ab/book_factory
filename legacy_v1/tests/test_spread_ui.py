"""True-spreads UI: spread routes, deprecated page routes, last-line fix, build footer, progress API."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app import create_app
from emma_data import build_emma_job, persist_emma_job
from pipeline import pdf_build
from pipeline.build_info import get_build_info
from pipeline.job_log import read_json, write_json

JOB_ID = "b" * 32
LAST_LINE = "I'm the one who decides. And I decided."


@pytest.fixture(scope="module")
def _built_job(tmp_path_factory):
    job_dir = tmp_path_factory.mktemp("ui") / JOB_ID
    inputs, story, pages = build_emma_job(job_dir)
    pdf = pdf_build.build_pdf(job_dir, story, pages, inputs)
    persist_emma_job(job_dir, inputs, story, pages, pdf)
    return job_dir


@pytest.fixture
def ui(_built_job, tmp_path, monkeypatch):
    import shutil

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    job_dir = jobs / JOB_ID
    shutil.copytree(_built_job, job_dir)
    job = read_json(job_dir / "job.json")
    write_json(job_dir / "job.json", {**job, "status": "content_ready", "spreads_done": 14, "spreads_total": 14})
    monkeypatch.setattr("app.JOB_ROOT", jobs)
    monkeypatch.setattr("app.ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr("app.TEMPLATE_ROOT", tmp_path / "templates")
    monkeypatch.setattr("app.job_executor", MagicMock())
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), job_dir


# --- spread routes return 200, page routes redirect -------------------------------------------------------------


def test_spread_art_composed_and_halves_return_200(ui):
    client, job_dir = ui
    art = client.get(f"/jobs/{JOB_ID}/spreads/3/art")
    assert art.status_code == 200 and art.mimetype == "image/png"
    with Image.open(BytesIO(art.data)) as image:
        assert image.size == (2840, 1440)  # the one wide picture, not a half
    assert client.get(f"/jobs/{JOB_ID}/spreads/3/composed").status_code == 200
    for side in ("left", "right"):
        half = client.get(f"/jobs/{JOB_ID}/spreads/3/page/{side}")
        assert half.status_code == 200
        with Image.open(BytesIO(half.data)) as image:
            assert image.size == (1440, 1440)
    assert (job_dir / "spreads" / "spread-03" / "page-left.png").is_file()  # derived cache, not the source


def test_spread_routes_404_outside_the_book(ui):
    client, _ = ui
    for n in (0, 15):
        assert client.get(f"/jobs/{JOB_ID}/spreads/{n}/art").status_code == 404
        assert client.get(f"/jobs/{JOB_ID}/spreads/{n}/composed").status_code == 404
    assert client.get(f"/jobs/{JOB_ID}/spreads/1/page/middle").status_code == 404


def test_deprecated_page_routes_redirect_to_the_spread(ui):
    client, _ = ui
    for page, spread in ((2, 1), (3, 1), (4, 2), (29, 14)):
        for kind in ("art", "composed"):
            response = client.get(f"/jobs/{JOB_ID}/pages/{page}/{kind}")
            assert response.status_code == 302, (page, kind)
            assert response.location.endswith(f"/jobs/{JOB_ID}/spreads/{spread}/{kind}")
    for page in (1, 30, 32):  # title, parent spread, dedication have no spread art
        assert client.get(f"/jobs/{JOB_ID}/pages/{page}/art").status_code == 404


# --- job page: sections, last-line guard, footer -------------------------------------------------------------------


def test_job_page_lists_spreads_in_order_with_cover_before_them(ui):
    client, _ = ui
    page = client.get(f"/jobs/{JOB_ID}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert html.index('id="cover-card"') < html.index('id="title-page-card"') < html.index('id="spread-1"')
    positions = [html.index(f'id="spread-{n}"') for n in range(1, 15)]
    assert positions == sorted(positions)
    assert "Spread 1 \u00b7 pages 2\u20133 \u00b7 text on left" in html
    assert "Spread 2 \u00b7 pages 4\u20135 \u00b7 text on right" in html
    assert f"/jobs/{JOB_ID}/spreads/1/composed" in html or f"/jobs/{JOB_ID}/spreads/1/art" in html
    assert "page-left" not in html and "art-left" not in html


def test_missing_last_line_shows_guard_and_button_then_one_click_fixes_it(ui):
    client, job_dir = ui
    story_path = job_dir / "story" / "rebuilt_story.json"
    story = read_json(story_path)
    story["spreads"][-1]["text"] = "Emma holds the pen. Then Emma goes to play."
    write_json(story_path, story)

    html = client.get(f"/jobs/{JOB_ID}").get_data(as_text=True)
    assert "Spread 14 must end with:" in html and "Insert last line" in html

    response = client.post(f"/jobs/{JOB_ID}/last-line")
    assert response.status_code == 302
    fixed = read_json(story_path)["spreads"][-1]["text"]
    assert fixed == f"Emma holds the pen. Then Emma goes to play. {LAST_LINE}"
    html = client.get(f"/jobs/{JOB_ID}").get_data(as_text=True)
    assert "Spread 14 must end with:" not in html and "Insert last line" not in html
    # idempotent: pressing it again never duplicates the line
    client.post(f"/jobs/{JOB_ID}/last-line")
    assert read_json(story_path)["spreads"][-1]["text"] == fixed
    pages = read_json(job_dir / "story" / "pages.json")["pages"]
    assert any(LAST_LINE in (p.get("text") or "") for p in pages if p.get("spread") == 14)


def test_insert_last_line_replaces_a_curly_near_match(ui):
    from pipeline.content_edit import insert_last_line

    _client, job_dir = ui
    story_path = job_dir / "story" / "rebuilt_story.json"
    story = read_json(story_path)
    story["spreads"][-1]["text"] = "Emma holds the pen. \u201cI\u2019m the one who decides, and I decided!\u201d"
    write_json(story_path, story)
    result = insert_last_line(job_dir)
    assert result["changed"] is True
    assert read_json(story_path)["spreads"][-1]["text"] == f"Emma holds the pen. {LAST_LINE}"
    assert insert_last_line(job_dir)["changed"] is False


def test_inline_spread_edit_saves_text_and_clears_art_when_the_scene_changes(ui):
    client, job_dir = ui
    art = job_dir / "spreads" / "spread-05" / "art.png"
    assert art.is_file()
    response = client.post(f"/jobs/{JOB_ID}/spreads/5/edit", data={"text": "Emma chooses the coat."})
    assert response.status_code == 302
    assert read_json(job_dir / "story" / "rebuilt_story.json")["spreads"][4]["text"] == "Emma chooses the coat."
    assert art.is_file()  # words only: the picture stays

    scene = "On the left a quiet hallway with a plain wall; on the right Emma reaches for her coat."
    client.post(f"/jobs/{JOB_ID}/spreads/5/edit", data={"scene": scene})
    assert read_json(job_dir / "story" / "rebuilt_story.json")["spreads"][4]["scene"] == scene
    assert not art.is_file()  # the picture brief changed, so the old picture is gone


def test_build_label_is_in_the_footer_of_every_page(ui):
    client, _ = ui
    for url in ("/", f"/jobs/{JOB_ID}"):
        html = client.get(url).get_data(as_text=True)
        assert 'id="build-info"' in html and "Build " in html


def test_get_build_info_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    monkeypatch.setenv("BUILD_BRANCH", "raised-ready-migration")
    info = get_build_info(tmp_path)  # not a git checkout
    assert info == {
        "sha": "abc1234",
        "branch": "raised-ready-migration",
        "label": "Build abc1234 \u00b7 raised-ready-migration",
    }
    monkeypatch.delenv("BUILD_SHA")
    monkeypatch.delenv("BUILD_BRANCH")
    assert get_build_info(tmp_path)["label"] == "Build unknown \u00b7 unknown"


def test_get_build_info_reads_this_checkout():
    info = get_build_info()
    assert info["label"].startswith("Build ") and "\u00b7" in info["label"]


# --- progress API -------------------------------------------------------------------------------------------------------


def test_status_api_reports_spreads_done_of_14(ui):
    client, job_dir = ui
    data = client.get(f"/api/jobs/{JOB_ID}").get_json()
    assert (data["spreads_done"], data["spreads_total"]) == (14, 14)
    assert "current_page" not in data and "total_pages" not in data
    (job_dir / "spreads" / "spread-14" / "art.png").unlink()
    (job_dir / "spreads" / "spread-13" / "art.png").unlink()
    assert client.get(f"/api/jobs/{JOB_ID}").get_json()["spreads_done"] == 12
