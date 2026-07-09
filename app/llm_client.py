"""
llm_client.py
-------------
Thin wrapper around the configured LLM providers.

ENGINEERING IMPROVEMENT IMPLEMENTED: Retry & Fallback logic.

Why this one:
- The agent's entire pipeline (planning AND content generation) depends on
  an external LLM call. A single transient failure (rate limit, timeout,
  malformed JSON from the model) should not crash the whole request or
  return nothing to the user.
- Retry: transient errors (timeouts, 429s, connection errors, or the model
  returning invalid JSON when we asked for JSON) are retried with capped
  exponential backoff before giving up.
- Fallback: if all retries are exhausted, the caller gets a clearly-flagged
  the next provider before falling back to deterministic template logic.
- Provider fallback order:
    1. Groq
    2. Gemini free-tier friendly Flash model
    3. Deterministic template logic in planner.py / executor.py

This turns "the LLM is a single point of failure" into "the LLM is a
best-effort enhancement over a deterministic baseline" -- a small change
that makes the agent meaningfully more production-ready.
"""
import json
import os
import time
import logging
from typing import Optional

import httpx
from groq import Groq
from groq import APIError, APIConnectionError, APITimeoutError, RateLimitError

logger = logging.getLogger("agent.llm_client")

MODEL_NAME = "llama-3.3-70b-versatile"
DEFAULT_GEMINI_MODELS = "gemini-2.5-flash-lite,gemini-2.5-flash"
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.5
GEMINI_API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class LLMUnavailableError(Exception):
    """Raised only when all LLM providers are exhausted."""


class ProviderFailure(Exception):
    """Internal exception used when one provider fails and the next should run."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _gemini_models() -> list[str]:
    """
    Returns Gemini models to try in order.

    GEMINI_MODELS supports a comma-separated fallback list. GEMINI_MODEL is
    still supported for backwards compatibility with the earlier single-model
    configuration.
    """
    configured = os.getenv("GEMINI_MODELS") or os.getenv("GEMINI_MODEL")
    raw_models = configured or DEFAULT_GEMINI_MODELS
    return [model.strip() for model in raw_models.split(",") if model.strip()]


def _get_client() -> Optional[Groq]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    # groq 0.11.0's bundled HTTP wrapper passes the removed `proxies=`
    # argument when used with httpx 0.28+. Supplying a current httpx client
    # bypasses that legacy wrapper and keeps existing environments working.
    return Groq(api_key=api_key, http_client=httpx.Client())


def _validate_json_if_needed(content: str, json_mode: bool) -> None:
    if json_mode:
        # Validate it's actually parseable JSON before trusting it.
        # A model returning malformed JSON counts as a failed attempt.
        json.loads(content)


def _call_groq(prompt: str, system_prompt: str, json_mode: bool) -> str:
    """Calls Groq with retry + exponential backoff."""
    client = _get_client()
    if client is None:
        raise ProviderFailure("GROQ_API_KEY not configured")

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            kwargs = {
                "model": MODEL_NAME,
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": 1500,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            completion = client.chat.completions.create(**kwargs)
            content = (completion.choices[0].message.content or "").strip()
            _validate_json_if_needed(content, json_mode)

            return content

        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            last_error = e
            logger.warning(
                "Groq call failed (attempt %d/%d): %s", attempt, MAX_RETRIES, e
            )
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(
                "Groq returned invalid JSON (attempt %d/%d): %s",
                attempt, MAX_RETRIES, e,
            )
        except APIError as e:
            # Non-retryable API errors (bad request, auth failure) - try next provider.
            last_error = e
            logger.error("Non-retryable Groq API error: %s", e)
            break

        if attempt < MAX_RETRIES:
            time.sleep(BASE_BACKOFF_SECONDS * attempt)

    raise ProviderFailure(f"Groq unavailable after {MAX_RETRIES} attempts: {last_error}")


def _extract_gemini_text(data: dict) -> str:
    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise ProviderFailure("Gemini returned an empty response")
    return text


def _call_gemini(prompt: str, system_prompt: str, json_mode: bool) -> str:
    """Calls Gemini with retry + exponential backoff."""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ProviderFailure("GEMINI_API_KEY not configured")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 1500,
        },
    }
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    retry_status_codes = {429, 500, 502, 503, 504}
    max_retries = _env_int("GEMINI_MAX_RETRIES", 2)
    max_retry_after_seconds = _env_int("GEMINI_MAX_RETRY_AFTER_SECONDS", 10)
    last_error: Optional[Exception] = None

    for model in _gemini_models():
        url = GEMINI_API_URL_TEMPLATE.format(model=model)

        for attempt in range(1, max_retries + 1):
            try:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(
                        url,
                        headers={"x-goog-api-key": api_key},
                        json=payload,
                    )

                if response.status_code in retry_status_codes:
                    raise httpx.HTTPStatusError(
                        f"Gemini {model} HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                if response.status_code >= 400:
                    raise ProviderFailure(
                        f"Gemini {model} non-retryable HTTP "
                        f"{response.status_code}: {response.text[:300]}"
                    )

                content = _extract_gemini_text(response.json())
                _validate_json_if_needed(content, json_mode)
                logger.info("Gemini call succeeded via %s.", model)
                return content

            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                last_error = e
                status_code = (
                    e.response.status_code
                    if isinstance(e, httpx.HTTPStatusError) and e.response is not None
                    else None
                )
                logger.warning(
                    "Gemini %s call failed (attempt %d/%d): %s",
                    model, attempt, max_retries, e,
                )

                if status_code == 429:
                    retry_after = e.response.headers.get("retry-after", "0")
                    try:
                        retry_after_seconds = int(float(retry_after))
                    except ValueError:
                        retry_after_seconds = 0

                    if (
                        0 < retry_after_seconds <= max_retry_after_seconds
                        and attempt < max_retries
                    ):
                        logger.info(
                            "Gemini asked to retry after %d seconds.",
                            retry_after_seconds,
                        )
                        time.sleep(retry_after_seconds)
                        continue

                    logger.warning(
                        "Gemini %s is rate-limited; trying next Gemini model.",
                        model,
                    )
                    break

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    "Gemini %s returned invalid JSON (attempt %d/%d): %s",
                    model, attempt, max_retries, e,
                )
            except ProviderFailure:
                raise

            if attempt < max_retries:
                time.sleep(BASE_BACKOFF_SECONDS * attempt)

    raise ProviderFailure(
        f"Gemini unavailable after trying {', '.join(_gemini_models())}: {last_error}"
    )


def call_llm(prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
    """
    Calls Groq first, then Gemini if Groq fails.

    Raises LLMUnavailableError only if every configured provider fails. Callers
    then use deterministic fallback content -- see planner.py and executor.py.
    """
    provider_errors = []

    for provider_name, provider in (
        ("Groq", _call_groq),
        ("Gemini", _call_gemini),
    ):
        try:
            content = provider(prompt, system_prompt, json_mode)
            logger.info("LLM call succeeded via %s.", provider_name)
            return content
        except ProviderFailure as e:
            provider_errors.append(f"{provider_name}: {e}")
            logger.warning("%s provider failed; trying next fallback if available: %s",
                           provider_name, e)

    raise LLMUnavailableError(
        "All LLM providers failed. " + " | ".join(provider_errors)
    )
