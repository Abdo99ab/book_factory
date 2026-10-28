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
    # PARKED: data protection (spec §14) - every OpenAI call sends child data (photo, name); DPA / region / zero-retention goes here
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

# Spread art is ONE wide picture, so "auto" means the largest LANDSCAPE size the model offers.
# Listed largest first; NEVER includes a square — 1024x1024 was caching as landscape and cropping
# heads off. Sheets / covers use SQUARE_SIZES separately.
LANDSCAPE_SIZES = ("2048x1024", "1792x1024", "1536x1024")
IMAGE_SIZE_FALLBACK = LANDSCAPE_SIZES  # the default ladder
# Everything that is not spread art (hero / set / anchor sheets, cover panels) is square.
SQUARE_SIZES = ("2048x2048", "1536x1536", "1024x1024")
_LADDERS = {"landscape": LANDSCAPE_SIZES, "square": SQUARE_SIZES}

_VALID_QUALITY = {"low", "medium", "high", "auto"}
# (model, aspect) -> first size the model accepted in this process
_WORKING_SIZE: dict[tuple[str, str], str] = {}
# Step-down / reject notes from the last image call (flushed by illustrate into job events).
_LAST_SIZE_EVENTS: list[dict] = []


def reset_size_cache() -> None:
    _WORKING_SIZE.clear()
    _LAST_SIZE_EVENTS.clear()


def take_size_events() -> list[dict]:
    """Drain step-down / reject notes from the last ``image_edit`` / ``image_generate`` call."""
    events = list(_LAST_SIZE_EVENTS)
    _LAST_SIZE_EVENTS.clear()
    return events


def _is_square_size(size: str) -> bool:
    value = str(size or "").strip().lower()
    if value in {"", "auto", "landscape"}:
        return False
    if value == "square":
        return True
    if "x" not in value:
        return False
    w, _, h = value.partition("x")
    return w.isdigit() and h.isdigit() and int(w) == int(h)


def image_quality(requested: str | None = None) -> str:
    """Quality from the IMAGE_QUALITY env var at call time (default high when unset).

    Never hard-coded at the call sites: an explicit ``requested`` wins, then the environment, then
    the constants default. Unknown values fall back to high rather than silently going low.
    """
    value = (requested or os.getenv("IMAGE_QUALITY") or IMAGE_QUALITY or "high").strip().lower()
    return value if value in _VALID_QUALITY else "high"


def _ladder(value: str, aspect: str) -> tuple[str, ...] | None:
    """The size ladder for an 'auto' style request, or None for an explicit WxH size."""
    if value in {"", "auto"}:
        return _LADDERS.get(aspect, LANDSCAPE_SIZES)
    if value in _LADDERS:
        return _LADDERS[value]
    return None


def image_size_candidates(requested: str | None = None, aspect: str = "landscape") -> list[str]:
    """Sizes to try, in order. ``auto`` -> the landscape (or ``aspect``) ladder, largest first.

    Landscape never includes a square size (even if the caller passed one explicitly mixed in).
    """
    value = (requested if requested is not None else IMAGE_SIZE or "auto").strip().lower()
    ladder = _ladder(value, aspect)
    if ladder is not None:
        return list(ladder)
    if aspect == "landscape" and _is_square_size(value):
        return list(LANDSCAPE_SIZES)
    return [value]


def square_image_size() -> str:
    """Size request for square art (sheets, covers): IMAGE_SIZE when it is an explicit square, else 'square'."""
    value = (IMAGE_SIZE or "auto").strip().lower()
    if "x" in value:
        w, _, h = value.partition("x")
        if w.isdigit() and w == h:
            return value
    return "square"


