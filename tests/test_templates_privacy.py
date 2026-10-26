"""Step 11 - catalogue templates keep {slot} placeholders and never store the child's data."""

from __future__ import annotations

import json

import pytest

from emma_data import emma_bundle, persist_emma_job
from pipeline import book_templates as bt
from pipeline.job_log import read_json, write_json


@pytest.fixture
def source_job(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "TEMPLATE_ROOT", tmp_path / "templates")
    job_dir = tmp_path / "jobs" / ("b" * 32)
    inputs, story, pages = emma_bundle(job_dir)
    # Things that must NEVER reach a template:
    (job_dir / "character").mkdir(parents=True, exist_ok=True)
    (job_dir / "character" / "reference.jpg").write_bytes(b"photo-bytes")
    (job_dir / "character" / "character.png").write_bytes(b"sheet-bytes")
    (job_dir / "anchor").mkdir(exist_ok=True)
    (job_dir / "anchor" / "anchor.png").write_bytes(b"anchor-bytes")
    (job_dir / "spreads" / "spread-01").mkdir(parents=True, exist_ok=True)
    (job_dir / "spreads" / "spread-01" / "art-left.png").write_bytes(b"art-bytes")
    pdf = job_dir / "print" / "EMMA-TEST-001-final.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF")
    persist_emma_job(job_dir, inputs, story, pages, pdf)
    return job_dir


def _new_values(**over):
    values = {
        "name": "Amira",
        "age": "3",
        "pronouns": "she",
        "carer_name": "Tom",
        "carer_role": "Dad",
        "home_type": "flat",
        "comfort_object": "dog",
        "dedication_text": "For Amira, from Nana",
        "order_id": "NEW-ORDER-7",
        "potty_or_seat": "toilet seat",
    }
    values.update(over)
    return values


def test_template_has_placeholders_and_no_child_data(source_job):
    meta = bt.save_job_as_template(source_job)
    tdir = bt.template_dir(meta["template_id"])
    body = read_json(tdir / "story" / "template_story.json")
    blob = json.dumps(body) + json.dumps(meta)

    assert {"name", "comfort_object", "potty_or_seat"} <= set(meta["placeholders"])
    assert "{name}" in body["spreads"][0]["text"]
    for private in ("Emma", "Sam", "EMMA-TEST-001", "For Emma, from Mum and Dad", "bunny"):
        assert private.lower() not in blob.lower(), private
    # No locked copy, no pages, no per-child fields.
    assert "locked" not in body and "title" not in body and "_cast_lock" not in body
    assert "source_name" not in meta and "source_job_id" not in meta


def test_template_directory_never_holds_photo_sheets_or_art(source_job):
    meta = bt.save_job_as_template(source_job)
    tdir = bt.template_dir(meta["template_id"])
    files = sorted(p.relative_to(tdir).as_posix() for p in tdir.rglob("*") if p.is_file())
    assert files == ["story/template_story.json", "template.json"]
    for blob in tdir.rglob("*"):
        if blob.is_file():
            data = blob.read_bytes()
            assert b"photo-bytes" not in data and b"sheet-bytes" not in data and b"art-bytes" not in data


def test_slot_schema_includes_standard_and_title_slots():
    schema = bt.slot_schema("big_kid_list")
    ids = {s["id"] for s in schema}
    assert {"name", "carer_name", "carer_role", "comfort_object", "order_id", "potty_or_seat"} <= ids
    assert bt.missing_slots("big_kid_list", {}) != []
    assert "potty_or_seat" in bt.missing_slots("big_kid_list", {"name": "A"})


def test_slot_fill_unit():
    out = bt.fill_placeholders({"t": "{name} has {comfort_object}. {comfort_object^} is soft. {name}'s turn."},
                               {"name": "Amira", "comfort_object": "dog"})
    assert out["t"] == "Amira has dog. Dog is soft. Amira's turn."
    with pytest.raises(ValueError):
        bt.fill_placeholders("{school_name}", {"name": "A"})
    assert bt.to_placeholders("Emma hugs bunny. Bunny waits.", {"name": "Emma", "comfort_object": "bunny"}) == \
        "{name} hugs {comfort_object}. {comfort_object^} waits."


