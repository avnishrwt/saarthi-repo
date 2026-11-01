"""
Dispatcher agent: map an incident's needs to the team TYPES it requires (PRD 6.5 step 1).
Deterministic on purpose: a wrong team type is a safety issue, so this is a table a
judge can read, not an LLM guess. The optimizer then picks WHICH team of each type.
"""
from __future__ import annotations

from shared.schemas import HazardType, Incident, Need, TeamType


def required_team_types(inc: Incident, surge_hazard: str = "flood") -> list[TeamType]:
    types: list[TeamType] = []

    def add(t: TeamType):
        if t not in types:
            types.append(t)

    needs = set(inc.needs)
    hazards = set(inc.hazards) | ({inc.hazard} if inc.hazard else set())
    flooded = bool(inc.location.flooded) or HazardType.water in hazards
    if Need.rescue in needs or Need.evacuation in needs or Need.welfare_check in needs:
        # one team per danger reported: "aag lagi" + "baadh aa gayi" -> fire team AND boat
        if HazardType.fire in hazards:
            add(TeamType.fire)
        if hazards & {HazardType.collapse, HazardType.landslide}:
            add(TeamType.search_rescue)
        if flooded or (not types and surge_hazard == "flood"):
            add(TeamType.boat)
        if not types:
            add(TeamType.search_rescue)
    if Need.medical in needs:
        add(TeamType.medical)            # boat + medical alert when both are needed
    if needs & {Need.food, Need.water, Need.medicine, Need.shelter} and not types:
        add(TeamType.relief)             # trucks; optimizer substitutes a boat if the lane is flooded
    return types


def explain(types: list[TeamType]) -> str:
    return " + ".join(t.value for t in types) if types else "no team (information only)"
