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
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from shared import config

log = logging.getLogger("rahat.llm")
T = TypeVar("T", bound=BaseModel)
_clients: dict[str, object] = {}


def enabled() -> bool:
    return config.LLM_PROVIDER in ("gemini", "anthropic")


def name() -> str:
    return f"{config.LLM_PROVIDER}:{config.LLM_MODEL}" if enabled() else "rules"


def _gemini(system: str, user: str, schema: Type[T]) -> Optional[T]:
    from google import genai
    from google.genai import types
    if "gemini" not in _clients:
        _clients["gemini"] = genai.Client(http_options=types.HttpOptions(timeout=int(config.LLM_TIMEOUT_S * 1000)))
    resp = _clients["gemini"].models.generate_content(
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
    import anthropic
    if "anthropic" not in _clients:
        _clients["anthropic"] = anthropic.Anthropic(timeout=config.LLM_TIMEOUT_S, max_retries=1)
    resp = _clients["anthropic"].messages.parse(
        model=config.LLM_MODEL, max_tokens=3000, system=system,
        output_config={"effort": config.LLM_EFFORT},
        messages=[{"role": "user", "content": user}], output_format=schema)
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output


def structured(system: str, user: str, schema: Type[T]) -> Optional[T]:
    if not enabled():
        return None
    try:
        fn = _gemini if config.LLM_PROVIDER == "gemini" else _anthropic
        return fn(system, user, schema)
    except Exception as e:
        log.warning("LLM call failed (%s: %s); caller falls back", type(e).__name__, str(e)[:200])
        return None
