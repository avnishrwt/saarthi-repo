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
    # Hindi script (typed, or a voice note written down in Devanagari)
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "छे": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "बीस": 20, "पचास": 50, "सौ": 100,
}
PEOPLE_WORDS = (r"(?:log|logo|logon|people|ppl|persons|person|men|women|jan|members|member|aadmi|admi|ladka|ladki|aurat|mahila|"
                r"bacche|bachche|bacchon|bachon|kids|children|parivar|family|students|student|yatri|yaatri|"
                r"passengers|marij|mariz|marijon|marizon|mareez|patients|buzurg|buzurgon|bujurg|bujurgon|"
                r"लोगों|लोगो|लोग|पीपल्स|पीपल|बच्चों|बच्चे|आदमी|औरतें|औरत|महिलाएं|महिला|परिवार|सदस्य|मरीज|यात्री|छात्र)")
# "10 chhote bacche", "2 bade log": one size word allowed between the number and the people word
SIZE = r"(?:(?:chhote|chote|chhoti|choti|bade|badi|buzurg|bujurg)\s+)?"
# "mere papa", "mera bhai": one named person (needs the possessive: a bare "bhai" is usually "bro")
RELATIVE = re.compile(r"\b(?:mera|meri|mere|my)\s+(?:papa|pitaji|father|dad|mummy|maa|mother|mom|bhai|brother|"
                      r"behen|bahan|sister|didi|beta|beti|son|daughter|chacha|chachi|mama|mami|pati|husband|patni)\b")


def _squash(text: str) -> str:
    """Collapse repeated letters so panic spellings match: "bachaoooo" -> "bachao", "paani" -> "pani"."""
    return re.sub(r"(\w)\1+", r"\1", text)


# Devanagari vowel signs (ा ि ़ ...) are not \w in Python, so without this "आग" (fire) matched inside
# "आगाग" (Whisper's spelling of "aa gaya"). Whole Hindi words only.
_W = r"[\wऀ-ॿ]"


def _has(text: str, words: list[str]) -> bool:
    text = geo.nfc(text)
    words = [geo.nfc(w) for w in words]
    sq = _squash(text)
    return any(re.search(rf"(?<!{_W}){w}(?!{_W})", text) or re.search(rf"(?<!{_W}){_squash(w)}(?!{_W})", sq)
               for w in words)


ELDERLY = ["dadi", "dada", "nani", "nana", "dadiji", "naniji", "buzurg", "bujurg", "budhe", "budhi", "boodhe",
           "old lady", "old man", "old woman", "elderly", "senior citizen", "grandmother", "grandfather",
           "grandma", "grandpa", "amma", "दादी", "दादा", "नानी", "नाना", "बुजुर्ग", "बूढ़े"]
CHILD = ["baccha", "bacche", "bachcha", "bachche", "bacchi", "baby", "kids", "kid", "child", "children",
         "infant", "newborn", "chhota bachcha", "बच्चा", "बच्चे", "बच्ची"]
DISABLED = ["wheelchair", "viklang", "divyang", "disabled", "handicapped", "blind", "andha", "andhi",
            "chal nahi sakte", "chal nahi sakti", "chal nahi pa", "paralysed", "paralyzed", "lakwa", "विकलांग"]
PREGNANT = ["pregnant", "garbhvati", "garbhwati", "delivery", "labour pain", "labor pain", "गर्भवती"]
ALONE = ["akela", "akeli", "akele", "alone", "lives alone", "koi nahi hai", "अकेला", "अकेली"]

RESCUE = ["phasi", "phase", "phasa", "phans", "phansa", "phansi", "phanse", "fasi", "fase", "fanse", "fasa",
          "fansa", "fansi", "trapped", "stuck", "bachao", "bacha lo", "rescue", "doob", "doob rahe", "drowning",
          "beh gaya", "beh gaye", "beh gayi", "bah gaya", "bah gaye", "chhat pe", "chhat par", "roof", "छत पर", "छत पे",
          "nahi nikal", "nikal nahi", "nikalo", "nikaalo", "nikal lo", "boat bhejo", "boat",
          "फंसी", "फंसे", "फंसा", "बचाओ", "डूब", "निकालो", "निकालना", "निकालना है",
          "ped pe", "ped par", "pedh pe", "on tree", "on a tree", "up a tree", "पेड़ पर", "पेड़ पे",
          "beh rahi", "bah rahi", "beh raha", "bah raha", "swept away", "car beh", "gaadi beh",
          "khai me gir", "khai mein gir", "rasta band", "nikalna hai", "nikalne ka rasta",
          # pinned under something (not bare "daba": "dabba" = box)
          "dab gaya", "dab gayi", "dab gaye", "dabe hue", "daba hua", "dabi hui", "dab rakha", "dab rkha",
          "dab raha", "dab rha", "dab rahi", "neeche dab", "niche dab", "pinned", "crushed",
          "दब गया", "दब गई", "दबे", "नीचे दब"]
