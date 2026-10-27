"""Step 4 — character approval gate."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.job_log import write_json
from pipeline.runner import prepare_content


@pytest.fixture
def job_dir(tmp_path):
    job = tmp_path / "job"
    (job / "character").mkdir(parents=True)
    (job / "character" / "reference.png").write_bytes(b"png")
    write_json(
        job / "inputs.json",
        {
            "title_id": "big_kid_list",
            "route": "morning_light",
            "name": "Emma",
            "age": "3",
            "pronouns": "she",
            "gender": "female",
            "carer": {"name": "Sam", "role": "Mum"},
            "home_type": "house",
            "comfort_object": "bunny",
            "slots": {"potty_or_seat": "potty"},
            "order_id": "RR-1",
        },
    )
    write_json(job / "job.json", {"job_id": "abc", "status": "queued"})
    return job


def _fake_image_result():
    result = MagicMock()
    result.data = [MagicMock(b64_json="aGVsbG8=")]
    return result


@patch("pipeline.character.image_edit", return_value=_fake_image_result())
def test_gate_holds_without_approval(mock_edit, job_dir):
    import json as json_mod

    client = MagicMock()
    inputs = json_mod.loads((job_dir / "inputs.json").read_text(encoding="utf-8"))
    character_path, story, pages = prepare_content(client, job_dir, inputs)
    assert character_path.is_file()
    assert story is None
    assert pages is None
    import json as json_mod

    job = json_mod.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["status"] == "character_ready"


@patch("pipeline.character.image_edit", return_value=_fake_image_result())
@patch("pipeline.runner.rebuild_story")
@patch("pipeline.runner.compile_pages")
def test_approval_continues_to_story(mock_pages, mock_story, mock_edit, job_dir):
    mock_story.return_value = {"title": "Emma and the Big-Kid List", "spreads": []}
    mock_pages.return_value = [{"number": 1}]
    import json as json_mod

    job = json_mod.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    job["character_approved_at"] = 1.0
    write_json(job_dir / "job.json", job)

    client = MagicMock()
    _path, story, pages = prepare_content(client, job_dir, json.loads((job_dir / "inputs.json").read_text()))
    assert story is not None
    assert pages is not None
    mock_story.assert_called_once()
    mock_pages.assert_called_once()
