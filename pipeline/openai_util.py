import logging
import os
import time
from pathlib import Path

from openai import OpenAI

from .constants import (
    IMAGE_MODEL,
    IMAGE_QUALITY,
    IMAGE_SIZE,
    OPENAI_MAX_ATTEMPTS,
    OPENAI_TIMEOUT_SECONDS,
    TEXT_MODEL,
)

logger = logging.getLogger(__name__)


def create_client(api_key=None):
    return OpenAI(
        api_key=api_key or os.getenv("_OPENAI_API_KEY"),
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _is_retryable(error):
    status_code = getattr(error, "status_code", None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    if status_code in {408, 409, 429} or (status_code is not None and status_code >= 500):
        return True
    text = f"{type(error).__name__} {error}".lower()
    return any(
        phrase in text
        for phrase in (
            "timeout",
            "timed out",
            "connection error",
            "connection reset",
            "temporarily unavailable",
            "rate limit",
            "server error",
        )
    )


def with_retries(request, description):
    for attempt in range(1, OPENAI_MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        logger.info("%s started (attempt %d/%d)", description, attempt, OPENAI_MAX_ATTEMPTS)
        try:
            result = request()
            logger.info(
                "%s finished in %.1fs (attempt %d/%d)",
                description,
                time.perf_counter() - started,
                attempt,
                OPENAI_MAX_ATTEMPTS,
            )
            return result
        except Exception as error:
            logger.warning(
                "%s failed in %.1fs (attempt %d/%d): %s",
                description,
                time.perf_counter() - started,
                attempt,
                OPENAI_MAX_ATTEMPTS,
                error,
            )
            if attempt == OPENAI_MAX_ATTEMPTS or not _is_retryable(error):
                raise
            delay = min(5 * (2 ** (attempt - 1)), 30)
            time.sleep(delay)


def _model_allows_custom_temperature(model: str | None = None) -> bool:
    """gpt-6* reasoning models only accept the default temperature (1)."""
    name = (model or TEXT_MODEL or "").strip().lower()
    return not name.startswith("gpt-6")


def _chat_kwargs(temperature: float | None = None) -> dict:
    kwargs = {
        "model": TEXT_MODEL,
        "response_format": {"type": "json_object"},
    }
    if temperature is not None and _model_allows_custom_temperature():
        kwargs["temperature"] = temperature
    return kwargs


def chat_json(client, system, user, description="Text request"):
    def request():
        return client.chat.completions.create(
            **_chat_kwargs(temperature=0.7),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

    result = with_retries(request, description)
    return result.choices[0].message.content


def chat_json_vision(client, system, user_content, description="Vision request", temperature=0.3):
    """user_content: string or OpenAI multimodal content list (text + image_url parts)."""
    def request():
        return client.chat.completions.create(
            **_chat_kwargs(temperature=temperature),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )

    result = with_retries(request, description)
    return result.choices[0].message.content


# --- Native image size (no upscaler: whatever the model returns is what we print) -------------

# Square sizes, largest first. Page art is square (216 mm box), so we only try squares.
IMAGE_SIZE_FALLBACK = ("4096x4096", "3072x3072", "2048x2048", "1536x1536", "1024x1024")
_VALID_QUALITY = {"low", "medium", "high", "auto"}
_WORKING_SIZE: dict[str, str] = {}


def reset_size_cache() -> None:
    _WORKING_SIZE.clear()


def image_quality(requested: str | None = None) -> str:
    """IMAGE_QUALITY from constants (default high); never silently 'low'."""
    value = (requested or IMAGE_QUALITY or "high").strip().lower()
    return value if value in _VALID_QUALITY else "high"


def image_size_candidates(requested: str | None = None) -> list[str]:
    """Sizes to try, in order. IMAGE_SIZE=auto -> largest-first fallback list."""
    value = (requested if requested is not None else IMAGE_SIZE or "auto").strip().lower()
    if value in {"", "auto"}:
        return list(IMAGE_SIZE_FALLBACK)
    return [value]


def largest_image_size(requested: str | None = None, supported=None) -> str:
    """Largest square size to ask for.

    ``requested`` defaults to IMAGE_SIZE. An explicit size is returned as-is. With
    ``auto`` the first entry of IMAGE_SIZE_FALLBACK that is in ``supported`` wins (all
    candidates are allowed when ``supported`` is None). A size that already worked in
    this process is preferred, so we do not re-probe on every page.
    """
    candidates = image_size_candidates(requested)
    if supported is not None:
        supported = {str(s).lower() for s in supported}
        candidates = [c for c in candidates if c in supported]
    if not candidates:
        raise ValueError("No supported image size among the fallback list.")
    cached = _WORKING_SIZE.get(IMAGE_MODEL)
    if cached and cached in candidates and len(candidates) > 1:
        return cached
    return candidates[0]


def _is_size_error(error) -> bool:
    status = getattr(error, "status_code", None)
    text = f"{type(error).__name__} {error}".lower()
    if status not in {None, 400, 422}:
        return False
    return "size" in text and any(
        word in text for word in ("invalid", "unsupported", "not supported", "must be one of", "unknown")
    )


def _with_size_fallback(call, size, description):
    """Run ``call(size)``; on an invalid-size error step down the fallback list."""
    requested = str(size or IMAGE_SIZE or "auto").strip().lower()
    candidates = image_size_candidates(requested)
    if requested == "auto":
        start = largest_image_size("auto")
        candidates = candidates[candidates.index(start) :]
    last_error = None
    for index, candidate in enumerate(candidates):
        try:
            result = with_retries(lambda c=candidate: call(c), f"{description} @{candidate}")
            _WORKING_SIZE[IMAGE_MODEL] = candidate
            return result
        except Exception as error:  # noqa: BLE001
            last_error = error
            if _is_size_error(error) and index + 1 < len(candidates):
                logger.warning("%s: size %s rejected, falling back (%s)", description, candidate, error)
                continue
            raise
    raise last_error  # pragma: no cover


def image_edit(client, image_path, prompt, size=None, quality=None, description="Image edit"):
    """images.edit with one or several reference images (path or list of paths)."""
    paths = [image_path] if isinstance(image_path, (str, Path)) else list(image_path)
    quality = image_quality(quality)

    def call(chosen_size):
        handles = [Path(p).open("rb") for p in paths]
        try:
            return client.images.edit(
                model=IMAGE_MODEL,
                image=handles[0] if len(handles) == 1 else handles,
                prompt=prompt,
                quality=quality,
                size=chosen_size,
            )
        finally:
            for handle in handles:
                handle.close()

    return _with_size_fallback(call, size, description)


def image_generate(client, prompt, size=None, quality=None, description="Image generate"):
    quality = image_quality(quality)

    def call(chosen_size):
        return client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            quality=quality,
            size=chosen_size,
        )

    return _with_size_fallback(call, size, description)