PLEA = ["help", "madad", "sahayata", "emergency", "urgent", "sos", "save me", "jaldi aao", "jaldi aa jao",
        "मदद", "सहायता", "हेल्प", "हैल्प", "नीड", "इमरजेंसी", "जल्दी आओ", "जल्दी आ जाओ", "बचा लो"]
MEDICAL_LIFE = ["behosh", "unconscious", "saans nahi", "saans nahi le", "not breathing", "heart attack",
                "dil ka daura", "bahut khoon", "heavy bleeding", "critical", "mar raha", "dying",
                "oxygen band", "oxygen khatam", "oxygen machine band", "oxygen nahi", "बेहोश",
                "saanp ne kaata", "saap ne kaata", "saanp ne kata", "saanp kaat", "snake bite", "snakebite",
                "bitten by a snake", "सांप ने काटा", "साँप ने काटा"]
# "toot gaya" alone is not medical ("pul toot gaya" = the bridge broke): only with a body part
MEDICAL_SERIOUS = ["injured", "ghayal", "khoon", "bleeding", "fracture", "pair toot", "haath toot", "haddi toot",
                   "taang toot", "leg toot", "chot", "chot lagi", "tez bukhar", "high fever", "labour pain",
                   "delivery", "saans", "breathing problem", "ambulance", "doctor", "medical", "oxygen",
                   "current laga", "karant", "bijli ke taar", "घायल", "खून"]
MEDICAL_MINOR = ["bukhar", "fever", "dard", "pain", "ulti", "vomit", "loose motion", "diarrhea"]
FOOD = ["khana", "khaana", "food", "bhookh", "bhook", "bhukhe", "ration", "rashan", "roti", "doodh", "dudh",
        "milk", "baby food", "खाना", "भूख", "दूध"]
WATER_NEED = ["peene ka paani", "pine ka pani", "peene ka pani", "drinking water", "pani khatam", "paani khatam",
              "paani ki bottle", "pani ki bottle", "paani ki botal", "water bottle", "पीने का पानी"]
MEDICINE = ["dawai", "dawa", "davai", "medicine", "insulin", "tablet", "दवाई", "दवा"]
# "relief camp me" means they are already in a shelter: only asking for one counts
SHELTER = ["shelter", "rehne ki jagah", "chhat nahi", "ghar gir", "camp chahiye", "camp bhejo"]
WELFARE = ["phone nahi utha", "phone nahi uth", "phone band", "sampark nahi", "contact nahi", "no contact",
           "phone nahi lag", "phone nahi mil", "call nahi lag", "not reachable", "unreachable", "फोन नहीं लग",
           "not answering", "koi khabar nahi", "फोन नहीं उठा", "संपर्क नहीं"]
# a status update, not a request: "aag bujh gayi sab bahar aa gaye", "sab theek hai bas safai chahiye".
# Only removes the rescue we INFERRED from "water + people"; an explicit "phase hai" / "bachao" still counts.
SAFE = ["sab theek", "sab thik", "sab safe", "safe hai", "sab bahar aa gaye", "sab bahar aa gye", "bahar aa gaye",
        "bujh gayi", "bujh gaya", "bujh gai", "khatra nahi", "khatra tal", "all safe", "everyone safe", "we are safe",
        "madad nahi chahiye", "help nahi chahiye", "सब ठीक", "सुरक्षित हैं"]
EVAC = ["nikalna hai", "evacuate", "evacuation", "bahar nikalo", "shift karna"]

WATER = ["paani", "pani", "water", "flood", "flooded", "flooding", "flud", "baadh", "badh", "baad aa", "baad aagayi",
         "doob", "pani bhar", "पानी", "बाढ़", "बाढ", "बाड़", "बाड", "फ्लड", "फ्लूड"]
# moving water sweeps people away: scored like a roof rescue, not like "water entering house"
ROOF_WORDS = ["chhat pe", "chhat par", "roof", "chhat tak", "chat pe", "छत पर", "छत पे", "छत तक"]
SWIFT = ["paani tez", "pani tez", "tez paani", "tez pani", "tez bahav", "bahav tez", "nadi ke kinare", "nadi me",
         "nadi mein", "beh rahe", "beh rahi", "beh gaye", "beh gaya", "bah rahe", "current tez", "swift"]
RISING = ["badh raha", "badh rahi", "badh gaya", "tezi se badh", "rising", "बढ़ रहा"]
# running out of these is dangerous within hours, not a routine supply request
VITAL_MEDS = ["insulin", "dialysis", "oxygen cylinder", "inhaler"]
FIRE = ["aag", "fire", "jal raha", "jal rahi", "dhuan", "smoke", "आग"]
COLLAPSE = ["gir gaya", "gir gayi", "collapse", "collapsed", "malba", "debris", "deewar giri", "diwar giri",
            "makaan gira", "building giri", "गिर गया", "मलबा"]
LANDSLIDE = ["landslide", "bhooskhalan", "pahad gira", "pahad khisak", "bhuskhalan", "भूस्खलन"]

