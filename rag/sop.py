"""
SOP-grounded field checklist (PRD FR16, brief section 6).

Before the event: put OFFICIAL guideline text in data/sop/ as .md or .txt (or .pdf
with pypdf installed). Optional first line:  hazards: flood, water
Chunks = sections split on headings. Nothing in data/sop/ -> every checklist is
NOT_IN_SOP. We never ship guidance the model wrote from memory.

During the disaster:
  query from incident fields -> filter chunks by hazard -> top-k by similarity
  -> best score below threshold? NOT_IN_SOP ("Not in SOP, escalate to officer")
  -> LLM writes items, each tagged with a passage id; any item whose id is not in
     the retrieved set is DROPPED. Without an LLM: extractive (copy the sentences).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from agents import embeddings, llm
from shared import config
from shared.schemas import Incident

log = logging.getLogger("rahat.rag")
SOP_DIR = config.DATA_DIR / "sop"
THRESHOLD = 0.18          # tune on the ~20 RAG test incidents (brief section 6, Evaluation)
TOP_K = 4
NOT_IN_SOP = "NOT_IN_SOP"

HAZARD_WORDS = {"water": ["flood", "water", "boat", "drown"], "fire": ["fire", "burn"],
                "collapse": ["collapse", "debris", "building"], "landslide": ["landslide"]}


@dataclass
class Chunk:
    id: str
    doc: str
    section: str
    text: str
    hazards: set[str]


def _read(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="ignore")


HEADING = re.compile(r"^(#{1,4}\s+.+|\d+(\.\d+)*\.?\s+[A-Z][^\n]{2,80}|[A-Z][A-Z \-/&]{4,80})$")


def chunk_text(doc: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    doc_hazards: set[str] = set()
    if lines and lines[0].lower().startswith("hazards:"):
        doc_hazards = {h.strip() for h in lines[0].split(":", 1)[1].split(",") if h.strip()}
        lines = lines[1:]
    chunks, section, buf = [], "Introduction", []

    def flush():
        body = "\n".join(buf).strip()
        if len(body) > 40:
            low = (section + " " + body).lower()
            hz = set(doc_hazards) or {h for h, ws in HAZARD_WORDS.items() if any(w in low for w in ws)}
            chunks.append(Chunk(f"{doc}#{len(chunks) + 1}", doc, section, body, hz))

    for line in lines:
        if HEADING.match(line.strip()) and len(line.strip()) < 90:
            flush()
            section, buf = line.strip("# ").strip(), []
        else:
            buf.append(line)
    flush()
    return chunks


@lru_cache(maxsize=1)
def load_chunks() -> tuple[Chunk, ...]:
    out: list[Chunk] = []
    if SOP_DIR.exists():
        for p in sorted(SOP_DIR.iterdir()):
            if p.suffix.lower() in (".md", ".txt", ".pdf") and p.name.lower() != "readme.md":
                try:
                    out += chunk_text(p.stem, _read(p))
                except Exception as e:
                    log.warning("could not read %s: %s", p.name, e)
    return tuple(out)


def build_query(inc: Incident) -> str:
    parts = [n.value for n in inc.needs] + [v.value for v in inc.vulnerable]
    if inc.hazard:
        parts.append(inc.hazard.value)
        if inc.hazard.value == "water":
            parts.append("flood")
    if inc.hazard_detail:
        parts.append(inc.hazard_detail)
    parts += [t.value for t in inc.required_team_types]
    if "roof" in (inc.hazard_detail or ""):
        parts.append("rooftop")
    return "rescue " + " ".join(parts) if "rescue" not in parts else " ".join(parts)


def retrieve(query: str, hazard: Optional[str]) -> list[tuple[Chunk, float]]:
    chunks = list(load_chunks())
    if hazard:
        filtered = [c for c in chunks if not c.hazards or hazard in c.hazards]
        chunks = filtered or chunks
    if not chunks:
        return []
    sims = embeddings.similarities(query, [f"{c.section}. {c.text}" for c in chunks])
    ranked = sorted(zip(chunks, sims), key=lambda x: -x[1])[:TOP_K]
    return [(c, float(s)) for c, s in ranked]


class _Item(BaseModel):
    text: str
    passage_id: str


class _Checklist(BaseModel):
    items: list[_Item]


def _llm_items(query: str, hits: list[tuple[Chunk, float]]) -> Optional[list[dict]]:
    passages = "\n\n".join(f"[{c.id}] ({c.doc} / {c.section})\n{c.text[:1500]}" for c, _ in hits)
    out = llm.structured(
        ("Write a short field checklist for a rescue team using ONLY the passages given. "
         "Each item must be a step stated in one passage and must carry that passage's id in passage_id. "
         "If the passages do not cover the situation, return no items. Do not add advice of your own."),
        f"Situation: {query}\n\nPassages:\n{passages}", _Checklist)
    return [i.model_dump() for i in out.items] if out is not None else None


def _extractive(query: str, hits: list[tuple[Chunk, float]], n: int = 6) -> list[dict]:
    sents = []
    for c, _ in hits:
        for s in re.split(r"(?<=[.;:])\s+|\n+[-*•]\s*|\n+\d+[.)]\s+", c.text):
            s = s.strip(" -*•\n")
            if 25 <= len(s) <= 300:
                sents.append((c.id, s))
    if not sents:
        return []
    sims = embeddings.similarities(query, [s for _, s in sents])
    best = sorted(zip(sents, sims), key=lambda x: -x[1])[:n]
    return [{"text": s, "passage_id": cid} for (cid, s), _ in best]


def checklist(inc: Incident) -> dict:
    query = build_query(inc)
    hits = retrieve(query, inc.hazard.value if inc.hazard else None)
    if not hits or hits[0][1] < THRESHOLD:
        return {"status": NOT_IN_SOP, "message": "Not in SOP, escalate to officer", "query": query,
                "best_score": round(hits[0][1], 3) if hits else None, "items": [], "sources": [],
                "sop_loaded": len(load_chunks())}
    valid = {c.id: c for c, _ in hits}
    raw = _llm_items(query, hits)
    mode = "llm" if raw is not None else "extractive"
    if raw is None:
        raw = _extractive(query, hits)
    items = [i for i in raw if i.get("passage_id") in valid]         # drop anything without a real source
    dropped = len(raw) - len(items)
    if not items:
        return {"status": NOT_IN_SOP, "message": "Not in SOP, escalate to officer", "query": query,
                "items": [], "sources": [], "sop_loaded": len(load_chunks())}
    return {
        "status": "OK", "mode": mode, "query": query, "dropped_unsourced": dropped,
        "items": [{**i, "source": f"{valid[i['passage_id']].doc} / {valid[i['passage_id']].section}"} for i in items],
        "sources": [{"id": c.id, "doc": c.doc, "section": c.section, "score": round(s, 3)} for c, s in hits],
        "sop_loaded": len(load_chunks()),
    }
