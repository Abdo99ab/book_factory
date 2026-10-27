"""Step 10 - QA reports and the human-check gate."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import pytest

from pipeline import pdf_build, qa
from pipeline.constants import PAGE_WITH_BLEED_MM, SPREAD_HEIGHT_MM, SPREAD_WIDTH_MM
from pipeline.job_log import read_json, write_json
from emma_data import build_emma_job, persist_emma_job


JOB_ID = "a" * 32


@pytest.fixture(scope="module")
def _built_job(tmp_path_factory):
    job_dir = tmp_path_factory.mktemp("qa") / JOB_ID
    inputs, story, pages = build_emma_job(job_dir)
    pdf = pdf_build.build_pdf(job_dir, story, pages, inputs)
    persist_emma_job(job_dir, inputs, story, pages, pdf)
    return job_dir


@pytest.fixture
def job(_built_job, tmp_path):
    """A private copy per test so tests can break things freely."""
    target = tmp_path / JOB_ID
    shutil.copytree(_built_job, target)
    return target


def _all_ticks():
    return [key for key, _ in qa.HUMAN_CHECK_ITEMS]


def test_clean_job_passes_qa_with_ppi_warning(job):
    report = qa.run_qa(job)
    assert report["passed"], report
    assert set(report["reports"]) == {"structure", "text_guard", "anchor_state", "face_position"}
    assert report["min_effective_ppi"] and 150 <= report["min_effective_ppi"] < 300
    codes = {i["code"] for r in report["reports"].values() for i in r["issues"]}
    assert "resolution" in codes  # 2840 px over 426 mm = ~169 ppi: below the 300 target -> warning, not failure
    assert "anchor_not_reviewed" in codes and "faces_not_reviewed" in codes
    assert "fold_warning" not in codes and "calm_zone_warning" not in codes


def test_structure_fails_when_art_missing(job):
    (job / "spreads" / "spread-03" / "art.png").unlink()
    report = qa.run_qa(job)
    assert not report["passed"]
    assert any(i["code"] == "art_missing" for i in report["reports"]["structure"]["issues"])


def test_structure_warns_below_150_ppi_when_enforcement_off(job, monkeypatch):
    from PIL import Image

    Image.new("RGB", (1000, 507), (240, 230, 210)).save(job / "spreads" / "spread-02" / "art.png")
    monkeypatch.setattr("pipeline.constants.ENFORCE_PRINT_PPI", False)
    report = qa.run_qa(job)
    assert report["min_effective_ppi"] < 150
    res = [i for i in report["reports"]["structure"]["issues"] if i["code"] == "resolution"]
    assert res and res[0]["severity"] == "warning"
    from pipeline.illustrate import assert_print_resolution

    assert assert_print_resolution(job) is not None

    monkeypatch.setattr("pipeline.constants.ENFORCE_PRINT_PPI", True)
    report2 = qa.run_qa(job)
    assert not report2["reports"]["structure"]["passed"]
    from pipeline.illustrate import PrintResolutionError

    with pytest.raises(PrintResolutionError):
        assert_print_resolution(job)


def test_text_guard_flags_bad_text(job):
    story = read_json(job / "story" / "rebuilt_story.json")
    story["spreads"][1]["text"] = "Emma walked through the wonderfully enormous kitchen and carefully considered whether the extraordinarily important paper list on the refrigerator really deserved another tick today."
    write_json(job / "story" / "rebuilt_story.json", story)
    report = qa.run_qa(job)
    assert not report["reports"]["text_guard"]["passed"]


def test_anchor_state_mismatch_and_reset(job):
    meta_path = job / "spreads" / "spread-05" / "spread.json"
    write_json(meta_path, {"spread": 5, "anchor_state": 1})
    report = qa.run_qa(job)
    assert any(i["code"] == "anchor_state_mismatch" for i in report["reports"]["anchor_state"]["issues"])


def test_anchor_reset_seen_by_reviewer(job):
    write_json(job / "qa" / "review_record.json", {"spreads": {"9": {"anchor_state_seen": 0}}})
    report = qa.run_qa(job)
    assert any(i["code"] == "anchor_state_seen" for i in report["reports"]["anchor_state"]["issues"])


def test_text_over_face_fails(job):
    layout = read_json(job / "print" / "layout.json")
    carrying = next(
        r for r in layout["pages"] if r.get("kind") == "spread_half" and r.get("box_mm")
    )
    left, top, width, height = carrying["box_mm"]
    # face boxes are normalised on the WHOLE spread; the right page's text box sits 210 mm in
    shift = 0.0 if carrying["side"] == "left" else SPREAD_WIDTH_MM - PAGE_WITH_BLEED_MM
    face = {"x": (left + shift) / SPREAD_WIDTH_MM, "y": top / SPREAD_HEIGHT_MM, "w": 0.05, "h": 0.1}
    write_json(job / "qa" / "review_record.json", {"spreads": {str(carrying["spread"]): {"faces": [face]}}})
    report = qa.run_qa(job)
    assert any(i["code"] == "text_over_face" for i in report["reports"]["face_position"]["issues"])


def test_send_blocked_until_qa_and_human_check_pass(job):
    assert qa.send_blockers(job)  # nothing done yet
    with pytest.raises(PermissionError):
        qa.send_preview(job)

    qa.run_qa(job)
    assert any("Human check" in b for b in qa.send_blockers(job))

    qa.record_human_check(job, "Taoufik", _all_ticks()[:-1])  # one item missing
    assert any("incomplete" in b for b in qa.send_blockers(job))
    qa.record_human_check(job, "", _all_ticks())  # nobody signed
    assert qa.send_blockers(job)

    qa.record_human_check(job, "Taoufik", _all_ticks())
    assert qa.send_blockers(job) == []
    qa.send_preview(job)
    assert (read_json(job / "job.json") or {}).get("sent_at")


def test_failed_qa_blocks_send_even_with_human_check(job):
    (job / "spreads" / "spread-03" / "art.png").unlink()
    qa.run_qa(job)
    qa.record_human_check(job, "Taoufik", _all_ticks())
    assert any("QA failed" in b for b in qa.send_blockers(job))


def test_rebuilding_pdf_makes_checks_stale(job):
    qa.run_qa(job)
    qa.record_human_check(job, "Taoufik", _all_ticks())
    assert qa.send_blockers(job) == []
    pdf = job / (read_json(job / "job.json")["pdf"])
    pdf.write_bytes(pdf.read_bytes() + b"\n%rebuilt")
    blockers = qa.send_blockers(job)
    assert any("out of date" in b for b in blockers)


def test_send_route_blocked_then_allowed(job, tmp_path, monkeypatch):
    from app import create_app

    monkeypatch.setattr("app.JOB_ROOT", job.parent)
    monkeypatch.setattr("app.ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr("app.TEMPLATE_ROOT", tmp_path / "templates")
    monkeypatch.setattr("app.job_executor", MagicMock())
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    page = client.get(f"/jobs/{job.name}")
    assert page.status_code == 200 and b"Human check" in page.data and b"Send preview" in page.data

    blocked = client.post(f"/jobs/{job.name}/send-preview", follow_redirects=True)
    assert b"Send blocked" in blocked.data
    assert not (read_json(job / "job.json") or {}).get("sent_at")

    client.post(f"/jobs/{job.name}/qa")
    client.post(f"/jobs/{job.name}/human-check", data={"reviewer": "Taoufik", "checked": _all_ticks()})
    client.post(f"/jobs/{job.name}/send-preview")
    assert (read_json(job / "job.json") or {}).get("sent_at")


def test_text_over_face_ignores_a_face_on_the_other_half(job):
    """A face at the far side of the spread from the text box is fine (and the old per-page keys are not read)."""
    layout = read_json(job / "print" / "layout.json")
    carrying = next(
        r for r in layout["pages"] if r.get("kind") == "spread_half" and r.get("box_mm")
    )
    far_x = 0.8 if carrying["side"] == "left" else 0.05
    face = {"x": far_x, "y": 0.1, "w": 0.1, "h": 0.1}
    write_json(job / "qa" / "review_record.json", {"spreads": {str(carrying["spread"]): {"faces": [face]}}})
    report = qa.run_qa(job)
    assert not any(i["code"] == "text_over_face" for i in report["reports"]["face_position"]["issues"])


def test_fold_and_calm_warnings_from_spread_json_reach_the_structure_report(job):
    meta_path = job / "spreads" / "spread-04" / "spread.json"
    meta = read_json(meta_path) or {}
    write_json(meta_path, {**meta, "fold_failures": ["fold"], "calm_zone_busy": True})
    report = qa.run_qa(job)
    issues = report["reports"]["structure"]["issues"]
    fold = [i for i in issues if i["code"] == "fold_warning"]
    calm = [i for i in issues if i["code"] == "calm_zone_warning"]
    assert fold and fold[0]["severity"] == "warning" and fold[0]["spread"] == 4
    assert fold[0]["proposed_action"] == "regen_spread_art"
    assert calm and calm[0]["spread"] == 4
    assert report["passed"]  # warnings never block


# --- fold check at generation time (mocked vision + image calls) -----------------------------------------------


def _generate_with_faces(tmp_path, monkeypatch, faces_per_call):
    from unittest.mock import patch

    from PIL import Image

    from emma_data import emma_bundle
    from mock_openai import FakeImageEdit
    from pipeline import imagecheck
    from pipeline.illustrate import generate_spread_art
    from pipeline.pages import spread_pages

    job_dir = tmp_path / "fold"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    calls = iter(faces_per_call)
    monkeypatch.setattr(qa, "detect_faces", lambda client, path: next(calls))
    monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    fake = FakeImageEdit(size=3408)
    with patch("pipeline.illustrate.image_edit", fake):
        meta = generate_spread_art(
            MagicMock(), job_dir, {**spread_pages(pages)[1], "spread": 1}, inputs, story, character
        )
    return job_dir, meta, fake


ON_THE_FOLD = [{"x": 0.47, "y": 0.4, "w": 0.06, "h": 0.12}]  # straddles x = 50 %
CLEAR_OF_THE_FOLD = [{"x": 0.62, "y": 0.4, "w": 0.06, "h": 0.12}]


def test_face_on_the_fold_regenerates_once_then_warns(tmp_path, monkeypatch):
    from pipeline.job_log import read_events

    job_dir, meta, fake = _generate_with_faces(tmp_path, monkeypatch, [ON_THE_FOLD, ON_THE_FOLD])
    assert len(fake.calls) == 2  # exactly one regeneration, no more
    assert "RETRY" in fake.calls[1]["prompt"] or "fold" in fake.calls[1]["prompt"].lower()
    assert fake.calls[1]["prompt"] != fake.calls[0]["prompt"]
    types = [e["type"] for e in read_events(job_dir)]
    assert types.count("fold_retry") == 1 and types.count("fold_warning") == 1
    assert meta["attempts"] == 2 and meta["fold_failures"] == ["fold"]
    assert (job_dir / "spreads" / "spread-01" / "art.png").is_file()  # the art is kept
    issues = qa.spread_layout_issues(job_dir, [1])
    assert "fold_warning" in [i["code"] for i in issues]


def test_fold_retry_that_clears_the_fold_gives_no_warning(tmp_path, monkeypatch):
    from pipeline.job_log import read_events

    job_dir, meta, fake = _generate_with_faces(tmp_path, monkeypatch, [ON_THE_FOLD, CLEAR_OF_THE_FOLD])
    assert len(fake.calls) == 2
    types = [e["type"] for e in read_events(job_dir)]
    assert types.count("fold_retry") == 1 and "fold_warning" not in types
    assert meta["fold_failures"] == []


def test_clear_spread_is_ok_with_one_call(tmp_path, monkeypatch):
    from pipeline.job_log import read_events

    job_dir, meta, fake = _generate_with_faces(tmp_path, monkeypatch, [CLEAR_OF_THE_FOLD])
    assert len(fake.calls) == 1
    types = [e["type"] for e in read_events(job_dir)]
    assert "fold_retry" not in types and "fold_warning" not in types
    assert meta["fold_checked"] is True and meta["fold_failures"] == []


def test_undetectable_faces_skip_fold_check_but_empty_list_is_no_face(tmp_path, monkeypatch):
    from pipeline import imagecheck

    monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    # None = detector unavailable → fold not checked, no no_face failure.
    job_dir, meta, fake = _generate_with_faces(tmp_path, monkeypatch, [None])
    assert len(fake.calls) == 1
    assert meta["fold_checked"] is False and meta["fold_failures"] == []


def test_busy_calm_half_warns_without_regenerating(tmp_path, monkeypatch):
    """Spread 1 keeps its text on the LEFT half; a busy centred calm box warns without regen."""
    from unittest.mock import patch

    from PIL import Image

    from emma_data import emma_bundle
    from pipeline import imagecheck
    from pipeline.illustrate import generate_spread_art
    from pipeline.job_log import read_events
    from pipeline.pages import spread_pages
    from test_emma_regressions import FIXTURES, FixedPngImageEdit

    job_dir = tmp_path / "busy"
    inputs, story, pages = emma_bundle(job_dir)
    character = job_dir / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    monkeypatch.setattr(
        qa, "detect_faces", lambda client, path: [{"x": 0.62, "y": 0.4, "w": 0.08, "h": 0.12}]
    )
    monkeypatch.setattr(imagecheck, "head_cut_heuristic", lambda path: False)
    fake = FixedPngImageEdit()
    fake._cycle = [(FIXTURES / "page_dark_lower.png").read_bytes()] * 3
    with patch("pipeline.illustrate.image_edit", fake):
        meta = generate_spread_art(
            MagicMock(), job_dir, {**spread_pages(pages)[1], "spread": 1}, inputs, story, character
        )
    monkeypatch.setattr(imagecheck, "is_busy", lambda stats: True)
    fake2 = FixedPngImageEdit()
    fake2._cycle = [(FIXTURES / "page_dark_lower.png").read_bytes()] * 3
    job2 = tmp_path / "busy2"
    inputs, story, pages = emma_bundle(job2)
    character = job2 / "character" / "character.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32)).save(character)
    with patch("pipeline.illustrate.image_edit", fake2):
        meta2 = generate_spread_art(
            MagicMock(), job2, {**spread_pages(pages)[1], "spread": 1}, inputs, story, character
        )
    assert meta2["calm_zone_busy"] is True
    assert "calm_zone_warning" in [e["type"] for e in read_events(job2)]
