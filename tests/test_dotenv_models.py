"""``.env`` model settings must win over constants defaults."""

from __future__ import annotations

import importlib
import os

from dotenv import load_dotenv


def test_constants_picks_up_text_and_image_model_from_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TEXT_MODEL=from-dotenv-text\n"
        "IMAGE_MODEL=from-dotenv-image\n"
        "IMAGE_QUALITY=low\n"
        "IMAGE_SIZE=1024x1024\n",
        encoding="utf-8",
    )
    for key in ("TEXT_MODEL", "IMAGE_MODEL", "IMAGE_QUALITY", "IMAGE_SIZE"):
        monkeypatch.delenv(key, raising=False)

    # Simulate app/constants loading this file into the process env first.
    assert load_dotenv(env_file, override=True)
    # Re-bind via monkeypatch so pytest restores the real environment after the test.
    for key in ("TEXT_MODEL", "IMAGE_MODEL", "IMAGE_QUALITY", "IMAGE_SIZE"):
        monkeypatch.setenv(key, os.environ[key])

    import pipeline.constants as constants

    importlib.reload(constants)

    assert constants.TEXT_MODEL == "from-dotenv-text"
    assert constants.IMAGE_MODEL == "from-dotenv-image"
    assert constants.IMAGE_QUALITY == "low"
    assert constants.IMAGE_SIZE == "1024x1024"
