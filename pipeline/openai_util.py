import logging
import os
import time
from pathlib import Path

from openai import OpenAI

from .constants import IMAGE_MODEL, OPENAI_MAX_ATTEMPTS, OPENAI_TIMEOUT_SECONDS, TEXT_MODEL

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


def chat_json(client, system, user, description="Text request"):
    def request():
        return client.chat.completions.create(
            model=TEXT_MODEL,
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

    result = with_retries(request, description)
    return result.choices[0].message.content


def image_edit(client, image_path, prompt, size, quality="low", description="Image edit"):
    def request():
        with Path(image_path).open("rb") as image:
            return client.images.edit(
                model=IMAGE_MODEL,
                image=image,
                prompt=prompt,
                quality=quality,
                size=size,
            )

    return with_retries(request, description)


def image_generate(client, prompt, size, quality="low", description="Image generate"):
    def request():
        return client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            quality=quality,
            size=size,
        )

    return with_retries(request, description)
