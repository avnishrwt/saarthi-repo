"""
All tunable numbers in one place, so the dashboard can show them and judges can
see exactly what the system does. Every value here is a prototype choice, not a
validated setting: tune on the ~50-message TUNING split only, never on the
locked evaluation split.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = os.getenv("RAHAT_DB", str(ROOT / "rahat.db"))

# ----------------------------------------------------------------- LLM (Extractor + RAG)
# Extractor falls back to rule-based extraction when no key is set or a call fails.
# Gemini is the team's choice; Anthropic kept as a second option for the LLM comparison test.
_default_provider = ("gemini" if (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
                     else "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "rules")
LLM_PROVIDER = os.getenv("RAHAT_LLM", _default_provider)
# Tested 2026-10-31 on 6 demo messages: gemini-3.1-flash-lite was correct on all 6 in 1-4 s.
# gemini-2.5-flash is closed to new keys; 3.7/3.8-flash returned 503/504; 3.5-flash-lite invented
# "alone" for dadi. Re-test on the 20-message set (brief decision #1) and override with RAHAT_LLM_MODEL.
LLM_MODEL = os.getenv("RAHAT_LLM_MODEL", {"gemini": "gemini-3.1-flash-lite",
                                          "anthropic": "claude-opus-5-5"}.get(LLM_PROVIDER, "none"))
LLM_EFFORT = os.getenv("RAHAT_LLM_EFFORT", "low")   # anthropic only: extraction is simple, keep latency down
LLM_TIMEOUT_S = float(os.getenv("RAHAT_LLM_TIMEOUT_S", "20"))

# "hash" (stateless char n-grams, no download) or "st" (sentence-transformers model)
EMBEDDINGS = os.getenv("RAHAT_EMBEDDINGS", "hash")
ST_MODEL = os.getenv("RAHAT_ST_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

SENDER_SALT = os.getenv("RAHAT_SALT", "change-me-at-the-event")

# ----------------------------------------------------------------- affected area
AREA_NAME = os.getenv("RAHAT_DISTRICT", "Dehradun (demo scenario)")
AREA_CENTER = (30.3000, 78.0350)   # affected-area circle over Dehradun; flooded places are a demo assumption
AREA_RADIUS_KM = 5.5

# ----------------------------------------------------------------- priority (PRD 6.1)
# P = w1*medical + w2*people + w3*vulnerable + w4*hazard + w5*waiting   (factors 0..10)
PRIORITY_WEIGHTS = {
    "medical": 1.2,
    "people": 1.0,
    "vulnerable": 1.0,
    "hazard": 1.0,
    "waiting": 0.8,
}
WAITING_MIN_PER_POINT = 6.0        # +1 point every 6 min waiting, capped at 10
BANDS = [("CRIT", 16.0), ("HIGH", 11.0), ("MED", 6.0), ("LOW", 0.0)]

# ----------------------------------------------------------------- confidence (PRD 6.2)
CONF_NEUTRAL = 0.60                # a lone message starts here: NOT low
CONF_HIGH = 0.50                   # >= this is the "high confidence" column of the matrix
CONF_SUSPECT = 0.30                # < this: verify first, no team recommended yet
CONF_PER_CONFIRMATION = 0.10       # each independent sender (non copy-paste), max +0.30
CONF_DETAIL_BONUS = 0.05           # known landmark in area / stated number
RED_FLAG_PENALTY = {
    "out_of_area": 0.35,
    "copy_paste_burst": 0.30,
    "low_reputation_sender": 0.20,
    "serial_reporter": 0.20,
    "location_not_found": 0.0,     # vague is not fake: shown, not penalised
}
COPY_PASTE_SIM = 0.92              # text similarity counted as "same text"
COPY_PASTE_MIN_SENDERS = 3
COPY_PASTE_WINDOW_S = 180
SERIAL_REPORTER_INCIDENTS = 3      # one sender, >= 3 different-place incidents in 30 min

# ----------------------------------------------------------------- merging (PRD FR04)
# distance = 0.6*(1-text_sim) + 0.25*geo + 0.15*time + need_mismatch ; join if < MERGE_EPS
MERGE_EPS = 0.45
MERGE_MAX_KM = 0.40
MERGE_WINDOW_MIN = 60
NEED_MISMATCH_PENALTY = 0.30

# ----------------------------------------------------------------- optimizer (PRD 6.5)
# "weighted" (default): cost = travel_min * (1 + priority/10) - LAMBDA * priority + penalty
# "prd":                cost = travel_min                     - LAMBDA * priority + penalty
# Why weighted: when every incident gets a team, the -LAMBDA*priority terms are the
# same for every full assignment and cancel out, so the PRD formula only minimises
# TOTAL travel and can still make a critical case wait. Weighting travel by priority
# makes a minute of a critical case's wait cost more than a minute of a food request's.
COST_MODE = os.getenv("RAHAT_COST_MODE", "weighted")
LAMBDA = 2.0
ROAD_FACTOR = 1.3                  # straight line x 1.3 ~ road distance (prototype)
SPEED_KMH = {"boat": 15, "medical": 30, "relief": 25, "search_rescue": 25, "fire": 30}
SUBSTITUTES = {                    # need type -> [(team type that can stand in, penalty)]
    "relief": [("boat", 8.0)],     # flooded lanes: a boat carries food/water
    "search_rescue": [("boat", 5.0)],
    "medical": [],
    "boat": [],
    "fire": [],
}
OVER_CAPACITY_PENALTY = 10.0
SURGE_SPIKE_WINDOW_MIN = 5
SURGE_SPIKE_COUNT = 8              # help messages from one area in the window

FOLLOWUP_WINDOW_MIN = 30           # a reply from the same sender joins the same incident
