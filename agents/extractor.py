"""
Extractor agent: one raw message -> Extraction (PRD FR02).

- LLM path: Gemini (or Claude) with a schema-validated JSON output, via agents/llm.py.
- Fallback: rule-based extractor (agents/rules.py) when no key, a refusal, or any error.
- After either path, the location is canonicalised through the gazetteer, so a
  place the LLM "invents" that is not on our map stays unmatched (null place_id).
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from agents import geo, llm, rules
from shared.schemas import Extraction, HazardType, MedicalSeverity, Need, Vulnerable

log = logging.getLogger("rahat.extractor")


class _LLMExtraction(BaseModel):
    """What we ask the LLM for. Every field is nullable: null means 'not in the message'."""
    location_text: Optional[str] = Field(None, description="Place exactly as the sender described it, or null")
    known_place: Optional[str] = Field(None, description="One of the known place names if the message clearly refers to it, else null")
    people_count: Optional[int] = Field(None, description="Number of people needing help if stated; null if not stated")
    people_estimated: bool = Field(False, description="true if the count was inferred from words like 'family', not stated")
    vulnerable: list[Vulnerable] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)
    hazard: Optional[HazardType] = None
    water_depth_ft: Optional[float] = None
    medical: MedicalSeverity = MedicalSeverity.none
    language: Optional[str] = Field(None, description="hi, en or hinglish")
    is_help_request: bool = True


SYSTEM = """You extract structured fields from disaster help messages sent in Hindi, English or Hinglish \
(Roman-script Hindi), often with typos and panic. Rules:
- Only fill a field if the message states it. If unsure, use null or an empty list. Never guess.
- people_count: the number of people who need help. "meri dadi phasi hai" = 1. "4-5 log" = 5 (take the upper bound). \
"pura parivar" -> leave null unless a number is given, OR give 4 with people_estimated=true.
- vulnerable: elderly (dadi, nani, buzurg, age 60+), child, disabled, pregnant, alone (lives/is alone).
- needs: rescue (trapped, stuck, phasi, water inside home with a person), medical, food, water (drinking water), \
medicine, shelter, evacuation, information (a question, not a request for help).
- hazard: water, fire, collapse, landslide. water_depth_ft only if a depth is said.
- medical: life_threatening (unconscious, not breathing, heavy bleeding), serious (injury, fracture, bleeding), minor, none.
- known_place must be copied exactly from the list of known places, or null.
- A message like "kab tak barish rukegi?" is is_help_request=false with needs=[information]."""


def _llm_extract(text: str) -> Optional[Extraction]:
    place_names = ", ".join(p.name for p in geo.places())
    o = llm.structured(SYSTEM, f"Known places: {place_names}\n\nMessage:\n{text}", _LLMExtraction)
    if o is None:                    # network, quota, refusal, bad JSON: fall back, never block the pipeline
        return None
    ex = Extraction(
        location_text=o.location_text, people_count=o.people_count, people_estimated=o.people_estimated,
        vulnerable=list(dict.fromkeys(o.vulnerable)), needs=list(dict.fromkeys(o.needs)), hazard=o.hazard,
        water_depth_ft=o.water_depth_ft, medical=o.medical, language=o.language,
        is_help_request=o.is_help_request, extractor=f"llm:{llm.name()}",
    )
    # canonicalise location through OUR gazetteer only
    for cand in (o.known_place, o.location_text, text):
        if cand:
            p, _ = geo.match_place(cand)
            if p:
                ex.place_id = p.place_id
                ex.location_text = ex.location_text or p.name
                break
    if ex.hazard == HazardType.water:
        ex.hazard_detail = f"water ~{ex.water_depth_ft:g} ft" if ex.water_depth_ft else "water"
    return ex


def extract(text: str, use_llm: Optional[bool] = None) -> Extraction:
    if not text.strip():
        return Extraction(is_help_request=False, extractor="empty")
    if use_llm is None:
        use_llm = llm.enabled()
    if use_llm:
        ex = _llm_extract(text)
        if ex is not None:
            return ex
    return rules.extract(text)
