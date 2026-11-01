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
    ex = rules.extract("Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    assert ex.place_id == "DEEP_NAGAR"
    assert Vulnerable.elderly in ex.vulnerable
    assert Need.rescue in ex.needs
    assert ex.water_depth_ft == 5
    assert ex.people_count == 1


def test_unknown_place_stays_null():
    assert rules.extract("mothro wala me paani").place_id == "MOTHROWALA"     # typo-tolerant
    assert rules.extract("sector 9 me paani").place_id is None              # not on our map -> null, not a guess
    assert rules.extract("दीप नगर में पानी").place_id == "DEEP_NAGAR"         # Devanagari


def test_ramesh_lone_elderly():
    ex = rules.extract("main akela hu, 75 saal, ghar me paani aa gaya, kanwali mandir ke peeche")
    assert ex.place_id == "KANWALI"
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
    food = _inc("FOOD", 30.31349, 78.05924, 10.0, t0)                       # arrives first
    dadi = _inc("DADI", 30.28857, 78.05132, 18.0, t0 + timedelta(seconds=5))  # critical
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
    incs = [_inc(f"I{i}", 30.29 + i * 0.004, 78.01 + i * 0.008, 5 + i, t0) for i in range(6)]
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
    dadi = by_place["DEEP_NAGAR"]
    assert len(dadi) == 1 and len(dadi[0].message_ids) == 3
    assert dadi[0].band == Band.CRIT and dadi[0].confidence >= 0.8
    assert dadi[0].callback.status == "not_needed"      # 3 independent relatives: no callback left pending

    # food at the school is a separate incident (not merged with dadi)
    assert len(by_place["DALANWALA"]) == 1

    # fake: one incident, red flags, verify first, never deleted, no team recommended
    fake = by_place["HAR_KI_PAURI"][0]
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
    assert rec[by_place["DALANWALA"][0].id] == "BOAT-B"
    assert fake.id not in rec

    # follow-up reply joins the vague incident and gets a location
    send(r, "S-vague-1", "parade ground ke paas wali gali")
    v = r.store.get("incidents", vague[0].id)
    assert v.location.place_id == "PARADE_GROUND" and len(v.message_ids) == 2


def test_lone_victim_stays_high_and_callback_runs(r):
    send(r, "S-ramesh", "main akela hu, 75 saal, ghar me paani aa gaya, kanwali mandir ke peeche")
    inc = r.store.all("incidents")[0]
    assert inc.band == Band.CRIT
    assert inc.confidence >= config.CONF_HIGH            # neutral, not low
    assert inc.callback.status == "pending"              # verification in parallel
    rec = [a for a in r.store.all("assignments") if a.incident_id == inc.id]
    assert rec and rec[0].team_id == "BOAT-C"            # a team still moves


def test_approve_rescue_resolve_loop(r):
    send(r, "S-dadi-1", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
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
    m = send(r, "S-x", "mothrowala me 3 log phase hai paani 4 feet")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn())
    r.team_status(a.team_id, TeamStatusIn(status="no_one_here"))
    assert r.store.get("incidents", a.incident_id).status.value == "false"
    assert r.store.reputation(m.sender_hash) < 1.0


def test_gps_ping_moves_team_without_changing_assignment(r):
    send(r, "S-dadi-1", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn())
    r.team_status(a.team_id, TeamStatusIn(status="location", lat=30.3111, lng=78.0400))
    t = r.store.get("teams", a.team_id)
    assert (t.lat, t.lng) == (30.3111, 78.0400) and t.status.value == "busy"
    assert r.store.get("assignments", a.id).status.value == "approved"


def test_reject_bans_pair(r):
    send(r, "S-dadi-1", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn(action="reject"))
    rec = [x for x in r.store.all("assignments") if x.status.value == "recommended"]
    assert rec and rec[0].team_id != a.team_id


# ----------------------------------------------------------------- citizen chat (multi-turn)
def replies(r, m):
    return [o["text"] for o in reversed(r.store.outbox(m.sender_hash))]


def test_chat_same_sender_stays_one_incident_and_every_message_is_answered(r):
    first = send(r, "S-chat", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    send(r, "S-chat", "dadi 80 saal ki hai, chal nahi sakti")
    send(r, "S-chat", "kab tak aayenge?")
    assert len(r.store.all("incidents")) == 1                       # more detail, not a new case
    out = replies(r, first)
    assert len(out) == 4 and "pahunch gaya" in out[0]                # ack once, on first contact only
    assert "bana diya" in out[1] and "jod di" in out[2] and "jod di" in out[3]
    assert not any("kahan" in t.lower() for t in out)               # never asks where after being told


def test_chat_asks_each_missing_field_once(r):
    m = send(r, "S-vague-chat", "khana chahiye 4-5 log hai")
    send(r, "S-vague-chat", "pata nahi bhai")
    send(r, "S-vague-chat", "Dalanwala school ke paas")
    out = replies(r, m)
    assert sum("kahan" in t.lower() for t in out) == 1
    assert len(out) == 4 and "jod di" in out[-1]
    assert len(r.store.all("incidents")) == 1


def test_same_sender_new_place_is_a_new_incident(r):
    send(r, "S-volunteer", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    send(r, "S-volunteer", "MAJRA mein chhat pe 6 log phase hai bacche bhi hai")
    assert len(r.store.all("incidents")) == 2                       # a volunteer reporting for others


def test_chat_gets_team_updates(r):
    m = send(r, "S-dadi-1", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn())
    send(r, "S-dadi-1", "kab tak aayenge?")
    r.team_status(a.team_id, TeamStatusIn(status="reached"))
    r.team_status(a.team_id, TeamStatusIn(status="rescued"))
    out = replies(r, m)
    assert "bheji gayi" in out[2] and "aa rahi hai" in out[3]
    assert "pahunch gayi" in out[4] and "poora kar diya" in out[5] and len(out) == 6   # each update sent once


# ----------------------------------------------------------------- rules fallback must not drop real pleas
@pytest.mark.parametrize("text", ["bachaoooo", "mujhe madad chahiye", "me car ke neeche dab rkha hua 2-4 log chahiye",
                                  "kya koi madad karega?"])
def test_rules_pleas_are_help_requests(text):
    assert rules.extract(text).is_help_request


def test_rules_question_and_food_box_stay_as_they_were():
    assert not rules.extract("bijli kab tak aayegi koi batao").is_help_request
    assert Need.rescue not in rules.extract("khane ka dabba chahiye 5 log").needs


def test_unreadable_message_goes_to_human_review_not_noise(r):
    m = send(r, "S-odd", "hello ji suno")
    assert m.status.value == "review" and not r.store.all("incidents")
    assert "khud dekhega" in replies(r, m)[-1]
    assert send(r, "S-q", "kab tak barish rukegi?").status.value == "info"     # a clear question is still info


def test_citizen_hears_back_at_every_step(r):
    """The person who asked must not go silent after one reply: ack, dispatch, on the way, reached, done."""
    from backend.service import hash_sender
    send(r, "S-dadi-1", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    send(r, "S-dadi-2", "deep nagar gali 3 old lady trapped plz")          # second person, same case
    a = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a.id, ApproveIn())
    for s in ("en_route", "reached", "rescued"):
        r.team_status(a.team_id, TeamStatusIn(status=s))
    first = [m["text"] for m in reversed(r.store.outbox(hash_sender("S-dadi-1")))]
    assert first[0].startswith("RAHAT: Aapka message control room tak pahunch gaya")
    assert any("bheji gayi hai" in t for t in first)
    assert any("raaste mein" in t for t in first)
    assert any("pahunch gayi hai" in t for t in first)
    assert any("kaam poora" in t for t in first)
    second = [m["text"] for m in r.store.outbox(hash_sender("S-dadi-2"))]
    assert any("pehle se control room mein hai" in t for t in second)       # told it was merged, not ignored
    assert any("raaste mein" in t for t in second)                         # and gets the same updates


def test_noise_gets_a_helpful_reply(r):
    from backend.service import hash_sender
    send(r, "S-q", "kab tak barish rukegi?")
    texts = [m["text"] for m in r.store.outbox(hash_sender("S-q"))]
    assert any("madad ki request nahi mili" in t for t in texts)


def test_operator_radio_update_and_late_phone_tap_is_ignored(r):
    """Radio fallback: the operator enters 'rescued' for the team. The team's own phone was offline and syncs
    its old taps later: they must not touch the team's NEXT job."""
    send(r, "S-dadi-1", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")
    a1 = [x for x in r.store.all("assignments") if x.status.value == "recommended"][0]
    r.decide(a1.id, ApproveIn())
    r.team_status(a1.team_id, TeamStatusIn(status="rescued", assignment_id=a1.id, by="operator-1", via="radio"))
    inc1 = r.store.get("incidents", a1.incident_id)
    assert inc1.status.value == "resolved"
    assert any("operator-1 entered 'rescued'" in n and "radio" in n for n in inc1.notes)

    # the freed boat gets a new job
    send(r, "S-nehru", "Nehru colony me 3 log phase hai paani 5 feet hai")
    a2 = [x for x in r.store.all("assignments") if x.status.value == "recommended" and x.team_id == a1.team_id]
    assert a2, "freed team should be suggested for the next case"
    r.decide(a2[0].id, ApproveIn())

    # the phone comes back online and sends its stale 'reached' for the OLD job
    res = r.team_status(a1.team_id, TeamStatusIn(status="reached", assignment_id=a1.id, via="phone"))
    assert res.get("ignored") == "stale assignment"
    assert r.store.get("assignments", a2[0].id).status.value == "approved"     # new job untouched
    assert "stale_status_ignored" in [x.action for x in r.store.audit_list(50)]


def test_voice_note_is_transcribed_then_extracted(r, monkeypatch, tmp_path):
    """Voice -> text -> the same extractor agent -> a case. The transcript is kept and labelled."""
    from agents import speech
    up = config.ROOT / "uploads"
    up.mkdir(exist_ok=True)
    f = up / "test-voice-note.ogg"
    f.write_bytes(b"fake-audio")
    try:
        monkeypatch.setattr(speech, "engine", lambda: "gemini")
        monkeypatch.setattr(speech, "_gemini", lambda p: ("Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai", "gemini:test"))
        m = r.ingest(MessageIn(channel="voice", sender_id="S-v", audio_url=str(f)), wait=True)
        assert m.raw_text.startswith("Bhai Deep Nagar") and m.meta["transcribed_by"] == "gemini:test"
        inc = r.store.get("incidents", m.incident_id)
        assert inc.location.place_id == "DEEP_NAGAR" and inc.band == Band.CRIT

        monkeypatch.setattr(speech, "_gemini", lambda p: (None, "gemini(no speech heard)"))
        m2 = r.ingest(MessageIn(channel="voice", sender_id="S-v2", audio_url=str(f)), wait=True)
        assert m2.status.value == "review" and "please listen" in m2.meta["review_reason"]   # never dropped
    finally:
        f.unlink(missing_ok=True)


def test_speech_refuses_files_outside_uploads(tmp_path):
    from agents import speech
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"not for outside services")
    text, why = speech.transcribe(str(secret))
    assert text is None and "refused" in why


# ----------------------------------------------------------------- SOP RAG
def test_sop_rag_only_cites_real_passages(monkeypatch):
    """Checklist items come only from the response chapter of a document that covers the hazard,
    each with its page; a hazard the documents do not cover is NOT_IN_SOP (no unrelated fallback)."""
    from rag import sop
    from shared.schemas import HazardType, TeamType
    text = ("<<PAGE 3>>\n2.1 Planning Background\nState governments will prepare long term flood plans and dams.\n"
            "<<PAGE 9>>\n8.1 Evacuation Of People\nRescue teams will evacuate elderly people and children first "
            "using boats and life jackets. Relief camps shall be set up with food and drinking water.\n")
    meta = {"title": "Guidelines: Management of Floods", "year": 2008, "hazards": ["water"], "response_sections": ["8"]}
    chunks = sop.chunk_text("doc_floods", text, meta)
    assert [c.response for c in chunks] == [False, True]
    assert chunks[1].page == 9 and chunks[1].hazards == {"water"}
    monkeypatch.setattr(sop, "load_chunks", lambda: tuple(chunks))
    monkeypatch.setattr(sop, "sources", lambda: {"doc_floods": meta})
    monkeypatch.setattr(sop, "_llm_items", lambda q, h: None)          # extractive path, no API
    now = now_utc()
    flood = Incident(id="F", created_at=now, updated_at=now, needs=[Need.rescue], vulnerable=[Vulnerable.elderly],
                     hazard=HazardType.water, required_team_types=[TeamType.boat])
    r = sop.checklist(flood)
    assert r["status"] == "OK" and r["items"]
    assert all("PDF p.9" in it["source"] and "§8.1" in it["source"] for it in r["items"])   # never the planning chapter
    fire = Incident(id="X", created_at=now, updated_at=now, needs=[Need.rescue], hazard=HazardType.fire)
    assert sop.checklist(fire)["status"] == "NOT_IN_SOP"


# ----------------------------------------------------------------- merging: same place is not enough
def test_extractor_knows_common_flood_words():
    assert Need.rescue in rules.extract("mujhe aur meri wife ko nikalo mothrowala, hum dono 75+ hai").needs
    assert rules.extract("mujhe aur meri wife ko nikalo mothrowala, hum dono 75+ hai").people_count == 2
    assert Need.rescue in rules.extract("Har ki pauri pe mera bhai phansa hai").needs
    assert Need.food in rules.extract("ISBT ke paas bachon ke liye doodh chahiye, 10 chhote bacche").needs
    assert rules.extract("ISBT ke paas bachon ke liye doodh chahiye, 10 chhote bacche").people_count == 10
    assert Need.medical in rules.extract("Dalanwala me dadi ka oxygen machine band ho gaya").needs
    assert Need.medical in rules.extract("Nehru colony me pregnant didi ko dard shuru ho gaya").needs
    assert rules.extract("GEHU ke paas 3 log nadi ke kinare phase, paani tez").hazard_detail.startswith("fast-moving")
    # status updates and questions are not new emergencies
    assert not rules.extract("paltan bazar aag bujh gayi sab bahar aa gaye").is_help_request
    assert not rules.extract("Rispana pul toot gaya kya? koi update").is_help_request
    assert not rules.extract("kanwali road pe naali ka paani ghar me, sab theek hai bas safai chahiye").is_help_request
    # an explicit plea still counts even if the sender says others are safe
    assert Need.rescue in rules.extract("baaki sab theek hai par dadi phasi hai deep nagar").needs


def test_different_lanes_stay_separate(r):
    a = send(r, "L1", "Deep Nagar gali 4 me nani phasi hai paani 4 feet")
    b = send(r, "L2", "Deep Nagar gali 2 me paani kamar tak, do bacche phase hai")
    c = send(r, "L3", "deep nagar gali 4 buzurg aurat phasi hai boat bhejo")
    a, b, c = (r.store.get("messages", m.id) for m in (a, b, c))
    assert a.incident_id == c.incident_id            # same lane, same story: one case
    assert b.incident_id != a.incident_id            # other lane: its own case


def test_different_emergencies_at_one_landmark_stay_separate(r):
    a = send(r, "K1", "Kargi chowk ke paas mere papa ko heart attack aaya hai, ambulance nahi aa rahi")
    b = send(r, "K2", "GEHU ke paas 3 log nadi ke kinare phase, paani tez")
    c = send(r, "K3", "graphic era ke hostel ke neeche paani, 200 students andar safe hai par khana kal tak ka hai")
    ids = {r.store.get("messages", m.id).incident_id for m in (a, b, c)}
    assert len(ids) == 3


def test_real_message_not_pulled_into_copy_paste_fake(r):
    for k in range(4):
        send(r, f"F{k}", "URGENT 100 log phase hai har ki pauri ghat pe turant NDRF bhejo")
    real = send(r, "R1", "Har ki pauri pe mera bhai phansa hai, wahan bhi paani badh raha")
    real = r.store.get("messages", real.id)
    inc =r.store.get("incidents", real.incident_id)
    assert inc is not None and len(set(inc.sender_hashes)) == 1      # its own case, not the fake cluster
    assert not any(f.startswith("copy_paste_burst") for f in inc.red_flags)


def test_status_update_never_marks_someone_elses_case(r):
    fire = send(r, "P1", "paltan bazar me aag lagi hai 3 log andar phase hai")
    upd = send(r, "P2", "paltan bazar aag bujh gayi sab bahar aa gaye")
    upd = r.store.get("messages", upd.id)
    inc = r.store.get("incidents", r.store.get("messages", fire.id).incident_id)
    assert upd.incident_id is None and upd.status.value == "info"
    assert inc.status.value in ("open", "assigned")                  # NOT closed by a stranger's message
    assert any("update from another sender" in n for n in inc.notes)  # but the operator sees it on the case


def test_status_update_gets_a_thank_you_not_an_error(r):
    from backend.service import hash_sender
    send(r, "U1", "paltan bazar aag bujh gayi sab bahar aa gaye")
    texts = [m["text"] for m in r.store.outbox(hash_sender("U1"))]
    assert any("Update ke liye dhanyavaad" in t for t in texts)


def test_lane_number_is_not_a_head_count():
    assert rules.extract("deep nagar gali 4 buzurg aurat phasi hai boat bhejo").people_count == 1
    assert rules.extract("gali no 3 me 5 log phase hai").people_count == 5


def test_fire_then_flood_from_same_sender_gets_both_teams(r):
    # real demo case: text "aag lag gayi", then a voice note "baadh aa gayi" (Whisper writes बाड)
    send(r, "S-fh", "Bchao muje aag lg gai yha Clement Town")
    m = send(r, "S-fh", "बजाग। कलमटाँन है बाड आगाग हैग प्लीस बजाग [English: Please save me, I'm in Clemontown]")
    inc = r.store.get("incidents", m.incident_id)
    assert {h.value for h in inc.hazards} == {"fire", "water"}
    assert {t.value for t in inc.required_team_types} >= {"fire", "boat"}


def test_flood_spellings_are_water_hazard():
    for t in ["baad aa gayi hai ghar me", "बाड आ गई है", "flud aa gaya", "फ्लड आया है"]:
        assert rules.extract(t + " 3 log phase hain Majra").hazard is not None, t


def test_hindi_script_flood_people_and_place():
    # ढ़ typed as ONE character (U+095D) must read like ढ + nukta; Hindi numbers and digits count people
    e = rules.extract("भाई सुभाष नगर में बा\u095d आ चुकी है, दो पीपल्स को नीड है जल्दी आ जाओ")
    assert e.hazard is not None and e.hazard.value == "water"
    assert e.people_count == 2 and e.location_text == "Subhash Nagar" and e.is_help_request
    assert rules.extract("माजरा में ५ लोग फंसे हैं").people_count == 5
    assert rules.extract("माजरा में पानी आया, आग नहीं").hazard.value in ("water", "fire")   # parses, no crash
    assert rules.extract("आगाग हैग").hazard is None         # "आग" inside a longer word is not fire
