"""Child face sheet + body sheet generation (mocked OpenAI)."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from pipeline.character import character_paths, generate_character


def _png_b64(colour=(200, 180, 160)) -> str:
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (64, 64), colour).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_generate_character_writes_face_and_body(tmp_path: Path, monkeypatch):
    ref = tmp_path / "character" / "reference.jpg"
    ref.parent.mkdir(parents=True)
    Image.new("RGB", (128, 128), (180, 140, 120)).save(ref)

    calls = []

    def fake_edit(client, images, prompt, size=None, quality=None, description="Image edit", mask_path=None):
        calls.append({"description": description, "prompt": prompt})
        result = MagicMock()
        result.data = [MagicMock(b64_json=_png_b64())]
        return result

    monkeypatch.setattr("pipeline.character.image_edit", fake_edit)
    monkeypatch.setattr(
        "pipeline.character.get_title",
        lambda tid: {"title_id": tid, "route": "morning_light", "outfit": "teal jumper", "age_band": "3-6"},
    )
    monkeypatch.setattr("pipeline.character.resolve_route", lambda title: "morning_light")

    inputs = {
        "title_id": "dinosaur_egg",
        "name": "Emma",
        "age": "4",
        "pronouns": "she",
        "outfit": "teal jumper",
    }
    paths = generate_character(MagicMock(), tmp_path, ref, inputs)
    assert paths["body"].is_file()
    assert paths["face"].is_file()
    assert len(calls) == 2
    assert any("Face" in c["description"] for c in calls)
    assert any("head-and-shoulders" in c["prompt"].lower() or "face sheet" in c["prompt"].lower() for c in calls)
    expected = character_paths(tmp_path)
    assert paths["body"] == expected["body"]
    assert paths["face"] == expected["face"]