def largest_image_size(
    client=None,
    aspect: str = "landscape",
    *,
    requested: str | None = None,
    supported=None,
) -> str:
    """The size to ask for next: the largest ``aspect`` size the model offers.

    Tries 2048x1024, 1792x1024, 1536x1024, 1024x1024 (landscape; squares for ``aspect="square"``).
    The first size the model accepts is cached per model by the real request (``_with_size_fallback``
    steps down on an invalid-size error), so this returns the cached size once one is known, else
    the largest candidate. ``requested`` defaults to IMAGE_SIZE; an explicit WxH wins as-is.
    ``supported`` restricts the ladder (tests / models with a known list). ``client`` is accepted so
    a caller may pass the live client; no image is generated just to probe a size.
    """
    del client
    candidates = image_size_candidates(requested, aspect)
    if supported is not None:
        supported = {str(s).lower() for s in supported}
        candidates = [c for c in candidates if c in supported]
    if not candidates:
        raise ValueError("No supported image size among the fallback list.")
    cached = _WORKING_SIZE.get((IMAGE_MODEL, aspect))
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
    """Run ``call(size)``; on an invalid-size error step down the ladder (largest first).

    A square is never cached as the working size for ``aspect="landscape"``. Each step-down is
    recorded in ``_LAST_SIZE_EVENTS`` (``image_size_stepdown``) so the job log can show why.
    """
    requested = str(size if size is not None else IMAGE_SIZE or "auto").strip().lower()
    aspect = "square" if requested == "square" or _is_square_size(requested) else "landscape"
    ladder_request = requested if not (aspect == "landscape" and _is_square_size(requested)) else "auto"
    candidates = image_size_candidates(ladder_request, aspect)
    if aspect == "landscape":
        candidates = [c for c in candidates if not _is_square_size(c)] or list(LANDSCAPE_SIZES)
    if len(candidates) > 1:
        start = largest_image_size(aspect=aspect, requested=ladder_request)
        if start in candidates:
            candidates = candidates[candidates.index(start) :]
    last_error = None
    for index, candidate in enumerate(candidates):
        try:
            result = with_retries(lambda c=candidate: call(c), f"{description} @{candidate}")
            # Never cache a square as the landscape working size (defence in depth).
            if not (aspect == "landscape" and _is_square_size(candidate)):
                if _WORKING_SIZE.get((IMAGE_MODEL, aspect)) != candidate:
                    _WORKING_SIZE[(IMAGE_MODEL, aspect)] = candidate
                    logger.info("Image size for %s (%s): %s accepted", IMAGE_MODEL, aspect, candidate)
            return result
        except Exception as error:  # noqa: BLE001
            last_error = error
            if _is_size_error(error) and index + 1 < len(candidates):
                msg = f"{type(error).__name__}: {error}"
                logger.warning("%s: size %s rejected, falling back (%s)", description, candidate, error)
                _LAST_SIZE_EVENTS.append(
                    {
                        "type": "image_size_stepdown",
                        "message": f"{description}: size {candidate} rejected; trying {candidates[index + 1]}.",
                        "size": candidate,
                        "next_size": candidates[index + 1],
                        "error": msg,
                    }
                )
                continue
            raise
    raise last_error  # pragma: no cover


def image_edit(
    client,
    image_path,
    prompt,
    size=None,
    quality=None,
    description="Image edit",
    mask_path=None,
):
    """images.edit with one or several reference images (path or list of paths).

    Optional ``mask_path``: PNG whose transparent pixels mark the region to edit
    (used for background extension after a shift).
    """
    paths = [image_path] if isinstance(image_path, (str, Path)) else list(image_path)
    quality = image_quality(quality)

    def call(chosen_size):
        handles = [Path(p).open("rb") for p in paths]
        mask_handle = Path(mask_path).open("rb") if mask_path else None
        try:
            kwargs = {
                "model": IMAGE_MODEL,
                "image": handles[0] if len(handles) == 1 else handles,
                "prompt": prompt,
                "quality": quality,
                "size": chosen_size,
            }
            if mask_handle is not None:
                kwargs["mask"] = mask_handle
            return client.images.edit(**kwargs)
        finally:
            for handle in handles:
                handle.close()
            if mask_handle is not None:
                mask_handle.close()

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
