"""Step 10 - QA reports and the human-check gate."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import pytest

from pipeline import pdf_build, qa
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
    assert "resolution" in codes  # 1400 px < 300 ppi target -> warning, not failure
    assert "anchor_not_reviewed" in codes and "faces_not_reviewed" in codes


def test_structure_fails_when_art_missing(job):
    (job / "spreads" / "spread-03" / "art-right.png").unlink()
    report = qa.run_qa(job)
    assert not report["passed"]
    assert any(i["code"] == "art_missing" for i in report["reports"]["structure"]["issues"])


def test_structure_fails_below_150_ppi(job):
    from PIL import Image

    Image.new("RGB", (1000, 1000), (240, 230, 210)).save(job / "spreads" / "spread-02" / "art-left.png")
    report = qa.run_qa(job)
    assert not report["reports"]["structure"]["passed"]
    assert report["min_effective_ppi"] < 150
    from pipeline.illustrate import PrintResolutionError, assert_print_resolution

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
    write_json(job / "qa" / "review_record.json", {"pages": {"18": {"anchor_state_seen": 0}}})
    report = qa.run_qa(job)
    assert any(i["code"] == "anchor_state_seen" for i in report["reports"]["anchor_state"]["issues"])


def test_text_over_face_fails(job):
    layout = read_json(job / "print" / "layout.json")
    carrying = next(r for r in layout["pages"] if r.get("box_mm"))
    left, top, width, height = carrying["box_mm"]
    face = {"x": left / 216, "y": top / 216, "w": 0.1, "h": 0.1}
    write_json(job / "qa" / "review_record.json", {"pages": {str(carrying["page"]): {"faces": [face]}}})
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
    (job / "spreads" / "spread-03" / "art-right.png").unlink()
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
