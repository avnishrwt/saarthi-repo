"""
Priority score (PRD 6.1): how serious the incident is IF TRUE.
Confidence never enters this function: low confidence adds verification, it
never lowers priority (PRD 6, Ethics).

P = w1*medical + w2*people + w3*vulnerable + w4*hazard + w5*waiting   (each factor 0..10)
"""
from __future__ import annotations

from datetime import datetime

from agents.rules import people_factor
from shared import config
from shared.schemas import (Band, HazardType, Incident, MedicalSeverity, Need, PriorityFactor,
                            Vulnerable, now_utc)

MEDICAL = {MedicalSeverity.none: 0, MedicalSeverity.minor: 3, MedicalSeverity.serious: 7,
           MedicalSeverity.life_threatening: 10}
VULN = {Vulnerable.elderly: 8, Vulnerable.disabled: 8, Vulnerable.pregnant: 8, Vulnerable.child: 7}


def _medical(inc: Incident) -> tuple[float, str]:
    return MEDICAL[inc.medical], inc.medical.value.replace("_", " ")


def _vulnerable(inc: Incident) -> tuple[float, str]:
    kinds = [v for v in inc.vulnerable if v in VULN]
    if not kinds:
        return 0.0, "none reported"
    score = max(VULN[v] for v in kinds) + (len(set(kinds)) - 1)
    why = ", ".join(sorted({v.value for v in kinds}))
    if Vulnerable.alone in inc.vulnerable:
        score += 2
        why += " + alone"
    return float(min(score, 10)), why


def _hazard(inc: Incident) -> tuple[float, str]:
    if inc.hazard in (HazardType.fire, HazardType.collapse, HazardType.landslide):
        return 9.0, inc.hazard.value
    if inc.hazard == HazardType.water:
        if inc.hazard_detail and ("roof" in inc.hazard_detail or "fast-moving" in inc.hazard_detail):
            return 9.0, inc.hazard_detail
        d = inc.water_depth_ft
        if inc.hazard_detail and "rising" in inc.hazard_detail:
            return max(7.0, 8.0 if d and d >= 5 else 0.0), inc.hazard_detail
        if d is None:
            return 5.0, "water, depth not stated"
        if d >= 5:
            return 8.0, f"water ~{d:g} ft"
        if d >= 3:
            return 6.0, f"water ~{d:g} ft"
        return 4.0, f"water ~{d:g} ft"
    if Need.welfare_check in inc.needs and inc.location.flooded:
        return 5.0, "flooded area, no contact"
    return 0.0, "none reported"


def _waiting(inc: Incident, now: datetime) -> tuple[float, str]:
    mins = max(0.0, (now - inc.created_at).total_seconds() / 60)
    return round(min(10.0, mins / config.WAITING_MIN_PER_POINT), 1), f"{mins:.0f} min waiting"


def band_for(score: float) -> Band:
    for name, threshold in config.BANDS:
        if score >= threshold:
            return Band(name)
    return Band.LOW


def score(inc: Incident, now: datetime | None = None) -> Incident:
    """Fill priority_score, priority_breakdown and band in place; returns the incident."""
    now = now or now_utc()
    has_need = any(n != Need.information for n in inc.needs)
    people = people_factor(inc.people_count, has_need)
    p_why = (f"{inc.people_count} people" + (" (estimated)" if inc.people_estimated else "")
             if inc.people_count else "count not stated")
    factors = {
        "medical": _medical(inc),
        "people": (people, p_why),
        "vulnerable": _vulnerable(inc),
        "hazard": _hazard(inc),
        "waiting": _waiting(inc, now),
    }
    if inc.status in ("resolved", "false"):
        factors["waiting"] = (0.0, "closed")
    breakdown = {}
    total = 0.0
    for name, (value, why) in factors.items():
        w = config.PRIORITY_WEIGHTS[name]
        breakdown[name] = PriorityFactor(value=value, weight=w, weighted=round(value * w, 2), why=why)
        total += value * w
    inc.priority_breakdown = breakdown
    inc.priority_score = round(total, 1)
    inc.band = band_for(inc.priority_score)
    return inc
