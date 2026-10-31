"""Known-answer tests. Run: .venv\\Scripts\\python -m pytest -q"""
import json
from datetime import timedelta

import pytest

from agents import rules
from backend import optimizer
from backend.service import Rahat
from shared import config
from shared.schemas import (ApproveIn, Band, Incident, Location, MessageIn, Need, Team, TeamStatusIn,
                            TeamType, Vulnerable, now_utc)


# ----------------------------------------------------------------- extractor (rules)
def test_dadi_extraction():
    ex = rules.extract("Bhai Rampur gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    assert ex.place_id == "RAMPUR_G3"
    assert Vulnerable.elderly in ex.vulnerable
    assert Need.rescue in ex.needs
    assert ex.water_depth_ft == 5
    assert ex.people_count == 1


def test_gali_number_never_confused():
    assert rules.extract("rampur gali 5 me paani").place_id == "RAMPUR_G5"
    assert rules.extract("rampur gali 4 me paani").place_id is None    # not on our map -> null, not a guess


def test_ramesh_lone_elderly():
    ex = rules.extract("main akela hu, 75 saal, ghar me paani aa gaya, sector 4 mandir ke peeche")
    assert ex.place_id == "SECTOR4_MANDIR"
    assert {Vulnerable.elderly, Vulnerable.alone} <= set(ex.vulnerable)
    assert ex.people_count == 1


def test_vague_and_noise():
    ex = rules.extract("khana chahiye 4-5 log hai")
    assert ex.place_id is None and ex.people_count == 5 and Need.food in ex.needs
    noise = rules.extract("kab tak barish rukegi?")
    assert not noise.is_help_request


# ----------------------------------------------------------------- optimizer: the pitch moment
def _inc(id_, lat, lng, p, created):
    return Incident(id=id_, created_at=created, updated_at=created, priority_score=p,
                    band=Band.CRIT if p >= 16 else Band.MED, required_team_types=[TeamType.boat],
                    location=Location(lat=lat, lng=lng, place_id=id_, in_area=True, flooded=True), action="dispatch")


def test_optimizer_sends_team_a_to_dadi_greedy_does_not():
    teams = [Team(**t) for t in json.loads((config.DATA_DIR / "teams.json").read_text())
             if t["id"] in ("BOAT-A", "BOAT-B")]
    t0 = now_utc()
    food = _inc("FOOD", 30.3100, 78.049343, 10.0, t0)                       # arrives first
    dadi = _inc("DADI", 30.3100, 78.0300, 18.0, t0 + timedelta(seconds=5))  # critical
    slots = optimizer.open_slots([food, dadi], optimizer.Counter())
    rahat = {p.slot.incident.id: (p.team.id, p.eta_min) for p in optimizer.solve(teams, slots)}
    greedy = {p.slot.incident.id: (p.team.id, p.eta_min) for p in optimizer.greedy(teams, slots)}
    assert rahat["DADI"][0] == "BOAT-A" and round(rahat["DADI"][1]) == 10
    assert rahat["FOOD"][0] == "BOAT-B" and round(rahat["FOOD"][1]) == 6
    assert greedy["FOOD"][0] == "BOAT-A" and greedy["DADI"][0] == "BOAT-B"
    assert round(greedy["DADI"][1]) == 20


def test_optimizer_is_deterministic():
    teams = [Team(**t) for t in json.loads((config.DATA_DIR / "teams.json").read_text())]
    t0 = now_utc()
    incs = [_inc(f"I{i}", 30.30 + i * 0.003, 78.02 + i * 0.004, 5 + i, t0) for i in range(6)]
    slots = optimizer.open_slots(incs, optimizer.Counter())
    a = [(p.team.id, p.slot.incident.id) for p in optimizer.solve(teams, slots)]
    b = [(p.team.id, p.slot.incident.id) for p in optimizer.solve(teams, slots)]
    assert a == b


# ----------------------------------------------------------------- end-to-end story
@pytest.fixture()
def r(tmp_path):
    return Rahat(db_path=str(tmp_path / "t.db"), use_llm=False, workers=1)


def send(r, sender, text, **kw):
    return r.ingest(MessageIn(sender_id=sender, raw_text=text, **kw), wait=True)


def test_flood_night_story(r):
    scen = json.loads((config.DATA_DIR / "demo_scenario.json").read_text(encoding="utf-8"))
    for m in scen["messages"]:
        send(r, m["sender"], m["text"])
    incs = {i.id: i for i in r.store.all("incidents")}
    by_place = {}
    for i in incs.values():
        by_place.setdefault(i.location.place_id, []).append(i)

    # three dadi messages -> ONE incident, critical, high confidence
    dadi = by_place["RAMPUR_G3"]
    assert len(dadi) == 1 and len(dadi[0].message_ids) == 3
    assert dadi[0].band == Band.CRIT and dadi[0].confidence >= 0.8

    # food at the school is a separate incident (not merged with dadi)
    assert len(by_place["RAMPUR_SCHOOL"]) == 1

    # fake: one incident, red flags, verify first, never deleted, no team recommended
    fake = by_place["KALINAGAR_BRIDGE"][0]
    assert len(fake.message_ids) == 8
    assert any(f.startswith("copy_paste_burst") for f in fake.red_flags)
    assert any(f.startswith("out_of_area") for f in fake.red_flags)
    assert fake.action == "verify_first"

    # vague message -> incident without location + follow-up question sent
    vague = [i for i in incs.values() if i.location.lat is None]
    assert len(vague) == 1 and vague[0].followup_sent

    # noise never becomes an incident
    assert sum(1 for m in r.store.all("messages") if m.status.value == "info") == 2

    # recommendations: A -> dadi, B -> food; nothing for the fake
    rec = {a.incident_id: a.team_id for a in r.store.all("assignments") if a.status.value == "recommended"}
    assert rec[dadi[0].id] == "BOAT-A"
    assert rec[by_place["RAMPUR_SCHOOL"][0].id] == "BOAT-B"
    assert fake.id not in rec

    # follow-up reply joins the vague incident and gets a location
    send(r, "S-vague-1", "panchayat bhawan ke paas wali gali")
    v = r.store.get("incidents", vague[0].id)
    assert v.location.place_id == "PANCHAYAT_BHAWAN" and len(v.message_ids) == 2


def test_lone_victim_stays_high_and_callback_runs(r):
    send(r, "S-ramesh", "main akela hu, 75 saal, ghar me paani aa gaya, sector 4 mandir ke peeche")
    inc = r.store.all("incidents")[0]
    assert inc.band == Band.CRIT
    assert inc.confidence >= config.CONF_HIGH            # neutral, not low
    assert inc.callback.status == "pending"              # verification in parallel
    rec = [a for a in r.store.all("assignments") if a.incident_id == inc.id]
    assert rec and rec[0].team_id == "BOAT-C"            # a team still moves


def test_approve_rescue_resolve_loop(r):
    send(r, "S-dadi-1", "Bhai Rampur gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn(operator="op1"))
    assert r.store.get("teams", a.team_id).status.value == "busy"
    r.proactive_result("VR-01", "no_answer")              # Kamla ji: new incident while A is busy
    kamla = [i for i in r.store.all("incidents") if i.source == "proactive_check"][0]
    assert kamla.band in (Band.CRIT, Band.HIGH)
    r.team_status(a.team_id, TeamStatusIn(status="rescued"))
    assert r.store.get("incidents", a.incident_id).status.value == "resolved"
    rec = {x.incident_id: x.team_id for x in r.store.all("assignments") if x.status.value == "recommended"}
    assert rec.get(kamla.id) == a.team_id                 # re-solve: freed boat A is nearest to Kamla ji
    actions = [x.action for x in r.store.audit_list(500)]
    assert "approved" in actions and "status_rescued" in actions


def test_no_one_here_lowers_reputation(r):
    m = send(r, "S-x", "rampur gali 5 me 3 log phase hai paani 4 feet")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn())
    r.team_status(a.team_id, TeamStatusIn(status="no_one_here"))
    assert r.store.get("incidents", a.incident_id).status.value == "false"
    assert r.store.reputation(m.sender_hash) < 1.0


def test_gps_ping_moves_team_without_changing_assignment(r):
    send(r, "S-dadi-1", "Bhai Rampur gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn())
    r.team_status(a.team_id, TeamStatusIn(status="location", lat=30.3111, lng=78.0400))
    t = r.store.get("teams", a.team_id)
    assert (t.lat, t.lng) == (30.3111, 78.0400) and t.status.value == "busy"
    assert r.store.get("assignments", a.id).status.value == "approved"


def test_reject_bans_pair(r):
    send(r, "S-dadi-1", "Bhai Rampur gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn(action="reject"))
    rec = [x for x in r.store.all("assignments") if x.status.value == "recommended"]
    assert rec and rec[0].team_id != a.team_id