QUESTION_ONLY = ["kab tak", "helpline", "hai kya", "hain kya", "kab aayegi", "kab aayega", "kya hal", "update", "news", "kab rukegi", "kab ruke",
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
    n = stated_people(t)
    if n is not None:
        return n, False
    if re.search(r"\b(?:hum|ham)\s+dono\b|\bdono\s+" + PEOPLE_WORDS, t) or re.search(r"\bmujhe aur meri?\b", t):
        return 2, False
    if _has(t, ["couple", "dampati", "dampatti", "pati patni", "mian biwi", "दंपति", "पति पत्नी"]):
        return 2, False
    if _has(t, ["pura parivar", "poora parivar", "puri family", "whole family", "family", "parivar"]):
        return 4, True          # estimated, shown as estimated on the dashboard
    if _has(t, ALONE) or re.search(r"\bmain\b.*\b(hu|hoon|hun)\b", t):
        return 1, False
    if len(RELATIVE.findall(t)) == 1 and not _has(t, ["aur", "and", "sab", "family", "parivar"]):
        return 1, False         # "mere papa ko heart attack" -> one person
    if _has(t, ELDERLY + PREGNANT) and not _has(t, CHILD):
        return 1, False         # "meri dadi phasi hai" -> one person
    return None, False


def stated_people(t: str) -> Optional[int]:
    """Only a number the sender actually wrote ("5 log", "do bacche", "200 students"); None otherwise.
    The verifier uses it to keep apart two cases at one place (200 students vs 3 people)."""
    # "gali 4 buzurg aurat" is lane 4, not 4 people
    t = re.sub(r"\b(?:gali|galli|lane|street|sector|block|ward|no|number|house|makaan|flat|plot)\s*\d+", " ", t)
    m = re.search(rf"(\d+)\s*{SIZE}{PEOPLE_WORDS}", t)
    if m:
        return int(m.group(1))
    for w, n in NUM_WORDS.items():
        if re.search(rf"\b{w}\s+{SIZE}{PEOPLE_WORDS}", t):
            return n
    return None


def _age(t: str, raw: str = "") -> Optional[int]:
    m = re.search(r"(\d{2,3})\s*(?:saal|sal|years|yrs|year|baras|वर्ष|साल)", t) or re.search(r"\b(\d{2})\s*\+", raw)
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
    text = geo.nfc(text)
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
    age = _age(t, raw_lower)
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
    elif depth is not None or _has(t, SWIFT) or _has(t, ROOF_WORDS):
        ex.hazard = HazardType.water
    if ex.hazard == HazardType.water:
        ex.water_depth_ft = depth
        if _has(t, ROOF_WORDS):
            ex.hazard_detail = "people on roof / water near roof"
        elif _has(t, SWIFT):
            ex.hazard_detail = "fast-moving water / river bank"
        elif _has(t, RISING):
            ex.hazard_detail = "water rising" + (f" ~{depth:g} ft" if depth is not None else "")
        elif depth is not None:
            ex.hazard_detail = f"water ~{depth:g} ft"
        else:
            ex.hazard_detail = "water entering house"

    # medical
    if _has(t, MEDICAL_LIFE):
        ex.medical = MedicalSeverity.life_threatening
    elif (_has(t, MEDICAL_SERIOUS) or _has(t, VITAL_MEDS)
          or (Vulnerable.pregnant in ex.vulnerable and _has(t, ["dard", "pain"]))):
        ex.medical = MedicalSeverity.serious
    elif _has(t, MEDICAL_MINOR):
        ex.medical = MedicalSeverity.minor

    # needs
    needs: list[Need] = []
    in_water_with_person = ex.hazard == HazardType.water and (
        ex.vulnerable or ex.people_count or _has(t, ["ghar", "house", "andar", "inside"]))
    explicit_rescue = _has(t, RESCUE)
    safe_update = _has(t, SAFE) and not re.search(r"\b(?:theek|thik|safe)\s+nahi", t)   # "sab theek nahi hai"
    inferred = in_water_with_person or ex.hazard in (HazardType.collapse, HazardType.landslide, HazardType.fire)
    if explicit_rescue or (inferred and not safe_update):
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
    if _has(t, WELFARE):
        needs.append(Need.welfare_check)
    plea = _has(t, PLEA)
    if not needs and ex.hazard and plea and not safe_update:
        needs.append(Need.rescue)
    if not needs and safe_update:
        ex.is_help_request = False          # status update: shown in Activity, never a new case
        return ex
    if not needs and not plea and (_has(t, QUESTION_ONLY) or raw_lower.strip().endswith("?")):
        needs.append(Need.information)
        ex.is_help_request = False
    if not needs and not ex.hazard and not ex.vulnerable and not plea:   # "madad chahiye" alone is still a request
        ex.is_help_request = False
    ex.needs = needs
    return ex


def is_status_update(text: str) -> bool:
    """'aag bujh gayi', 'sab theek hai': the sender is reporting, not asking."""
    return _has(geo.normalize(text), SAFE)


def people_factor(n: Optional[int], has_need: bool) -> float:
    """Shared helper used by priority: 1 person -> 2, 6 -> 7.2, 20+ -> 10."""
    if n is None:
        return 2.0 if has_need else 0.0
    return round(min(10.0, 2.0 + 2.0 * math.log2(max(n, 1))), 1)
