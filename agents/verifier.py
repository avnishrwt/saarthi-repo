"""
Verifier agent: merge duplicates into incidents, compute confidence with NAMED red
flags, apply the priority x confidence action matrix (PRD FR04, FR06, FR07, 6.4).

Merging = density clustering with min_samples=1 done incrementally: a new message
joins the incident holding its nearest member if distance < MERGE_EPS (this is
what DBSCAN(min_samples=1) computes, one point at a time, so incident ids stay
stable while messages stream in). evaluation/evaluate.py also runs batch DBSCAN
on the same distance for comparison.

distance = 0.6*(1 - text_similarity) + 0.25*geo + 0.15*time + need_mismatch
Hard rules: both must have a location within MERGE_MAX_KM (a message with no
location only joins its own sender's incident, i.e. a follow-up reply). A sender's
next message joins their own live incident unless it names a different place
(a volunteer reporting a second emergency).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from agents import embeddings, geo, priority
from shared import config
from shared.schemas import (Band, Extraction, HazardType, Incident, IncidentStatus, Location, MedicalSeverity,
                            Message, MessageStatus, Need, Vulnerable)

SEVERITY = {MedicalSeverity.none: 0, MedicalSeverity.minor: 1, MedicalSeverity.serious: 2,
            MedicalSeverity.life_threatening: 3}

FOLLOWUP = {
    "hi": "राहत: आप कहाँ हैं? गली/मोहल्ला या पास का कोई लैंडमार्क बताइए। कितने लोग हैं और क्या मदद चाहिए?",
    "en": "RAHAT: Where are you? Reply with your street or a nearby landmark, how many people, and what help you need.",
    "hinglish": "RAHAT: Aap kahan hain? Gali/mohalla ya paas ka landmark bataiye. Kitne log hain aur kya madad chahiye?",
}
FOLLOWUP_NEED = {
    "hi": "राहत: आपको कौन सी मदद चाहिए? बचाव, दवा, खाना या पानी?",
    "en": "RAHAT: What help do you need? Rescue, medical, food or water?",
    "hinglish": "RAHAT: Aapko kya madad chahiye? Rescue, dawai, khana ya paani?",
}


# ------------------------------------------------------------------ location
def resolve_location(msg: Message, ex: Extraction) -> Location:
    p = geo.place_by_id(ex.place_id)
    if p:
        return Location(text=ex.location_text or p.name, place_id=p.place_id, lat=p.lat, lng=p.lng,
                        in_area=p.in_area, flooded=p.flooded)
    if msg.lat is not None and msg.lng is not None:       # volunteer pin / known registry location
        near = geo.nearest_place(msg.lat, msg.lng)
        close = near and geo.haversine_km(msg.lat, msg.lng, near.lat, near.lng) < 0.3
        return Location(text=ex.location_text or (f"near {near.name}" if close else "map pin"),
                        place_id=near.place_id if close else None, lat=msg.lat, lng=msg.lng,
                        in_area=geo.in_affected_area(msg.lat, msg.lng),
                        flooded=near.flooded if close else None)
    return Location(text=ex.location_text)


# ------------------------------------------------------------------ merging
def _distance(msg: Message, ex: Extraction, loc: Location, inc: Incident, members: list[Message]) -> float:
    if inc.location.lat is None or loc.lat is None:
        return 9.0
    same_place = loc.place_id and loc.place_id == inc.location.place_id
    km = 0.0 if same_place else geo.haversine_km(loc.lat, loc.lng, inc.location.lat, inc.location.lng)
    if km > config.MERGE_MAX_KM:
        return 9.0
    q = embeddings.concept_text(msg.raw_text, ex)
    others = [embeddings.concept_text(m.raw_text, m.extraction) for m in members]
    sim = float(max(embeddings.similarities(q, others))) if others else 0.0
    dt = abs((msg.received_at - inc.updated_at).total_seconds()) / 60
    a = {n for n in ex.needs if n != Need.information}
    b = {n for n in inc.needs if n != Need.information}
    mismatch = config.NEED_MISMATCH_PENALTY if (a and b and not (a & b)) else 0.0
    return (0.6 * (1 - sim) + 0.25 * min(km / config.MERGE_MAX_KM, 1)
            + 0.15 * min(dt / config.MERGE_WINDOW_MIN, 1) + mismatch)


def _elsewhere(loc: Location, inc: Incident) -> bool:
    """True when both have a location and they are too far apart to be the same event."""
    if loc.lat is None or inc.location.lat is None:
        return False
    return geo.haversine_km(loc.lat, loc.lng, inc.location.lat, inc.location.lng) > config.MERGE_MAX_KM


def find_incident(store, msg: Message, ex: Extraction, loc: Location) -> tuple[Optional[Incident], str]:
    window = timedelta(minutes=config.MERGE_WINDOW_MIN)
    live = [i for i in store.all("incidents")
            if i.status in (IncidentStatus.open, IncidentStatus.assigned)
            and abs(msg.received_at - i.updated_at) <= window]
    # 1. the sender's next message (follow-up reply, location pin, more detail) joins their own incident
    for inc in sorted(live, key=lambda i: i.updated_at, reverse=True):
        if (msg.sender_hash in inc.sender_hashes
                and (msg.meta.get("location_only") or not _elsewhere(loc, inc))
                and msg.received_at - inc.updated_at <= timedelta(minutes=config.FOLLOWUP_WINDOW_MIN)):
            if msg.meta.get("location_only"):
                return inc, "location pin from the same sender"
            return inc, "reply to follow-up question" if inc.followup_sent else "more detail from the same sender"
    # 2. nearest incident by meaning + place + time
    best, best_d = None, 9.0
    for inc in live:
        members = [m for m in (store.get("messages", mid) for mid in inc.message_ids) if m]
        d = _distance(msg, ex, loc, inc, members)
        if d < best_d:
            best, best_d = inc, d
    if best is not None and best_d < config.MERGE_EPS:
        return best, f"merged (distance {best_d:.2f} < {config.MERGE_EPS})"
    return None, ""


def _merge_fields(inc: Incident, ex: Extraction, loc: Location) -> None:
    if inc.location.lat is None and loc.lat is not None:
        inc.location = loc
    elif not inc.location.text and loc.text:
        inc.location.text = loc.text
    if ex.people_count is not None and (inc.people_count is None or inc.people_estimated
                                        or ex.people_count > inc.people_count):
        if not (ex.people_estimated and inc.people_count and not inc.people_estimated):
            inc.people_count, inc.people_estimated = ex.people_count, ex.people_estimated
    for v in ex.vulnerable:
        if v not in inc.vulnerable:
            inc.vulnerable.append(v)
    for n in ex.needs:
        if n not in inc.needs:
            inc.needs.append(n)
    if len(inc.needs) > 1 and Need.information in inc.needs:
        inc.needs.remove(Need.information)
    if ex.hazard and (inc.hazard is None or inc.hazard == HazardType.water and ex.hazard != HazardType.water):
        inc.hazard = ex.hazard
    if ex.water_depth_ft and (inc.water_depth_ft or 0) < ex.water_depth_ft:
        inc.water_depth_ft = ex.water_depth_ft
    if ex.hazard_detail and (not inc.hazard_detail or "roof" in ex.hazard_detail):
        inc.hazard_detail = ex.hazard_detail
    if inc.water_depth_ft and (not inc.hazard_detail or "roof" not in inc.hazard_detail):
        inc.hazard_detail = f"water ~{inc.water_depth_ft:g} ft"
    if SEVERITY[ex.medical] > SEVERITY[inc.medical]:
        inc.medical = ex.medical


def make_title(inc: Incident) -> str:
    v = set(inc.vulnerable)
    n = inc.people_count
    if n == 1 or (n is None and v & {Vulnerable.elderly, Vulnerable.pregnant}):
        who = ("Elderly person" if Vulnerable.elderly in v else "Pregnant woman" if Vulnerable.pregnant in v
               else "Disabled person" if Vulnerable.disabled in v else "Child" if Vulnerable.child in v
               else "1 person")
        if Vulnerable.alone in v:
            who += " (alone)"
    elif n:
        who = f"{n}{'~' if inc.people_estimated else ''} people" + (" incl. children" if Vulnerable.child in v else "")
    else:
        who = "People"
    needs = [x for x in inc.needs if x != Need.information]
    what = {Need.rescue: "trapped" if inc.hazard else "need rescue", Need.medical: "need medical help",
            Need.food: "need food", Need.water: "need drinking water", Need.medicine: "need medicine",
            Need.shelter: "need shelter", Need.evacuation: "need evacuation",
            Need.welfare_check: "no contact - welfare check"}
    parts = [what[x] for x in needs[:2]] or ["reported"]
    where = inc.location.text or "location unknown"
    return f"{who} {' + '.join(parts)} - {where}"


# ------------------------------------------------------------------ confidence + red flags
def assess(store, inc: Incident, now: datetime) -> None:
    members = sorted([m for m in (store.get("messages", mid) for mid in inc.message_ids) if m],
                     key=lambda m: m.received_at)
    flags: list[str] = []
    reasons: list[str] = []
    conf = config.CONF_NEUTRAL
    reasons.append(f"start neutral {config.CONF_NEUTRAL:.2f} (a lone report is not low)")

    # independent confirmations vs copy-paste
    independent: list[Message] = []
    copies: list[Message] = []
    seen: dict[str, set[str]] = {}            # normalised text -> senders who sent it
    for m in members:
        if not m.raw_text:
            independent.append(m)
            continue
        key = " ".join(m.raw_text.lower().split())
        if seen.get(key, set()) - {m.sender_hash}:
            copies.append(m)                   # exact copy from another number: no embedding needed
        else:
            prev = [t for t, who in seen.items() if who - {m.sender_hash}]
            sims = embeddings.similarities(key, prev) if prev else []
            (copies if len(sims) and max(sims) >= config.COPY_PASTE_SIM else independent).append(m)
        seen.setdefault(key, set()).add(m.sender_hash)
    ind_senders = {m.sender_hash for m in independent}
    copy_senders = {m.sender_hash for m in copies} - ind_senders
    if len(ind_senders) > 1:
        bonus = min(0.30, config.CONF_PER_CONFIRMATION * (len(ind_senders) - 1))
        conf += bonus
        reasons.append(f"+{bonus:.2f}: {len(ind_senders)} independent senders")
    if len(copy_senders) + 1 >= config.COPY_PASTE_MIN_SENDERS:
        span = (copies[-1].received_at - members[0].received_at).total_seconds() if copies else 0
        if span <= config.COPY_PASTE_WINDOW_S or len(copy_senders) >= 5:
            flags.append(f"copy_paste_burst: same text from {len(copy_senders) + 1} numbers within {span:.0f}s")

    # specific details
    if inc.location.place_id and inc.location.in_area:
        conf += config.CONF_DETAIL_BONUS
        reasons.append(f"+{config.CONF_DETAIL_BONUS:.2f}: known landmark inside affected area")
    if inc.people_count and not inc.people_estimated and inc.people_count <= 20:
        conf += config.CONF_DETAIL_BONUS
        reasons.append(f"+{config.CONF_DETAIL_BONUS:.2f}: specific people count")

    # location red flags
    if inc.location.lat is not None and inc.location.in_area is False:
        flags.append("out_of_area: location is outside the affected area")
    if inc.location.lat is None and any(n != Need.information for n in inc.needs):
        reasons.append("location unknown: follow-up question sent (not a red flag)")

    # sender red flags
    senders = {m.sender_hash for m in members}
    low_rep = [s for s in senders if store.reputation(s) < 0.5]
    if low_rep:
        flags.append(f"low_reputation_sender: {len(low_rep)} sender(s) with past false reports")
    for s in senders:
        recent = {m.incident_id for m in store.messages_by_sender(s) if m.incident_id
                  and abs((m.received_at - now).total_seconds()) <= 1800}
        places = {store.get("incidents", i).location.place_id for i in recent if store.get("incidents", i)}
        if len(recent) >= config.SERIAL_REPORTER_INCIDENTS and len(places) >= config.SERIAL_REPORTER_INCIDENTS:
            flags.append("serial_reporter: one number reported many unrelated emergencies in 30 min")
            break

    for f in flags:
        key = f.split(":")[0]
        pen = config.RED_FLAG_PENALTY.get(key, 0.0)
        if pen:
            conf -= pen
            reasons.append(f"-{pen:.2f}: red flag {key}")

    # verification results override
    cb = inc.callback.status
    if cb == "confirmed":
        conf = max(conf, 0.90)
        reasons.append("callback/operator CONFIRMED -> at least 0.90")
    elif cb == "denied":
        conf = min(conf, 0.15)
        reasons.append("callback said help not needed -> at most 0.15")
    elif cb == "no_answer":
        reasons.append("callback not answered: confidence unchanged (silence is not evidence of a fake)")

    inc.confidence = round(max(0.02, min(0.98, conf)), 2)
    inc.red_flags = flags
    inc.confidence_reasons = reasons


def apply_matrix(inc: Incident) -> None:
    """PRD 6.3. Low confidence never stops a critical case; it adds verification."""
    high_p = inc.band in (Band.CRIT, Band.HIGH)
    if inc.status in (IncidentStatus.resolved, IncidentStatus.false):
        inc.action = "closed"
    elif inc.location.lat is None:
        inc.action = "need_location"
    elif inc.confidence < config.CONF_SUSPECT:
        inc.action = "verify_first"           # >=2 red flags: callback + operator before any team
    elif high_p and inc.confidence < config.CONF_HIGH:
        inc.action = "standby_and_verify"
    elif high_p:
        inc.action = "dispatch"
    elif inc.confidence < config.CONF_HIGH:
        inc.action = "callback_review"
    else:
        inc.action = "normal_queue"
    lone = len(set(inc.sender_hashes)) <= 1 and inc.source == "messages"
    needs_callback = inc.action in ("verify_first", "standby_and_verify", "callback_review") or (lone and high_p)
    # an AUTOMATIC callback (by=None) is no longer needed once independent people confirmed it
    if (inc.callback.status == "pending" and inc.callback.by is None and not needs_callback
            and not lone and not inc.red_flags):
        inc.callback.status = "not_needed"
        inc.notes.append(f"automatic callback cancelled: confirmed by {len(set(inc.sender_hashes))} senders")
    if needs_callback and inc.callback.status == "none":
        inc.callback.status = "pending"
        inc.notes.append("automatic callback started" + (" in parallel (lone report)" if lone and high_p else ""))


def followup_for(inc: Incident, language: Optional[str]) -> Optional[str]:
    lang = language if language in FOLLOWUP else "hinglish"
    real_needs = [n for n in inc.needs if n != Need.information]
    if inc.location.lat is None:
        return FOLLOWUP[lang]
    if not real_needs:
        return FOLLOWUP_NEED[lang]
    return None


def _kind(question: Optional[str]) -> Optional[str]:
    """Which field a follow-up asks for, whatever its language."""
    if question is None:
        return None
    return "location" if question in FOLLOWUP.values() else "need"


# ------------------------------------------------------------------ entry point
def verify(store, msg: Message, ex: Extraction, now: datetime) -> tuple[Optional[Incident], str]:
    """Attach msg to an incident (new or existing). Returns (incident or None, explanation).
    Caller must hold store.lock and persist msg afterwards."""
    loc = resolve_location(msg, ex)
    inc, why = find_incident(store, msg, ex, loc)
    if inc is None:
        if not ex.is_help_request:
            if ex.extractor == "rules" and not ex.needs:     # keywords recognised nothing: never drop it silently
                msg.status = MessageStatus.review
                msg.meta["review_reason"] = "keyword rules could not read this message"
                return None, "rules could not read it: human review"
            msg.status = MessageStatus.info
            return None, "not a help request (information / noise)"
        inc = Incident(id=store.next_id("INC"), created_at=msg.received_at, updated_at=msg.received_at,
                       location=loc, source=msg.meta.get("source", "messages"))
        why = "new incident"
    _merge_fields(inc, ex, loc)
    inc.message_ids.append(msg.id)
    if msg.sender_hash not in inc.sender_hashes:
        inc.sender_hashes.append(msg.sender_hash)
    inc.updated_at = max(inc.updated_at, msg.received_at)
    msg.incident_id = inc.id
    msg.status = MessageStatus.merged
    store.put("messages", msg)          # members must be readable by assess()
    priority.score(inc, now)
    assess(store, inc, now)
    apply_matrix(inc)
    inc.title = make_title(inc)
    q = followup_for(inc, ex.language)
    if q and _kind(q) != _kind(inc.followup_question):   # each missing field is asked once, not every message
        inc.followup_sent = True
        inc.followup_question = q
        msg.meta["followup_sent"] = True                  # service then skips its status reply
        store.send(msg.sender_hash, msg.channel.value, q, inc.id)
    return inc, why


def refresh(store, inc: Incident, now: datetime) -> Incident:
    """Re-score an existing incident (waiting time grew, callback came back, reputation changed)."""
    priority.score(inc, now)
    assess(store, inc, now)
    apply_matrix(inc)
    inc.title = make_title(inc)
    return inc
