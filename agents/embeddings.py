"""
Text similarity for duplicate merging and RAG retrieval.

Default "hash": stateless character n-gram hashing (no model download, works on
venue Wi-Fi, robust to Hinglish typos). On its own it cannot know that "dadi phasi
hai" means "old lady trapped", so `concept_text()` appends the extracted fields
(needs, vulnerable, hazard, place) as shared tokens before embedding.

"st": a real multilingual sentence-embedding model (pip install sentence-transformers).
Brief open decision #2: test on OUR data that "phasi hai" and "trapped" come out close
before switching the demo to it.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from shared import config
from shared.schemas import Extraction

log = logging.getLogger("rahat.embeddings")


def concept_text(raw_text: str, ex: Extraction | None) -> str:
    if ex is None:
        return raw_text
    tags = [f"need_{n.value}" for n in ex.needs] + [f"vuln_{v.value}" for v in ex.vulnerable]
    if ex.hazard:
        tags.append(f"hazard_{ex.hazard.value}")
    if ex.place_id:
        tags.append(f"place_{ex.place_id}")
    return f"{raw_text} || " + " ".join(tags * 2)   # tags repeated: the meaning should outweigh wording


@lru_cache(maxsize=1)
def _hasher():
    from sklearn.feature_extraction.text import HashingVectorizer
    return HashingVectorizer(analyzer="char_wb", ngram_range=(2, 4), n_features=2 ** 14,
                             alternate_sign=False, norm="l2", lowercase=True)


@lru_cache(maxsize=1)
def _st_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(config.ST_MODEL)


@lru_cache(maxsize=20000)
def _vec(text: str) -> np.ndarray:
    """One text -> unit vector, cached: the verifier compares each new message with many
    old ones, so without the cache the same texts were re-embedded on every message."""
    if config.EMBEDDINGS == "st":
        try:
            return np.asarray(_st_model().encode([text], normalize_embeddings=True)[0], dtype=np.float32)
        except Exception as e:
            log.warning("sentence-transformers unavailable (%s); using char n-gram hashing", e)
    return _hasher().transform([text]).toarray()[0].astype(np.float32)


def embed(texts: list[str]) -> np.ndarray:
    return np.stack([_vec(t) for t in texts])


def similarity(a: str, b: str) -> float:
    v = embed([a, b])
    return float(np.dot(v[0], v[1]))


def similarities(query: str, others: list[str]) -> np.ndarray:
    """Cosine similarity of one text against many (vectors are L2-normalised)."""
    if not others:
        return np.zeros(0)
    v = embed([query] + others)
    return v[1:] @ v[0]
