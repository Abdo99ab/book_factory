"""Humanize AI manuscript/page copy with blader/humanizer via OpenAI Skills API.

Flow:
1. POST /v1/skills once (zip of skills/humanizer/) → cache skill_id
2. Responses API + hosted shell skill_reference runs the skill in embedded mode
3. If Skills/Responses mount fails, fall back to the same SKILL.md as chat instructions
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from pathlib import Path

import httpx

from .constants import (
    BASE_DIR,
    PREFERENCES_DIR,
    TEXT_MODEL,
)
from .job_log import append_event, read_json, write_json
from .openai_util import chat_json, with_retries

logger = logging.getLogger(__name__)

SKILL_DIR = BASE_DIR / "skills" / "humanizer"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_CACHE = PREFERENCES_DIR.parent / "skills" / "humanizer_skill.json"
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")

EMBEDDED_MODE_RULES = """
You are using the humanizer skill in **Embedded mode**.
Return ONLY the final rewritten text (or the JSON object requested below).
Do not return drafts, pattern lists, or explanations.
This is children's picture-book prose: warm, concrete, age-appropriate.
Keep every plot fact, the hero child's name, and supporting character names.
Do not invent new characters, scenes, morals, or details.
Fiction invented detail already in the source may stay; do not add new facts.
"""


def humanize_enabled() -> bool:
    return os.getenv("HUMANIZE_STORY", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _api_key(client) -> str:
    key = getattr(client, "api_key", None) or os.getenv("_OPENAI_API_KEY") or os.getenv(
        "OPENAI_API_KEY"
    )
    if not key:
        raise RuntimeError("OpenAI API key missing for humanizer skill upload.")
    return key


def _load_skill_markdown() -> str:
    if not SKILL_MD.is_file():
        raise FileNotFoundError(
            f"Missing {SKILL_MD}. Vendored blader/humanizer SKILL.md is required."
        )
    return SKILL_MD.read_text(encoding="utf-8")


def _skill_zip_bytes() -> bytes:
    if not SKILL_MD.is_file():
        raise FileNotFoundError(f"Missing {SKILL_MD}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("humanizer/SKILL.md", SKILL_MD.read_bytes())
        license_path = SKILL_DIR / "LICENSE"
        if license_path.is_file():
            zf.writestr("humanizer/LICENSE", license_path.read_bytes())
    return buf.getvalue()


def _wait_for_skill_version(api_key: str, skill_id: str, version: str, attempts: int = 8):
    """Uploaded skill versions can 404 briefly before they are ready."""
    import time

    url = f"{OPENAI_API_BASE}/skills/{skill_id}/versions/{version}"
    headers = {"Authorization": f"Bearer {api_key}"}
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=60) as http:
                response = http.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            if response.status_code != 404:
                response.raise_for_status()
            last_error = f"404 waiting for skill version {version}"
        except Exception as error:  # noqa: BLE001
            last_error = error
        time.sleep(min(2 * attempt, 10))
    raise RuntimeError(f"Skill {skill_id} version {version} not ready: {last_error}")


def ensure_humanizer_skill_id(client) -> dict:
    """Upload blader/humanizer via POST /v1/skills (or reuse cached / env id)."""
    env_id = (os.getenv("HUMANIZER_SKILL_ID") or "").strip()
    cache = read_json(SKILL_CACHE) if SKILL_CACHE.is_file() else None
    api_key = _api_key(client)

    if env_id:
        meta = {"id": env_id, "default_version": os.getenv("HUMANIZER_SKILL_VERSION") or "latest"}
        write_json(SKILL_CACHE, {**meta, "source": "env"})
        return meta

    if cache and cache.get("id"):
        return cache

    zip_bytes = _skill_zip_bytes()

    def upload():
        with httpx.Client(timeout=180) as http:
            response = http.post(
                f"{OPENAI_API_BASE}/skills",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"files": ("humanizer.zip", zip_bytes, "application/zip")},
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"POST /v1/skills failed ({response.status_code}): {response.text[:500]}"
                )
            return response.json()

    skill = with_retries(upload, "Upload humanizer skill (POST /v1/skills)")
    skill_id = skill.get("id")
    version = str(skill.get("default_version") or skill.get("latest_version") or "latest")
    if not skill_id:
        raise RuntimeError(f"Skill upload returned no id: {skill}")

    if version != "latest":
        try:
            _wait_for_skill_version(api_key, skill_id, version)
        except Exception as error:  # noqa: BLE001
            logger.warning("Skill version wait skipped: %s", error)

    meta = {
        "id": skill_id,
        "default_version": version,
        "latest_version": skill.get("latest_version"),
        "name": skill.get("name") or "humanizer",
        "source": "upload",
    }
    SKILL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    write_json(SKILL_CACHE, meta)
    logger.info("Humanizer skill ready: %s (version %s)", skill_id, version)
    return meta


def _extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty humanizer response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        return json.loads(match.group(0))


def _humanize_via_skills_api(client, user_prompt: str, description: str) -> str:
    skill = ensure_humanizer_skill_id(client)
    skill_ref = {
        "type": "skill_reference",
        "skill_id": skill["id"],
    }
    version = skill.get("default_version")
    if version and version != "latest":
        skill_ref["version"] = str(version)

    def request():
        return client.responses.create(
            model=TEXT_MODEL,
            tools=[
                {
                    "type": "shell",
                    "environment": {
                        "type": "container_auto",
                        "skills": [skill_ref],
                    },
                }
            ],
            input=(
                "Use the humanizer skill (blader/humanizer).\n"
                f"{EMBEDDED_MODE_RULES}\n\n"
                f"{user_prompt}"
            ),
        )

    result = with_retries(request, description)
    text = getattr(result, "output_text", None)
    if not text:
        # Older SDK shapes
        chunks = []
        for item in getattr(result, "output", None) or []:
            for content in getattr(item, "content", None) or []:
                value = getattr(content, "text", None)
                if value:
                    chunks.append(value)
        text = "\n".join(chunks).strip()
    if not text:
        raise RuntimeError("Skills API humanizer returned no output_text")
    return text


def _humanize_via_skill_markdown(client, user_prompt: str, description: str) -> str:
    """Fallback: same skill instructions without hosted shell mount."""
    skill_body = _load_skill_markdown()
    system = (
        "Follow the humanizer skill instructions below exactly.\n"
        f"{EMBEDDED_MODE_RULES}\n\n"
        "----- SKILL.md (blader/humanizer) -----\n"
        f"{skill_body}\n"
        "----- end SKILL.md -----"
    )
    return chat_json(
        client,
        system,
        user_prompt,
        description=f"{description} (SKILL.md fallback)",
    )


def humanize_json(client, payload: dict, description: str) -> dict:
    """Run humanizer on a JSON payload; expects JSON object back."""
    use_api = os.getenv("HUMANIZE_VIA_SKILLS_API", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    user_prompt = (
        "Humanize the string values in this JSON that hold reader-facing prose. "
        "Keep the same JSON keys and structure. Do not rename keys. "
        "Return ONLY the JSON object.\n\n"
        f"{json.dumps(payload, ensure_ascii=True)}"
    )
    raw = None
    if use_api:
        try:
            raw = _humanize_via_skills_api(client, user_prompt, description)
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "%s Skills API path failed (%s); falling back to SKILL.md chat.",
                description,
                error,
            )
    if raw is None:
        raw = _humanize_via_skill_markdown(client, user_prompt, description)
    return _extract_json_object(raw)


def humanize_story_fields(client, job_dir: Path, story: dict) -> dict:
    """Humanize rebuilt_story (+ synopsis) after generation; keep structure fields."""
    if not humanize_enabled():
        return story

    source = {
        "rebuilt_story": story.get("rebuilt_story") or "",
        "synopsis": story.get("synopsis") or "",
    }
    if not source["rebuilt_story"].strip():
        return story

    append_event(
        job_dir,
        "humanize_story_start",
        "Humanizing manuscript with blader/humanizer skill.",
    )
    try:
        rewritten = humanize_json(client, source, "Humanize story prose")
    except Exception as error:  # noqa: BLE001
        logger.exception("Story humanize failed")
        append_event(job_dir, "humanize_story_failed", str(error))
        # Non-fatal: keep AI draft so the pipeline can continue.
        story = {
            **story,
            "humanized": False,
            "humanize_error": str(error),
        }
        write_json(job_dir / "story" / "rebuilt_story.json", story)
        return story

    story = {
        **story,
        "rebuilt_story": rewritten.get("rebuilt_story") or story.get("rebuilt_story"),
        "synopsis": rewritten.get("synopsis") or story.get("synopsis"),
        "humanized": True,
        "humanize_error": None,
    }
    write_json(job_dir / "story" / "rebuilt_story.json", story)
    (job_dir / "story" / "rebuilt_story.txt").write_text(
        story.get("rebuilt_story") or "", encoding="utf-8"
    )
    (job_dir / "story" / "rebuilt_story.ai.txt").write_text(
        source["rebuilt_story"], encoding="utf-8"
    )
    append_event(job_dir, "humanize_story_done", "Manuscript humanized.")
    return story


def humanize_page_texts(client, job_dir: Path, pages: list) -> list:
    """Humanize the reader-facing page text that appears in the printed book."""
    if not humanize_enabled():
        return pages

    batch = [
        {
            "number": page.get("number"),
            "text": page.get("text") or "",
        }
        for page in pages
        if not page.get("blank") and (page.get("text") or "").strip()
    ]
    if not batch:
        return pages

    append_event(
        job_dir,
        "humanize_pages_start",
        f"Humanizing {len(batch)} page text(s) with blader/humanizer skill.",
    )
    try:
        rewritten = humanize_json(
            client,
            {"pages": batch},
            "Humanize page texts",
        )
    except Exception as error:  # noqa: BLE001
        logger.exception("Page humanize failed")
        append_event(job_dir, "humanize_pages_failed", str(error))
        write_json(
            job_dir / "story" / "pages.json",
            {"pages": pages, "humanized": False, "humanize_error": str(error)},
        )
        return pages

    by_number = {
        int(item["number"]): item.get("text") or ""
        for item in (rewritten.get("pages") or [])
        if item.get("number") is not None
    }
    updated = []
    for page in pages:
        item = dict(page)
        number = int(item.get("number") or 0)
        if number in by_number and by_number[number].strip():
            item["text"] = by_number[number].strip()
        updated.append(item)

    write_json(
        job_dir / "story" / "pages.json",
        {"pages": updated, "humanized": True},
    )
    append_event(
        job_dir,
        "humanize_pages_done",
        f"Humanized text on {len(by_number)} page(s).",
    )
    return updated
