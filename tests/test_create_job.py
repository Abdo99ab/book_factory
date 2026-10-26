"""Step 3 — Raised Ready create-job contract."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr("app.JOB_ROOT", jobs)
    monkeypatch.setattr("app.ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr("app.TEMPLATE_ROOT", tmp_path / "templates")
    monkeypatch.setenv("_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.job_executor", MagicMock())
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), jobs


def _post_job(client, **extra):
    data = {
        "title_id": "big_kid_list",
        "name": "Emma",
        "age": "3",
        "pronouns": "she",
        "carer_name": "Sam",
        "carer_role": "Mum",
        "home_type": "house",
        "comfort_object": "blue bunny",
        "dedication_text": "For Emma",
        "order_id": "RR-1001",
        "start_mode": "preview",
        "slot_potty_or_seat": "potty",
        "reference_image": (BytesIO(b"fake"), "photo.png"),
        **extra,
    }
    return client.post("/jobs", data=data, content_type="multipart/form-data")


def test_valid_post_writes_new_inputs_shape(client):
    test_client, jobs = client
    response = _post_job(test_client)
    assert response.status_code == 302
    job_id = response.location.rstrip("/").split("/")[-1]
    inputs_path = jobs / job_id / "inputs.json"
    assert inputs_path.is_file()
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    assert inputs["title_id"] == "big_kid_list"
    assert inputs["order_id"] == "RR-1001"
    assert inputs["pronouns"] == "she"
    assert inputs["carer"]["name"] == "Sam"
    assert inputs["slots"]["potty_or_seat"] == "potty"
    assert "theme" not in inputs


def test_legacy_theme_field_ignored(client):
    test_client, jobs = client
    response = _post_job(test_client, theme="Adventure")
    assert response.status_code == 302
    job_id = response.location.rstrip("/").split("/")[-1]
    inputs = json.loads((jobs / job_id / "inputs.json").read_text(encoding="utf-8"))
    assert "theme" not in inputs


def test_missing_order_id_rejected(client):
    test_client, _jobs = client
    response = _post_job(test_client, order_id="")
    assert response.status_code == 302
    assert response.location.endswith("/create")


def test_invalid_order_id_rejected(client):
    test_client, _jobs = client
    response = _post_job(test_client, order_id="bad id!")
    assert response.status_code == 302
    assert response.location.endswith("/create")
