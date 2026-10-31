"""
SOP-grounded field checklist (PRD FR16, brief section 6).

Documents: OFFICIAL guideline text in data/sop/ (.pdf, .md, .txt), described in data/sop/sources.json
(title, publisher, year, url, hazards, which chapter is the response chapter). Get the PDFs with
`python -m rag.fetch_sop`. Nothing in data/sop/ -> every checklist is NOT_IN_SOP.
We never ship guidance a model wrote from memory: every item is tied to a passage, with its page.

During the disaster:
  query from incident fields (+ a few response words for that need)
  -> only RESPONSE-chapter passages, only documents that cover this hazard (no fallback:
     flood guidelines do not answer a fire question -> NOT_IN_SOP, escalate to officer)
  -> top-k by similarity; best score below threshold -> NOT_IN_SOP
  -> LLM writes items, each tagged with a passage id; any item whose id is not in the retrieved
     set is DROPPED. Without an LLM: extractive (copy the action sentences: will / shall / must ...).
"""
from __future__ import annotations

import json
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
PAGE = re.compile(r"^<<PAGE (\d+)>>$")

HAZARD_WORDS = {"water": ["flood", "water", "boat", "drown"], "fire": ["fire", "burn"],
                "collapse": ["collapse", "debris", "building"], "landslide": ["landslide"]}
# a few response words per need, so retrieval looks for what a field team does (not glossary / dams)
NEED_WORDS = {
    "rescue": "search and rescue evacuation boats life jackets rescue teams",
    "evacuation": "evacuation shelters relief camps",
    "medical": "medical first responders casualties first aid ambulance hospital",
    "medicine": "medicines medical stores",
    "food": "relief camps food packets supply",
    "water": "drinking water supply relief camps",
    "shelter": "relief camps shelters evacuated people",
    "welfare_check": "vulnerable people elderly evacuation",
}
ACTION = re.compile(r"\b(will|shall|should|must|ensure|be provided|be made|be taken|be arranged|be set up)\b", re.I)


@dataclass
class Chunk:
    id: str
    doc: str
    section: str
    text: str
    hazards: set[str]
    page: Optional[int] = None
    response: bool = True        # part of the response chapter (used for field checklists)


