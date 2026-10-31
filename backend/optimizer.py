"""
Team assignment (PRD 6.5, FR09).

Each incident needs one or more team TYPES (from the Dispatcher). Each (incident,
type) is a SLOT. We solve all free teams x all open slots at once with OR-Tools
min-cost flow:

    source --(1)--> team --(1, cost)--> slot --(1)--> sink
             team --(1, IDLE)-----------------------> sink

IDLE is huge, so the solver first fills as many slots as possible, then picks the
cheapest way to do it. Cost per pair (see shared/config.py COST_MODE):

    weighted: travel_min * (1 + P/10) - LAMBDA * P + mismatch_penalty
    prd:      travel_min               - LAMBDA * P + mismatch_penalty

Filters (no arc at all): busy team, wrong skill with no allowed substitute, a
non-boat team for a flooded location, the operator rejected this pair, incident
in "verify_first" or with no location.
Same input -> same output, every time (unlike an LLM), and every number is shown.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from agents import geo
from shared import config
from shared.schemas import Incident, Team, TeamStatus, TeamType

SCALE = 100          # OR-Tools needs integer costs
IDLE_COST = 10 ** 9


@dataclass
class Slot:
    incident: Incident
    team_type: TeamType


@dataclass
class Pair:
    team: Team
    slot: Slot
    cost: float
    eta_min: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reason: str = ""


def speed(team: Team) -> float:
    return team.speed_kmh or config.SPEED_KMH[team.type.value]


def pair_cost(team: Team, slot: Slot, mode: Optional[str] = None) -> Optional[Pair]:
    inc = slot.incident
    if inc.location.lat is None:
        return None
    penalty, how = 0.0, "exact skill"
    if team.type != slot.team_type:
        subs = dict(config.SUBSTITUTES.get(slot.team_type.value, []))
        if team.type.value not in subs:
            return None
        penalty, how = subs[team.type.value], f"{team.type.value} standing in for {slot.team_type.value}"
    if inc.location.flooded and team.type != TeamType.boat:
        return None                                   # road blocked: only boats reach
    if inc.people_count and team.type in (TeamType.boat, TeamType.search_rescue) and inc.people_count > team.capacity:
        penalty += config.OVER_CAPACITY_PENALTY
        how += f"; {inc.people_count} people > capacity {team.capacity} (may need 2 trips)"
    eta = geo.travel_minutes(team.lat, team.lng, inc.location.lat, inc.location.lng, speed(team))
    p = inc.priority_score
    mode = mode or config.COST_MODE
    travel_term = eta * (1 + p / 10) if mode == "weighted" else eta
    cost = travel_term - config.LAMBDA * p + penalty
    return Pair(team, slot, round(cost, 2), round(eta, 1),
                {"travel_min": round(eta, 1), "travel_term": round(travel_term, 2),
                 "priority_term": round(-config.LAMBDA * p, 2), "penalty": penalty},
                f"{team.name} -> {inc.id}: ETA {eta:.0f} min, {how}")


def open_slots(incidents: Iterable[Incident], filled: Counter) -> list[Slot]:
    """filled[(incident_id, team_type)] = how many active (approved/en route/on scene) teams of
    that type it already has. required_team_types may repeat a type ("need backup")."""
    slots = []
    for inc in incidents:
        if inc.status.value not in ("open", "assigned") or inc.action in ("verify_first", "need_location", "closed"):
            continue
        need = Counter(t.value for t in inc.required_team_types)
        for t, n in need.items():
            for _ in range(max(0, n - filled.get((inc.id, t), 0))):
                slots.append(Slot(inc, TeamType(t)))
    return slots


def solve(teams: list[Team], slots: list[Slot], banned: set[tuple[str, str]] = frozenset(),
          mode: Optional[str] = None) -> list[Pair]:
    from ortools.graph.python import min_cost_flow

    free = [t for t in teams if t.status == TeamStatus.free]
    if not free or not slots:
        return []
    pairs: dict[tuple[int, int], Pair] = {}
    for i, t in enumerate(free):
        for j, s in enumerate(slots):
            if (s.incident.id, t.id) in banned:
                continue
            pc = pair_cost(t, s, mode)
            if pc:
                pairs[(i, j)] = pc
    if not pairs:
        return []
    T, S = len(free), len(slots)
    src, sink = 0, T + S + 1
    mcf = min_cost_flow.SimpleMinCostFlow()
    for i in range(T):
        mcf.add_arc_with_capacity_and_unit_cost(src, 1 + i, 1, 0)
        mcf.add_arc_with_capacity_and_unit_cost(1 + i, sink, 1, IDLE_COST)
    arc_of: dict[int, tuple[int, int]] = {}
    for (i, j), pc in pairs.items():
        a = mcf.add_arc_with_capacity_and_unit_cost(1 + i, 1 + T + j, 1, int(round(pc.cost * SCALE)))
        arc_of[a] = (i, j)
    for j in range(S):
        mcf.add_arc_with_capacity_and_unit_cost(1 + T + j, sink, 1, 0)
    mcf.set_node_supply(src, T)
    mcf.set_node_supply(sink, -T)
    status = mcf.solve()
    if status != mcf.OPTIMAL:
        raise RuntimeError(f"min-cost flow failed: status {status}")
    chosen = [pairs[ij] for a, ij in arc_of.items() if mcf.flow(a) > 0]
    # one team must not take two slots of the same incident (flow already guarantees 1 slot per team)
    return sorted(chosen, key=lambda p: -p.slot.incident.priority_score)


def greedy(teams: list[Team], slots: list[Slot], mode: Optional[str] = None) -> list[Pair]:
    """BASELINE: 'nearest free team' habit. Slots served in arrival order, each takes the
    closest free capable team. This is what the optimizer must beat."""
    free = [t for t in teams if t.status == TeamStatus.free]
    out = []
    for s in sorted(slots, key=lambda s: s.incident.created_at):
        options = [pc for t in free if (pc := pair_cost(t, s, mode))]
        if not options:
            continue
        best = min(options, key=lambda pc: pc.eta_min)
        out.append(best)
        free.remove(best.team)
    return out


def summary(pairs: list[Pair], slots: list[Slot], critical_target_min: float = 15.0) -> dict:
    crit_ids = {s.incident.id for s in slots if s.incident.band.value == "CRIT"}
    waits = {}
    for p in pairs:
        iid = p.slot.incident.id
        waits[iid] = max(waits.get(iid, 0), p.eta_min)
    crit_waits = [waits[i] for i in crit_ids if i in waits]
    unserved = sorted(crit_ids - set(waits))
    return {
        "assigned_slots": len(pairs),
        "open_slots": len(slots),
        "critical_incidents": len(crit_ids),
        "critical_unserved": unserved,
        "critical_within_target": sum(1 for w in crit_waits if w <= critical_target_min),
        "critical_target_min": critical_target_min,
        "longest_critical_wait_min": round(max(crit_waits), 1) if crit_waits else None,
        "total_travel_min": round(sum(p.eta_min for p in pairs), 1),
        "assignments": [{"incident": p.slot.incident.id, "title": p.slot.incident.title,
                         "band": p.slot.incident.band.value, "team": p.team.id, "team_name": p.team.name,
                         "eta_min": p.eta_min} for p in pairs],
    }
