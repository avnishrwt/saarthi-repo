"""Gazetteer (data/locations.csv), fuzzy place matching, distances, affected area."""
from __future__ import annotations

import csv
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

from shared import config


@dataclass
class Place:
    place_id: str
    name: str
    aliases: list[str]
    lat: float
    lng: float
    in_area: bool
    flooded: bool


def nfc(text: str) -> str:
    """One spelling per Hindi letter: phones send ढ़ / ड़ / क़ either as ONE character (U+095D...) or as
    letter + nukta dot. NFC turns both into letter + dot, so \"बाढ़\" matches however it was typed."""
    return unicodedata.normalize("NFC", text or "")


_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize(text: str) -> str:
    t = nfc(text).lower().translate(_DEV_DIGITS)
    t = re.sub(r"\bno\.?\s*(\d)", r"no \1", t)
    t = re.sub(r"[^\w\sऀ-ॿ]", " ", t)    # keep Devanagari
    t = re.sub(r"(\D)(\d)", r"\1 \2", t)            # "gali3" -> "gali 3"
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=1)
def places() -> list[Place]:
    out = []
    with open(config.DATA_DIR / "locations.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            aliases = [normalize(a) for a in r["aliases"].split(";") if a.strip()]
            aliases.append(normalize(r["name"]))
            out.append(Place(r["place_id"], r["name"], sorted(set(aliases), key=len, reverse=True),
                             float(r["lat"]), float(r["lng"]), r["in_area"] == "1", r["flooded"] == "1"))
    return out


def place_by_id(pid: Optional[str]) -> Optional[Place]:
    return next((p for p in places() if p.place_id == pid), None) if pid else None


def _tok_match(a: str, b: str) -> bool:
    if a.isdigit() or b.isdigit():
        return a == b                       # "gali 3" must never match "gali 4"
    if a == b:
        return True
    return len(a) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8   # typos: "rampur"/"rampure"


def match_place(text: str) -> tuple[Optional[Place], float]:
    """Return (best place, score 0..1). Every alias token must appear (fuzzy) in the text."""
    tokens = normalize(text).split()
    if not tokens:
        return None, 0.0
    found = []          # (first token position, -score, place, score)
    for p in places():
        for alias in p.aliases:
            at = alias.split()
            pos = [next((i for i, t in enumerate(tokens) if _tok_match(a, t)), None) for a in at]
            if None in pos:
                continue
            score = 0.5 + 0.5 * min(len(alias) / 20, 1.0)   # longer alias = more specific
            found.append((min(pos), -score, p.place_id, p, score))
    if not found:
        return None, 0.0
    # "clement town subhash nagar side": two places named -> the first one mentioned wins;
    # same position -> the longer (more specific) alias wins
    _, _, _, best, best_score = min(found)
    return best, best_score


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def in_affected_area(lat: float, lng: float) -> bool:
    return haversine_km(lat, lng, *config.AREA_CENTER) <= config.AREA_RADIUS_KM


def nearest_place(lat: float, lng: float) -> Optional[Place]:
    return min(places(), key=lambda p: haversine_km(lat, lng, p.lat, p.lng), default=None)


def travel_minutes(team_lat: float, team_lng: float, lat: float, lng: float, speed_kmh: float) -> float:
    """Prototype travel time: straight line x road factor / speed (PRD section 11)."""
    km = haversine_km(team_lat, team_lng, lat, lng) * config.ROAD_FACTOR
    return km / speed_kmh * 60.0
