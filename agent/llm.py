"""Thin Gemini wrapper: rate limiting, exponential backoff and model fallback."""
import logging
import time
from collections.abc import Callable
from typing import Any

import config

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (5, 10, 20, 40)   
SERVER_ERROR_CODES = {500, 503}
SERVER_ERROR_RETRIES = 2            

_client: Any = None
_last_call: float | None = None


class LLMError(Exception):
    """Raised when Gemini cannot produce a reply; callers use a fallback."""


def max_retries(exc: Exception) -> int:
    """Retries allowed after this error: 429 gets the full schedule, 500/503 get two."""
    code = getattr(exc, "code", None)
    if code == 429:
        return len(BACKOFF_SECONDS)
    if code in SERVER_ERROR_CODES:
        return SERVER_ERROR_RETRIES
    return 0


def is_retryable(exc: Exception) -> bool:
    return max_retries(exc) > 0


def _throttle() -> None:
    """Keep calls at least 60 / GEMINI_MAX_RPM seconds apart."""
    global _last_call
    if config.GEMINI_MAX_RPM > 0 and _last_call is not None:
        wait = _last_call + 60 / config.GEMINI_MAX_RPM - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    _last_call = time.monotonic()


def _get_client() -> Any:
    """Create the Gemini client on first use."""
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise LLMError("GEMINI_API_KEY is not set")
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def call_with_backoff(
    call: Callable[[], str],
    sleep: Callable[[float], None] | None = None,
) -> str:
    """Run call(); on a retryable error wait 5, 10, 20, then 40 seconds and retry."""
    pause = sleep if sleep is not None else time.sleep
    attempt = 0
    while True:
        try:
            return call()
        except Exception as exc:
            if attempt >= max_retries(exc):
                raise
            delay = BACKOFF_SECONDS[attempt]
            logger.warning("Gemini returned %s; retrying in %ss",
                           getattr(exc, "code", "an error"), delay)
            pause(delay)
            attempt += 1


def generate_text(prompt: str, *, temperature: float = 0.2, json_output: bool = False) -> str:
    """Return Gemini's reply, trying the fallback models if one is exhausted.

    Raises LLMError if no model can answer.
    """
    models = [m for m in [config.GEMINI_MODEL, *config.GEMINI_FALLBACK_MODELS] if m]
    if not models:
        raise LLMError("GEMINI_MODEL is not set")

    settings: dict[str, Any] = {"temperature": temperature}
    if json_output:
        settings["response_mime_type"] = "application/json"

    last_error: Exception | None = None
    for model in models:
        def call(model: str = model) -> str:
            _throttle()
            response = _get_client().models.generate_content(
                model=model, contents=prompt, config=settings
            )
            return response.text or ""

        try:
            return call_with_backoff(call)
        except LLMError:
            raise                      # e.g. no API key: other models cannot help
        except Exception as exc:
            last_error = exc
            logger.warning("Gemini model '%s' failed (%s); trying the next one", model, exc)
    raise LLMError(f"All Gemini models failed: {last_error}") from last_error
