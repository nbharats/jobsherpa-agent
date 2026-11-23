"""Thin Gemini wrapper: rate limiting, exponential backoff and model fallback."""
import logging
import time
from collections.abc import Callable
from typing import Any

import config

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (5, 10, 20, 40)   # retry delays from the assignment
RETRYABLE_CODES = {429, 500, 503}

_client: Any = None
_last_call: float | None = None


class LLMError(Exception):
    """Raised when Gemini cannot produce a reply; callers use a fallback."""


def is_retryable(exc: Exception) -> bool:
    """True for rate-limit (429) and temporary server (500, 503) errors."""
    return getattr(exc, "code", None) in RETRYABLE_CODES


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
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        try:
            return call()
        except Exception as exc:
            if not is_retryable(exc) or attempt == len(BACKOFF_SECONDS):
                raise
            delay = BACKOFF_SECONDS[attempt]
            logger.warning("Gemini returned %s; retrying in %ss",
                           getattr(exc, "code", "an error"), delay)
            pause(delay)
    raise RuntimeError("unreachable")


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