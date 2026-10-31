"""
One place for every LLM call, so switching provider is one env var (brief open
decision #1: test 2-3 LLMs on 20 Hinglish messages and pick one).

    RAHAT_LLM=gemini     (default when GEMINI_API_KEY is set)   -> google-genai SDK
    RAHAT_LLM=anthropic  (when ANTHROPIC_API_KEY is set)         -> anthropic SDK
    RAHAT_LLM=rules      (no key)                                -> callers use their non-LLM fallback

structured(system, user, schema) returns a validated pydantic object, or None on ANY
failure (network, quota, refusal, invalid JSON). Callers must always have a fallback.
Keys come from .env only. Never commit them, never paste them in prompts.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from shared import config

log = logging.getLogger("rahat.llm")
T = TypeVar("T", bound=BaseModel)

# One client PER THREAD: sharing one SDK client across the worker threads gave
# "WinError 10038 not a socket" under a burst.
_local = threading.local()
# At most N LLM calls at once: a 6-worker burst hit the free-tier rate limit (429).
_slots = threading.BoundedSemaphore(int(os.getenv("RAHAT_LLM_CONCURRENCY", "2")))
RETRYABLE = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "10038", "ReadError", "ConnectError")

# Circuit breaker: after 3 failures in a row, skip the LLM for 60 s and use rules at once,
# so a provider outage ("503 high demand") does not make every message wait ~20 s.
BREAKER_FAILS, BREAKER_COOLDOWN_S = 3, 60.0
_breaker = {"fails": 0, "open_until": 0.0}
_breaker_lock = threading.Lock()


def available() -> bool:
    return enabled() and time.monotonic() >= _breaker["open_until"]


def status() -> str:
    if not enabled():
        return "rules"
    left = _breaker["open_until"] - time.monotonic()
    return f"{name()} (paused {left:.0f}s: provider errors, using rules)" if left > 0 else name()


def _record(ok: bool) -> None:
    with _breaker_lock:
        if ok:
            _breaker["fails"] = 0
            return
        _breaker["fails"] += 1
        if _breaker["fails"] >= BREAKER_FAILS:
            _breaker["open_until"] = time.monotonic() + BREAKER_COOLDOWN_S
            _breaker["fails"] = 0
            log.warning("LLM failed %d times in a row: using rules for %.0f s", BREAKER_FAILS, BREAKER_COOLDOWN_S)


def _client(kind: str):
    c = getattr(_local, kind, None)
    if c is None:
        if kind == "gemini":
            from google import genai
            from google.genai import types
            c = genai.Client(http_options=types.HttpOptions(timeout=int(config.LLM_TIMEOUT_S * 1000)))
        else:
            import anthropic
            c = anthropic.Anthropic(timeout=config.LLM_TIMEOUT_S, max_retries=1)
        setattr(_local, kind, c)
    return c


def enabled() -> bool:
    return config.LLM_PROVIDER in ("gemini", "anthropic")


def name() -> str:
    return f"{config.LLM_PROVIDER}:{config.LLM_MODEL}" if enabled() else "rules"


def _gemini(system: str, user: str, schema: Type[T]) -> Optional[T]:
    from google.genai import types
    resp = _client("gemini").models.generate_content(
        model=config.LLM_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    parsed = resp.parsed
    if isinstance(parsed, schema):
        return parsed
    if resp.text:                       # SDK could not parse: validate the raw JSON ourselves
        return schema.model_validate_json(resp.text)
    return None


def _anthropic(system: str, user: str, schema: Type[T]) -> Optional[T]:
    resp = _client("anthropic").messages.parse(
        model=config.LLM_MODEL, max_tokens=3000, system=system,
        output_config={"effort": config.LLM_EFFORT},
        messages=[{"role": "user", "content": user}], output_format=schema)
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output


def structured(system: str, user: str, schema: Type[T]) -> Optional[T]:
    if not available():
        return None
    fn = _gemini if config.LLM_PROVIDER == "gemini" else _anthropic
    for attempt in (1, 2):                      # one retry for rate limit / overload / dropped socket
        if not available():
            return None
        try:
            with _slots:
                out = fn(system, user, schema)
            _record(True)
            return out
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            daily_quota = "quota" in msg.lower() and "per day" in msg.lower()
            if attempt == 1 and not daily_quota and any(k in msg for k in RETRYABLE):
                time.sleep(1.0)
                continue
            _record(False)
            log.warning("LLM call failed (%s); caller falls back", msg[:200])
            return None
    return None