def test_reuse_fills_every_slot_and_rebuilds_locked_copy(source_job, tmp_path):
    meta = bt.save_job_as_template(source_job)
    new_dir = tmp_path / "jobs" / ("c" * 32)
    packed = bt.materialize_template_into_job(meta["template_id"], new_dir, values=_new_values())

    story, inputs, pages = packed["story"], packed["inputs"], packed["pages"]
    assert story["locked"]["title"] == "Amira and the Big-Kid List"
    assert story["locked"]["dedication"] == "For Amira, from Nana"
    assert "Amira" in story["spreads"][0]["text"]
    assert inputs["order_id"] == "NEW-ORDER-7" and inputs["carer"] == {"name": "Tom", "role": "Dad"}
    assert len(pages) == 32 and pages[0]["kind"] == "title" and pages[31]["kind"] == "dedication"
    assert "toilet seat" in json.dumps(story["spreads"])

    everything = "".join(p.read_text(encoding="utf-8") for p in new_dir.rglob("*.json"))
    for old in ("Emma", "EMMA-TEST-001", "bunny"):
        assert old.lower() not in everything.lower(), old
    assert not list(new_dir.rglob("*.png")) and not list(new_dir.rglob("reference.*"))


def test_reuse_reasks_required_slots(source_job, tmp_path):
    meta = bt.save_job_as_template(source_job)
    values = _new_values()
    del values["potty_or_seat"]
    with pytest.raises(ValueError, match="potty_or_seat"):
        bt.materialize_template_into_job(meta["template_id"], tmp_path / "jobs" / ("d" * 32), values=values)
    with pytest.raises(ValueError, match="order_id"):
        bt.materialize_template_into_job(meta["template_id"], tmp_path / "jobs" / ("d" * 32), values=_new_values(order_id=""))


def test_reuse_rejects_different_pronouns(source_job, tmp_path):
    meta = bt.save_job_as_template(source_job)
    with pytest.raises(ValueError, match="pronouns"):
        bt.materialize_template_into_job(meta["template_id"], tmp_path / "jobs" / ("e" * 32), values=_new_values(pronouns="he"))


def test_save_refuses_when_a_name_part_survives(source_job):
    inputs = read_json(source_job / "inputs.json")
    inputs["name"] = "Mary Jane"
    write_json(source_job / "inputs.json", inputs)
    story = read_json(source_job / "story" / "rebuilt_story.json")
    story["spreads"][0]["text"] = "Mary Jane sees the list. Jane smiles."
    write_json(source_job / "story" / "rebuilt_story.json", story)
    with pytest.raises(ValueError, match="personal data"):
        bt.save_job_as_template(source_job)


def test_runner_template_path_uses_filled_story_only():
    """Template runs regenerate sheets/art; the runner source must not copy art from a template."""
    from pathlib import Path

    src = (Path(bt.__file__).parent / "runner.py").read_text(encoding="utf-8")
    assert "generate_anchor_sheet(client, job_dir, inputs)" in src.split("def run_from_template")[1]


def test_reuse_routes_ask_every_slot_and_start_job(source_job, tmp_path, monkeypatch):
    from io import BytesIO
    from unittest.mock import MagicMock

    from app import create_app

    meta = bt.save_job_as_template(source_job)
    monkeypatch.setattr("app.JOB_ROOT", tmp_path / "jobs")
    monkeypatch.setattr("app.ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr("app.TEMPLATE_ROOT", tmp_path / "templates")
    monkeypatch.setenv("_OPENAI_API_KEY", "test-key")
    executor = MagicMock()
    monkeypatch.setattr("app.job_executor", executor)
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    page = client.get(f"/templates/{meta['template_id']}/reuse")
    html = page.data.decode("utf-8")
    assert page.status_code == 200
    for slot in bt.slot_schema("big_kid_list"):
        assert f'name="slot_{slot["id"]}"' in html
    assert "Emma" not in html

    incomplete = client.post(f"/templates/{meta['template_id']}/reuse", data={"slot_name": "Amira"}, follow_redirects=True)
    assert b"Please fill" in incomplete.data and not executor.submit.called

    data = {f"slot_{k}": v for k, v in _new_values().items()}
    data["reference_image"] = (BytesIO(b"\x89PNG fake"), "kid.png")
    done = client.post(f"/templates/{meta['template_id']}/reuse", data=data, content_type="multipart/form-data")
    assert done.status_code == 302 and executor.submit.called
    new_job = next((tmp_path / "jobs").iterdir())
    assert (new_job / "character" / "reference.png").is_file()
    assert read_json(new_job / "inputs.json")["name"] == "Amira"
