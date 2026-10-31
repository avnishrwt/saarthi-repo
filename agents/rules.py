"""
Rule-based Hinglish extractor.

Two jobs:
1. The "keyword rules" BASELINE the PRD says RAHAT must beat (section 11).
2. The FALLBACK when no LLM key is set or the LLM call fails, so the demo never stops.

Rule: if a field is not clearly in the text it stays null / empty. Never guess.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from agents import geo
from shared.schemas import Extraction, HazardType, MedicalSeverity, Need, Vulnerable

NUM_WORDS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "che": 6, "chhe": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "bees": 20, "pachas": 50, "sau": 100,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twenty": 20, "fifty": 50, "hundred": 100,
}
PEOPLE_WORDS = r"(?:log|logo|logon|people|persons|person|jan|members|member|aadmi|bacche|bachche|kids|children|parivar|family|लोग)"


def _has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<![\w]){w}(?![\w])", text) for w in words)


ELDERLY = ["dadi", "dada", "nani", "nana", "dadiji", "naniji", "buzurg", "bujurg", "budhe", "budhi", "boodhe",
           "old lady", "old man", "old woman", "elderly", "senior citizen", "grandmother", "grandfather",
           "grandma", "grandpa", "amma", "दादी", "दादा", "नानी", "नाना", "बुजुर्ग", "बूढ़े"]
CHILD = ["baccha", "bacche", "bachcha", "bachche", "bacchi", "baby", "kids", "kid", "child", "children",
         "infant", "newborn", "chhota bachcha", "बच्चा", "बच्चे", "बच्ची"]
DISABLED = ["wheelchair", "viklang", "divyang", "disabled", "handicapped", "blind", "andha", "andhi",
            "chal nahi sakte", "chal nahi sakti", "chal nahi pa", "paralysed", "paralyzed", "lakwa", "विकलांग"]
PREGNANT = ["pregnant", "garbhvati", "garbhwati", "delivery", "labour pain", "labor pain", "गर्भवती"]
ALONE = ["akela", "akeli", "akele", "alone", "lives alone", "koi nahi hai", "अकेला", "अकेली"]

RESCUE = ["phasi", "phase", "phasa", "phans", "fasi", "fase", "fanse", "fasa", "trapped", "stuck", "bachao",
          "bacha lo", "rescue", "doob", "doob rahe", "drowning", "beh gaya", "chhat pe", "chhat par", "roof",
          "nahi nikal", "nikal nahi", "boat bhejo", "boat", "फंसी", "फंसे", "फंसा", "बचाओ", "डूब"]
MEDICAL_LIFE = ["behosh", "unconscious", "saans nahi", "saans nahi le", "not breathing", "heart attack",
                "dil ka daura", "bahut khoon", "heavy bleeding", "critical", "mar raha", "dying", "बेहोश"]
MEDICAL_SERIOUS = ["injured", "ghayal", "khoon", "bleeding", "fracture", "toot gaya", "tut gaya", "toota",
                   "chot", "chot lagi", "tez bukhar", "high fever", "labour pain", "delivery", "saans",
                   "breathing problem", "ambulance", "doctor", "medical", "घायल", "खून"]
MEDICAL_MINOR = ["bukhar", "fever", "dard", "pain", "ulti", "vomit", "loose motion", "diarrhea"]
FOOD = ["khana", "khaana", "food", "bhookh", "bhook", "bhukhe", "ration", "rashan", "roti", "खाना", "भूख"]
WATER_NEED = ["peene ka paani", "pine ka pani", "peene ka pani", "drinking water", "pani khatam", "paani khatam",
              "पीने का पानी"]
MEDICINE = ["dawai", "dawa", "davai", "medicine", "insulin", "tablet", "दवाई", "दवा"]
SHELTER = ["shelter", "rehne ki jagah", "chhat nahi", "ghar gir", "camp"]
EVAC = ["nikalna hai", "evacuate", "evacuation", "bahar nikalo", "shift karna"]

WATER = ["paani", "pani", "water", "flood", "baadh", "badh", "doob", "pani bhar", "पानी", "बाढ़"]
FIRE = ["aag", "fire", "jal raha", "jal rahi", "dhuan", "smoke", "आग"]
COLLAPSE = ["gir gaya", "gir gayi", "collapse", "collapsed", "malba", "debris", "deewar giri", "diwar giri",
            "makaan gira", "building giri", "गिर गया", "मलबा"]
LANDSLIDE = ["landslide", "bhooskhalan", "pahad gira", "pahad khisak", "bhuskhalan", "भूस्खलन"]

QUESTION_ONLY = ["kab tak", "kab aayegi", "kab aayega", "kya hal", "update", "news", "kab rukegi", "kab ruke",
                 "when will", "koi batao", "school band", "kal school"]


def detect_language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    t = geo.normalize(text)
    en = ["the", "is", "are", "please", "pls", "plz", "help", "trapped", "water", "people", "need", "my", "stuck"]
    hi = ["hai", "hain", "mein", "me", "ki", "ka", "ke", "bhai", "log", "paani", "pani", "nahi", "chahiye", "ho"]
    toks = t.split()
    e = sum(1 for w in toks if w in en)
    h = sum(1 for w in toks if w in hi)
    if e and not h:
        return "en"
    return "hinglish"


def _people_count(t: str) -> tuple[Optional[int], bool]:
    m = re.search(rf"(\d+)\s*(?:-|se|to|ya)\s*(\d+)\s*{PEOPLE_WORDS}", t)
    if m:
        return max(int(m.group(1)), int(m.group(2))), False
    m = re.search(rf"(\d+)\s*{PEOPLE_WORDS}", t)
    if m:
        return int(m.group(1)), False
    for w, n in NUM_WORDS.items():
        if re.search(rf"\b{w}\s+{PEOPLE_WORDS}", t):
            return n, False
    if _has(t, ["pura parivar", "poora parivar", "puri family", "whole family", "family", "parivar"]):
        return 4, True          # estimated, shown as estimated on the dashboard
    if _has(t, ALONE) or re.search(r"\bmain\b.*\b(hu|hoon|hun)\b", t):
        return 1, False
    if _has(t, ELDERLY + PREGNANT) and not _has(t, CHILD):
        return 1, False         # "meri dadi phasi hai" -> one person
    return None, False


def _age(t: str) -> Optional[int]:
    m = re.search(r"(\d{2,3})\s*(?:saal|sal|years|yrs|year|baras|वर्ष|साल)", t)
    return int(m.group(1)) if m else None


def _water_depth_ft(t: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:feet|ft|foot|fit|fut|फीट)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:meter|metre|m)\b", t)
    if m:
        return round(float(m.group(1)) * 3.28, 1)
    if _has(t, ["gale tak", "neck", "chhati tak", "chest"]):
        return 4.5
    if _has(t, ["kamar tak", "waist"]):
        return 3.0
    if _has(t, ["ghutne tak", "knee"]):
        return 1.5
    return None


def extract(text: str) -> Extraction:
    t = geo.normalize(text)
    raw_lower = text.lower()
    ex = Extraction(extractor="rules", language=detect_language(text))

    place, _ = geo.match_place(text)
    if place:
        ex.place_id = place.place_id
        ex.location_text = place.name
        lm = re.search(r"(\w+\s+(?:ke|ki|ka)\s+(?:peeche|piche|paas|pass|samne|saamne|aage|bagal))", t)
        if lm:
            ex.location_text = f"{place.name} ({lm.group(1)})"

    # vulnerable persons
    age = _age(t)
    if _has(t, ELDERLY) or (age is not None and age >= 60):
        ex.vulnerable.append(Vulnerable.elderly)
    if _has(t, CHILD):
        ex.vulnerable.append(Vulnerable.child)
    if _has(t, DISABLED):
        ex.vulnerable.append(Vulnerable.disabled)
    if _has(t, PREGNANT):
        ex.vulnerable.append(Vulnerable.pregnant)
    if _has(t, ALONE):
        ex.vulnerable.append(Vulnerable.alone)

    ex.people_count, ex.people_estimated = _people_count(t)

    # hazard
    depth = _water_depth_ft(t)
    if _has(t, FIRE):
        ex.hazard = HazardType.fire
    elif _has(t, LANDSLIDE):
        ex.hazard = HazardType.landslide
    elif _has(t, COLLAPSE):
        ex.hazard = HazardType.collapse
    elif _has(t, WATER) and not _has(t, WATER_NEED):
        ex.hazard = HazardType.water
    elif depth is not None:
        ex.hazard = HazardType.water
    if ex.hazard == HazardType.water:
        ex.water_depth_ft = depth
        if _has(t, ["chhat pe", "chhat par", "roof", "chhat tak", "chat pe"]):
            ex.hazard_detail = "people on roof / water near roof"
        elif depth is not None:
            ex.hazard_detail = f"water ~{depth:g} ft"
        else:
            ex.hazard_detail = "water entering house"

    # medical
    if _has(t, MEDICAL_LIFE):
        ex.medical = MedicalSeverity.life_threatening
    elif _has(t, MEDICAL_SERIOUS):
        ex.medical = MedicalSeverity.serious
    elif _has(t, MEDICAL_MINOR):
        ex.medical = MedicalSeverity.minor

    # needs
    needs: list[Need] = []
    in_water_with_person = ex.hazard == HazardType.water and (
        ex.vulnerable or ex.people_count or _has(t, ["ghar", "house", "andar", "inside"]))
    if _has(t, RESCUE) or in_water_with_person or ex.hazard in (HazardType.collapse, HazardType.landslide, HazardType.fire):
        needs.append(Need.rescue)
    if ex.medical in (MedicalSeverity.serious, MedicalSeverity.life_threatening):
        needs.append(Need.medical)
    if _has(t, FOOD):
        needs.append(Need.food)
    if _has(t, WATER_NEED):
        needs.append(Need.water)
    if _has(t, MEDICINE):
        needs.append(Need.medicine)
    if _has(t, SHELTER):
        needs.append(Need.shelter)
    if _has(t, EVAC):
        needs.append(Need.evacuation)
    if not needs and ex.hazard and _has(t, ["help", "madad", "sahayata", "emergency", "urgent", "मदद"]):
        needs.append(Need.rescue)
    if not needs and (_has(t, QUESTION_ONLY) or raw_lower.strip().endswith("?")):
        needs.append(Need.information)
        ex.is_help_request = False
    if not needs and not ex.hazard and not ex.vulnerable:
        ex.is_help_request = False
    ex.needs = needs
    return ex


def people_factor(n: Optional[int], has_need: bool) -> float:
    """Shared helper used by priority: 1 person -> 2, 6 -> 7.2, 20+ -> 10."""
    if n is None:
        return 2.0 if has_need else 0.0
    return round(min(10.0, 2.0 + 2.0 * math.log2(max(n, 1))), 1)
