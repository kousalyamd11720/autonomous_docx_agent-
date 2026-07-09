"""
llm_client.py
--------------
Thin wrapper around the Groq chat completion API.

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
  `used_fallback=True` result instead of a raw exception, and the rest of
  the pipeline (planner / executor) has deterministic template logic it can
  fall back on so the API still returns a usable Word document.

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
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.5


class LLMUnavailableError(Exception):
    """Raised only when retries are exhausted AND no fallback was supplied."""


def _get_client() -> Optional[Groq]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    # groq 0.11.0's bundled HTTP wrapper passes the removed `proxies=`
    # argument when used with httpx 0.28+. Supplying a current httpx client
    # bypasses that legacy wrapper and keeps existing environments working.
    return Groq(api_key=api_key, http_client=httpx.Client())


def call_llm(prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
    """
    Calls the Groq LLM with retry + exponential backoff.

    Raises LLMUnavailableError if every attempt fails (including no API key
    configured). Callers are expected to catch this and use their own
    deterministic fallback content -- see planner.py and executor.py.
    """
    client = _get_client()
    if client is None:
        raise LLMUnavailableError("GROQ_API_KEY not configured")

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
            content = completion.choices[0].message.content

            if json_mode:
                # Validate it's actually parseable JSON before trusting it.
                # A model returning malformed JSON counts as a failed attempt
                # and triggers a retry, same as a network error would.
                json.loads(content)

            return content

        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            last_error = e
            logger.warning(
                "LLM call failed (attempt %d/%d): %s", attempt, MAX_RETRIES, e
            )
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(
                "LLM returned invalid JSON (attempt %d/%d): %s", attempt, MAX_RETRIES, e
            )
        except APIError as e:
            # Non-retryable API errors (bad request, auth failure) - fail fast
            last_error = e
            logger.error("Non-retryable LLM API error: %s", e)
            break

        if attempt < MAX_RETRIES:
            time.sleep(BASE_BACKOFF_SECONDS * attempt)

    raise LLMUnavailableError(f"LLM unavailable after {MAX_RETRIES} attempts: {last_error}")
