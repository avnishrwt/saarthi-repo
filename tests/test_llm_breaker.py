"""LLM failures must never slow down or stop the pipeline: retry once, then circuit breaker."""
from pydantic import BaseModel

from agents import llm


class Out(BaseModel):
    x: int


def test_breaker_opens_after_repeated_failures(monkeypatch):
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError("503 UNAVAILABLE high demand")

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "_gemini", boom)
    monkeypatch.setattr(llm, "_anthropic", boom)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setitem(llm._breaker, "fails", 0)
    monkeypatch.setitem(llm._breaker, "open_until", 0.0)

    for _ in range(3):
        assert llm.structured("s", "u", Out) is None      # each: 1 try + 1 retry
    assert len(calls) == 6
    assert not llm.available()                           # breaker open: skip the LLM entirely
    assert llm.structured("s", "u", Out) is None and len(calls) == 6
    assert "paused" in llm.status()


def test_success_resets_failure_count(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setitem(llm._breaker, "fails", 2)
    monkeypatch.setitem(llm._breaker, "open_until", 0.0)
    monkeypatch.setattr(llm, "_gemini", lambda *a: Out(x=1))
    monkeypatch.setattr(llm, "_anthropic", lambda *a: Out(x=1))
    assert llm.structured("s", "u", Out).x == 1
    assert llm._breaker["fails"] == 0
