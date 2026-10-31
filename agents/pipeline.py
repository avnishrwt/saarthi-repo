r"""
Agent layer as a LangGraph graph (PRD section 8):

    transcribe -> extract -> verify -> dispatch
         |            |          \-> END (information / noise)
         \-> END (no text: human review)

Extraction runs WITHOUT the store lock (it is the slow LLM call and messages are
independent), so a burst extracts in parallel. verify + dispatch run under the
store lock so two duplicates arriving together cannot create two incidents.
The optimizer is NOT an agent: it runs after this graph (backend/optimizer.py).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, TypedDict

from agents import dispatcher, extractor, speech, verifier
from shared.schemas import Extraction, Incident, Message, MessageStatus

log = logging.getLogger("rahat.pipeline")


class PipelineState(TypedDict, total=False):
    msg: Message
    ex: Optional[Extraction]
    incident: Optional[Incident]
    why: str
    trace: list[str]
    use_llm: Optional[bool]
    now: datetime
    surge_hazard: str


def build(store, now_fn):
    def transcribe(s: PipelineState) -> dict[str, Any]:
        msg = s["msg"]
        trace = list(s.get("trace", []))
        if msg.audio_url and not msg.raw_text:
            text, engine = speech.transcribe(msg.audio_url)
            trace.append(f"speech-to-text: {engine}")
            if text:
                msg.raw_text = text
                msg.meta["transcribed_by"] = engine
            else:
                msg.meta["review_reason"] = "audio received, no speech-to-text engine configured"
        return {"msg": msg, "trace": trace}

    def extract(s: PipelineState) -> dict[str, Any]:
        msg = s["msg"]
        if msg.meta.get("preset_extraction"):          # proactive check / IVR: fields known already
            ex = Extraction.model_validate(msg.meta["preset_extraction"])
        else:
            ex = extractor.extract(msg.raw_text, use_llm=s.get("use_llm"))
        msg.extraction = ex
        msg.language = msg.language or ex.language
        return {"msg": msg, "ex": ex, "trace": s.get("trace", []) + [f"extractor: {ex.extractor}"]}

    def verify(s: PipelineState) -> dict[str, Any]:
        with store.lock:
            inc, why = verifier.verify(store, s["msg"], s["ex"], s.get("now") or now_fn())
            if inc is not None:
                store.put("incidents", inc)
            store.put("messages", s["msg"])
        return {"incident": inc, "why": why, "trace": s.get("trace", []) + [f"verifier: {why}"]}

    def dispatch(s: PipelineState) -> dict[str, Any]:
        with store.lock:
            inc = store.get("incidents", s["incident"].id)
            inc.required_team_types = dispatcher.required_team_types(inc, s.get("surge_hazard", "flood"))
            store.put("incidents", inc)
        return {"incident": inc,
                "trace": s.get("trace", []) + [f"dispatcher: {dispatcher.explain(inc.required_team_types)}"]}

    def to_review(s: PipelineState) -> dict[str, Any]:
        msg = s["msg"]
        msg.status = MessageStatus.review
        msg.meta.setdefault("review_reason", "no text to process")
        with store.lock:
            store.put("messages", msg)
        return {"incident": None, "why": msg.meta["review_reason"]}

    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import RetryPolicy
    except ImportError:                                    # keep the demo alive without langgraph
        log.warning("langgraph not installed; running agents sequentially")
        return _Sequential(transcribe, extract, verify, dispatch, to_review)

    g = StateGraph(PipelineState)
    g.add_node("transcribe", transcribe)
    g.add_node("extract", extract, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("verify", verify)
    g.add_node("dispatch", dispatch)
    g.add_node("review", to_review)
    g.add_edge(START, "transcribe")
    g.add_conditional_edges("transcribe", lambda s: "extract" if s["msg"].raw_text.strip() else "review",
                            ["extract", "review"])
    g.add_edge("extract", "verify")
    g.add_conditional_edges("verify", lambda s: "dispatch" if s.get("incident") else END, ["dispatch", END])
    g.add_edge("dispatch", END)
    g.add_edge("review", END)
    return g.compile()


class _Sequential:
    def __init__(self, transcribe, extract, verify, dispatch, review):
        self.f = (transcribe, extract, verify, dispatch, review)

    def invoke(self, state: dict) -> dict:
        transcribe, extract, verify, dispatch, review = self.f
        state.update(transcribe(state))
        if not state["msg"].raw_text.strip():
            state.update(review(state))
            return state
        state.update(extract(state))
        state.update(verify(state))
        if state.get("incident"):
            state.update(dispatch(state))
        return state