@lru_cache(maxsize=1)
def sources() -> dict:
    f = SOP_DIR / "sources.json"
    if not f.exists():
        return {}
    try:
        return {k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    except Exception as e:
        log.warning("could not read sources.json: %s", e)
        return {}


def _read(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        # reading a 200-page PDF takes ~15 s: keep the extracted text next to it (re-done if the PDF changes)
        st = path.stat()
        cache = SOP_DIR / ".cache" / f"{path.stem}.{st.st_size}.{int(st.st_mtime)}.txt"
        if cache.exists():
            return cache.read_text(encoding="utf-8")
        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)       # font-encoding notices are noise
        # page markers let every passage carry its PDF page number
        text = "\n".join(f"<<PAGE {n}>>\n{p.extract_text() or ''}" for n, p in enumerate(PdfReader(str(path)).pages, 1))
        try:
            cache.parent.mkdir(exist_ok=True)
            for old in cache.parent.glob(f"{path.stem}.*.txt"):
                old.unlink()
            cache.write_text(text, encoding="utf-8")
        except OSError as e:
            log.warning("could not cache SOP text: %s", e)
        return text
    return path.read_text(encoding="utf-8", errors="ignore")


def warm_up() -> None:
    """Load the SOP once in the background at startup, so the first checklist is instant."""
    import threading
    threading.Thread(target=load_chunks, name="sop-warmup", daemon=True).start()


HEADING = re.compile(r"^(#{1,4}\s+.+|\d+(\.\d+)*\.?\s+[A-Z][^\n]{2,80}|[A-Z][A-Z \-/&]{4,80})$")


def _is_response(section: str, prefixes: list[str]) -> bool:
    return not prefixes or any(re.match(rf"^{re.escape(p)}\.\d", section) for p in prefixes)


def chunk_text(doc: str, text: str, meta: Optional[dict] = None) -> list[Chunk]:
    meta = meta or {}
    lines = text.splitlines()
    doc_hazards: set[str] = set(meta.get("hazards", []))
    if lines and lines[0].lower().startswith("hazards:"):
        doc_hazards = {h.strip() for h in lines[0].split(":", 1)[1].split(",") if h.strip()}
        lines = lines[1:]
    prefixes = [str(p) for p in meta.get("response_sections", [])]
    chunks, section, buf = [], "Introduction", []
    page = start_page = None

    def flush():
        body = re.sub(r"[ \t]+", " ", "\n".join(buf)).strip()
        if len(body) > 40:
            low = (section + " " + body).lower()
            hz = set(doc_hazards) or {h for h, ws in HAZARD_WORDS.items() if any(w in low for w in ws)}
            chunks.append(Chunk(f"{doc}#{len(chunks) + 1}", doc, section, body, hz, start_page,
                                _is_response(section, prefixes)))

    for line in lines:
        m = PAGE.match(line.strip())
        if m:
            page = int(m.group(1))
            if not buf:
                start_page = page
            continue
        if HEADING.match(line.strip()) and len(line.strip()) < 90:
            flush()
            section, buf, start_page = re.sub(r"\s+", " ", line.strip("# ").strip()), [], page
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
                    out += chunk_text(p.stem, _read(p), sources().get(p.stem))
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
    parts += [NEED_WORDS[n.value] for n in inc.needs if n.value in NEED_WORDS]
    return "rescue " + " ".join(parts) if "rescue" not in parts else " ".join(parts)


def retrieve(query: str, hazard: Optional[str]) -> list[tuple[Chunk, float]]:
    chunks = list(load_chunks())
    resp = [c for c in chunks if c.response]
    chunks = resp or chunks
    if hazard:
        # documents with declared hazards only answer for those hazards: no fallback to unrelated text
        chunks = [c for c in chunks if not c.hazards or hazard in c.hazards]
    if not chunks:
        return []
    sims = embeddings.similarities(query, [f"{c.section}. {c.text}" for c in chunks])
    ranked = sorted(zip(chunks, sims), key=lambda x: -x[1])[:TOP_K]
    return [(c, float(s)) for c, s in ranked]


def cite(c: Chunk) -> str:
    meta = sources().get(c.doc, {})
    name = f"NDMA {meta['title'].split(':')[-1].strip()} ({meta['year']})" if meta.get("title") else c.doc
    num = re.match(r"^\d+(\.\d+)+", c.section)                 # "7.9.1 One of the prime..." -> "7.9.1"
    sec = num.group(0) if num else c.section[:40]
    return f"{name}, §{sec}" + (f", PDF p.{c.page}" if c.page else "")


def _clean(s: str) -> str:
    """PDF text artefacts: private-use bullet glyphs, 'R e s t o r a t i o n', words split by line breaks."""
    s = re.sub(r"[-�•▪●]", " ", s)
    s = re.sub(r"\b((?:[A-Za-z] ){3,}[A-Za-z])\b", lambda m: m.group(1).replace(" ", ""), s)
    s = re.sub(r"(\w)- (\w)", r"\1\2", s)
    s = re.sub(r"\s+", " ", s).strip(" -*\n")
    return re.sub(r"^(?:[a-z]|[ivx]{1,4})\)\s*", "", s)           # list markers a) b) iv)


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
    sents, seen = [], set()
    for c, _ in hits:
        for s in re.split(r"(?<=[.;])\s+|\n+[-*•]\s*|\n+\d+[.)]\s+|\n(?=[a-z]\)\s)", c.text):
            s = _clean(s)
            key = s.lower()[:80]
            whole = s[:1].isupper() and s.endswith((".", ";"))              # a full sentence, not a fragment
            if 25 <= len(s) <= 300 and whole and ACTION.search(s) and key not in seen:   # steps, not descriptions
                seen.add(key)
                sents.append((c.id, s))
    if not sents:
        return []
    sims = embeddings.similarities(query, [s for _, s in sents])
    best = sorted(zip(sents, sims), key=lambda x: -x[1])[:n]
    return [{"text": s, "passage_id": cid} for (cid, s), _ in best]


def checklist(inc: Incident) -> dict:
    query = build_query(inc)
    hazard = inc.hazard.value if inc.hazard else None
    hits = retrieve(query, hazard)
    base = {"query": query, "sop_loaded": len(load_chunks()),
            "documents": [{"title": m.get("title"), "year": m.get("year"), "url": m.get("url")} for m in sources().values()]}
    if not hits or hits[0][1] < THRESHOLD:
        why = (f"The loaded SOPs do not cover '{hazard}'." if hazard and not hits and load_chunks() else None)
        return {**base, "status": NOT_IN_SOP, "message": "Not in SOP, escalate to officer", "why": why,
                "best_score": round(hits[0][1], 3) if hits else None, "items": [], "sources": []}
    valid = {c.id: c for c, _ in hits}
    raw = _llm_items(query, hits)
    mode = "llm" if raw is not None else "extractive"
    if raw is None:
        raw = _extractive(query, hits)
    items = [i for i in raw if i.get("passage_id") in valid]         # drop anything without a real source
    dropped = len(raw) - len(items)
    if not items:
        return {**base, "status": NOT_IN_SOP, "message": "Not in SOP, escalate to officer", "items": [], "sources": []}
    return {
        **base, "status": "OK", "mode": mode, "dropped_unsourced": dropped,
        "items": [{**i, "source": cite(valid[i["passage_id"]])} for i in items],
        "sources": [{"id": c.id, "doc": c.doc, "section": c.section, "page": c.page, "score": round(s, 3)} for c, s in hits],
    }
