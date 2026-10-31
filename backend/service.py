"""
RAHAT core loop (integration lead's file):

  ingest -> store FIRST -> agents (LangGraph) -> Surge check -> optimizer re-solve
  -> operator approves -> team status -> re-solve ...

Every recommendation, approval, override and status change is written to the
audit log (FR18). Nothing critical goes to a team without an operator action (FR10).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional

from agents import geo, llm, pipeline, priority, verifier
from backend import optimizer
from backend.db import Store
from rag import sop
from shared import config
from shared.schemas import (ApproveIn, Assignment, AssignmentStatus, Band, Channel, Extraction, Incident,
                            IncidentStatus, Message, MessageIn, MessageStatus, Need, Team, TeamStatus,
                            TeamStatusIn, VerifyIn, Vulnerable, VulnerableResident, now_utc)
from simulation import surge

log = logging.getLogger("rahat.service")
ACTIVE = (AssignmentStatus.approved, AssignmentStatus.en_route, AssignmentStatus.on_scene)
# channels where a real person wrote to us and can read a reply (not IVR / proactive / helpline transcripts)
CITIZEN_CHANNELS = {"sms", "whatsapp", "web", "voice", "volunteer"}
ACK = ("RAHAT: Aapka message control room tak pahunch gaya hai (ref {ref}). "
       "Agar jaan ka khatra hai toh 112 pe bhi call karein.")

# Replies to citizens: one per message, so the chat never goes silent. Fixed templates only;
# the LLM never writes text that a person in danger reads, and nothing promises a team before approval.
REPLY_NEW = "RAHAT: Case {id} bana diya gaya hai. Control room ise dekh raha hai."   # the ack already said 112
REPLY_ADDED = "RAHAT: Nayi jaankari case {id} mein jod di gayi hai. Control room ise dekh raha hai."
REPLY_KNOWN = ("RAHAT: Is jagah ka case pehle se control room mein hai ({id}). "
               "Aapki jaankari usi mein jod di gayi hai, alag se dobara bhejne ki zaroorat nahi.")
REPLY_NEED_PLACE = "Team bhejne ke liye aapki jagah chahiye: gali, mohalla ya paas ka landmark."
REPLY_EN_ROUTE ="RAHAT: {team} aapki taraf aa rahi hai. {eta} Ho sake toh oonchi jagah pe rahein."
REPLY_INFO = ("RAHAT: Is message mein madad ki request nahi mili. Agar madad chahiye toh "
              "jagah (gali / landmark) aur kitne log hain, yeh likh kar bhejein.")
REPLY_REVIEW = "RAHAT: Aapka message mil gaya hai. Control room ka ek vyakti ise khud dekhega."
UPDATE_DISPATCHED = "RAHAT: {team} aapki taraf bheji gayi hai. Andaazan {eta} min. Ho sake toh oonchi jagah pe rahein."
UPDATE_REACHED = "RAHAT: {team} aapki jagah pahunch gayi hai."


def hash_sender(raw: str) -> str:
    return hashlib.sha256((config.SENDER_SALT + raw.strip()).encode()).hexdigest()[:16]


def _aware(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return now_utc()
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Rahat:
    def __init__(self, db_path: Optional[str] = None, use_llm: Optional[bool] = None, workers: int = 6,
                 clock: Callable[[], datetime] = now_utc):
        self.store = Store(db_path or config.DB_PATH)     # resolved at call time, not import time
        self.use_llm = use_llm
        self.clock = clock
        self.graph = pipeline.build(self.store, clock)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rahat")
        self.listeners: list[Callable[[dict], None]] = []
        self.timings: list[float] = []
        self._seed()

    # ================================================================== setup
    def _seed(self, force: bool = False) -> None:
        if self.store.count("teams") and not force:
            return
        for t in json.loads((config.DATA_DIR / "teams.json").read_text(encoding="utf-8")):
            self.store.put("teams", Team(**t, last_seen=now_utc()))
        for r in json.loads((config.DATA_DIR / "vulnerable_residents.json").read_text(encoding="utf-8")):
            phone = r.pop("phone")
            self.store.put("residents", VulnerableResident(**r, phone_hash=hash_sender(phone)))
        self.store.audit("system", "seeded", "teams+residents")

    def reset(self) -> None:
        with self.store.lock:
            self.store.reset()
            self._seed(force=True)
        self.emit("reset")

    # ================================================================== events
    def emit(self, kind: str, **data) -> None:
        for fn in list(self.listeners):
            try:
                fn({"type": kind, **data})
            except Exception:
                log.exception("listener failed")

    # ================================================================== ingestion (FR01)
    def ingest(self, body: MessageIn, wait: bool = False) -> Message:
        sender = body.sender_hash or hash_sender(body.sender_id or f"anon-{now_utc().timestamp()}")
        with self.store.lock:
            msg = Message(id=self.store.next_id("MSG", 4), channel=body.channel,
                          received_at=_aware(body.received_at) if body.received_at else self.clock(),
                          sender_hash=sender, raw_text=body.raw_text.strip(), audio_url=body.audio_url,
                          language=body.language, lat=body.lat, lng=body.lng, meta=dict(body.meta))
            self.store.put("messages", msg)                     # stored BEFORE any processing
        self.emit("message", id=msg.id)
        fut = self.pool.submit(self._process, msg.id)
        if wait:
            fut.result()
            return self.store.get("messages", msg.id)
        return msg

    def _process(self, msg_id: str) -> None:
        t0 = now_utc()
        msg = self.store.get("messages", msg_id)
        # instant ack on first contact (the LLM can take seconds) and before any follow-up question;
        # later messages get _reply only, so the chat does not repeat itself
        ack = msg.meta.get("ack_template") or (ACK if msg.channel.value in CITIZEN_CHANNELS else None)
        if ack and not self.store.outbox(msg.sender_hash, 1):
            self.store.send(msg.sender_hash, msg.channel.value, ack.format(ref=msg.id))
        try:
            state = self.graph.invoke({"msg": msg, "trace": [], "use_llm": self.use_llm,
                                       "now": self.clock(), "surge_hazard": surge.get(self.store)["hazard"]})
        except Exception as e:                                    # never lose a message: send to review
            log.exception("pipeline failed for %s", msg_id)
            with self.store.lock:
                msg = self.store.get("messages", msg_id)
                msg.status = MessageStatus.review
                msg.meta["review_reason"] = f"processing failed: {type(e).__name__}"
                self.store.put("messages", msg)
            self._reply(msg_id, None)
            self.emit("message", id=msg_id)
            return
        inc = state.get("incident")
        with self.store.lock:
            m = self.store.get("messages", msg_id)
            m.meta["trace"] = state.get("trace", [])
            self.store.put("messages", m)
            if inc is not None:
                self.store.audit("agents", "message_attached", inc.id, None,
                                 {"message": msg_id, "why": state.get("why"), "band": inc.band.value,
                                  "confidence": inc.confidence})
        surge.evaluate(self.store)
        self.resolve("optimizer")
        self._reply(msg_id, inc)
        self.timings.append((now_utc() - t0).total_seconds())
        self.emit("incident", id=inc.id if inc else None, message=msg_id)

    def _reply(self, msg_id: str, inc: Optional[Incident]) -> None:
        """Answer every citizen message once: a follow-up question (sent by the verifier) or the case status."""
        with self.store.lock:
            msg = self.store.get("messages", msg_id)
            if msg.channel.value not in CITIZEN_CHANNELS or msg.meta.get("followup_sent"):
                return
            if inc is None:
                text = REPLY_REVIEW if msg.status == MessageStatus.review else REPLY_INFO
            else:
                inc = self.store.get("incidents", inc.id)
                act = [a for a in self.store.all("assignments") if a.incident_id == inc.id and a.status in ACTIVE]
                there = [a for a in act if a.status == AssignmentStatus.on_scene]
                if there:
                    text = UPDATE_REACHED.format(team=self.store.get("teams", there[0].team_id).name)
                elif act:
                    a = min(act, key=lambda a: a.eta_min)
                    left = a.eta_min - (self.clock() - a.approved_at).total_seconds() / 60
                    eta = f"Andaazan {round(left)} min aur." if left >= 1 else "Kuch hi der mein pahunchegi."
                    text = REPLY_EN_ROUTE.format(team=self.store.get("teams", a.team_id).name, eta=eta)
                else:
                    earlier = [m for m in (self.store.get("messages", i) for i in inc.message_ids if i != msg_id)
                               if m and m.sender_hash == msg.sender_hash]
                    if earlier:
                        text = REPLY_ADDED.format(id=inc.id)
                    elif len(inc.sender_hashes) > 1:      # someone else already reported this case
                        text = REPLY_KNOWN.format(id=inc.id)
                    else:
                        text = REPLY_NEW.format(id=inc.id)
                    if inc.location.lat is None:          # asked once already; keep reminding, don't re-ask
                        text += " " + REPLY_NEED_PLACE
            self.store.send(msg.sender_hash, msg.channel.value, text, inc.id if inc else None)

    def _tell_reporters(self, inc: Incident, text: str) -> None:
        if inc.source == "messages":                   # close the loop with the people who asked
            for sh in inc.sender_hashes:
                self.store.send(sh, "update", text, inc.id)

    # ================================================================== optimizer (FR09)
    def _filled(self) -> Counter:
        c: Counter = Counter()
        for a in self.store.all("assignments"):
            if a.status in ACTIVE:
                c[(a.incident_id, a.team_type_slot.value)] += 1
        return c

    def resolve(self, actor: str = "optimizer") -> list[Assignment]:
        """Re-solve all free teams x all open slots. Replaces previous un-approved recommendations."""
        with self.store.lock:
            t0 = now_utc()
            incidents = self.store.all("incidents")
            teams = self.store.all("teams")
            banned = {tuple(x) for x in self.store.kv_get("banned_pairs", [])}
            slots = optimizer.open_slots(incidents, self._filled())
            pairs = optimizer.solve(teams, slots, banned)
            old = {(a.incident_id, a.team_id, a.team_type_slot.value): a for a in self.store.all("assignments")
                   if a.status == AssignmentStatus.recommended}
            keep = set()
            out = []
            for p in pairs:
                key = (p.slot.incident.id, p.team.id, p.slot.team_type.value)
                a = old.get(key)
                if a is None:
                    a = Assignment(id=self.store.next_id("ASG"), incident_id=key[0], team_id=key[1],
                                   team_type_slot=p.slot.team_type, cost=p.cost, cost_breakdown=p.breakdown,
                                   eta_min=p.eta_min, recommended_at=now_utc(), reason=p.reason)
                    self.store.audit(actor, "recommended", a.id, None,
                                     {"incident": a.incident_id, "team": a.team_id, "eta_min": a.eta_min, "cost": a.cost})
                else:
                    a.cost, a.cost_breakdown, a.eta_min, a.reason = p.cost, p.breakdown, p.eta_min, p.reason
                keep.add(key)
                self.store.put("assignments", a)
                out.append(a)
            for key, a in old.items():
                if key not in keep:
                    self.store.delete("assignments", a.id)
                    self.store.audit(actor, "recommendation_withdrawn", a.id,
                                     {"incident": a.incident_id, "team": a.team_id}, None)
            self.store.kv_set("last_solve_ms", round((now_utc() - t0).total_seconds() * 1000, 1))
        self.emit("assignment")
        return out

    # ================================================================== operator (FR10)
    def decide(self, assignment_id: str, body: ApproveIn) -> Assignment:
        with self.store.lock:
            a = self.store.get("assignments", assignment_id)
            if a is None:
                raise KeyError(assignment_id)
            if a.status != AssignmentStatus.recommended:
                raise ValueError(f"assignment is {a.status.value}, not recommended")
            inc = self.store.get("incidents", a.incident_id)
            before = a.model_dump(mode="json")
            if body.action == "reject":
                a.status = AssignmentStatus.rejected
                banned = self.store.kv_get("banned_pairs", [])
                banned.append([a.incident_id, a.team_id])
                self.store.kv_set("banned_pairs", banned)
                self.store.put("assignments", a)
                self.store.audit(body.operator, "rejected", a.id, before, a.model_dump(mode="json"))
            else:
                if body.action == "change":
                    team = self.store.get("teams", body.team_id or "")
                    if team is None or team.status != TeamStatus.free:
                        raise ValueError("chosen team is not free")
                    pc = optimizer.pair_cost(team, optimizer.Slot(inc, a.team_type_slot))
                    eta = pc.eta_min if pc else round(geo.travel_minutes(
                        team.lat, team.lng, inc.location.lat, inc.location.lng, optimizer.speed(team)), 1)
                    self.store.delete("assignments", a.id)
                    a = Assignment(id=self.store.next_id("ASG"), incident_id=inc.id, team_id=team.id,
                                   team_type_slot=a.team_type_slot, cost=pc.cost if pc else 0.0,
                                   cost_breakdown=pc.breakdown if pc else {}, eta_min=eta,
                                   recommended_at=now_utc(),
                                   reason=(pc.reason if pc else f"operator override: {team.name} (outside optimizer filters)"))
                    self.store.audit(body.operator, "override", a.id, before,
                                     {"team": team.id, "eta_min": eta})
                a.status = AssignmentStatus.approved
                a.approved_by, a.approved_at = body.operator, now_utc()
                team = self.store.get("teams", a.team_id)
                team.status = TeamStatus.busy
                inc.status = IncidentStatus.assigned
                self.store.put("teams", team)
                self.store.put("incidents", inc)
                self.store.put("assignments", a)
                self.store.audit(body.operator, "approved", a.id, before, a.model_dump(mode="json"))
                self._tell_reporters(inc, UPDATE_DISPATCHED.format(team=team.name, eta=round(a.eta_min)))
        self.resolve("optimizer")
        self.emit("assignment", id=a.id)
        return a

    # ================================================================== field team (FR12)
    def _field_update(self, status: str, team_name: str, a: Optional[Assignment], inc: Incident) -> Optional[str]:
        """Message to the people who reported the case when the field team taps a status."""
        if a is None:
            return None
        if status == "en_route":
            return (f"RAHAT: {team_name} nikal chuki hai, raaste mein hai. Andaazan {round(a.eta_min)} min. "
                    "Phone ki battery bachayein, oonchi jagah pe rahein.")
        if status == "reached":
            return (f"RAHAT: {team_name} aapki batayi jagah pe pahunch gayi hai. Awaaz dein, torch ya "
                    "kapda hilayein taaki team aapko dekh sake.")
        if status == "rescued":
            still = inc.status != IncidentStatus.resolved
            return (f"RAHAT: {team_name} ne apna kaam poora kar diya. "
                    + ("Baaki team abhi bhi aa rahi hai." if still else
                       "Aapka case band kiya ja raha hai. Aur madad chahiye toh isi number pe likhein."))
        if status == "no_one_here":
            return (f"RAHAT: {team_name} aapki batayi jagah pe pahunchi lekin wahan koi nahi mila. Agar aap abhi "
                    "bhi phase hain toh turant sahi jagah aur paas ka landmark likhein.")
        if status == "need_backup":
            return "RAHAT: Team ne aur madad maangi hai, ek aur team bheji ja rahi hai."
        return None

    def current_assignment(self, team_id: str) -> Optional[Assignment]:
        act = [a for a in self.store.all("assignments") if a.team_id == team_id and a.status in ACTIVE]
        return max(act, key=lambda a: a.approved_at or a.recommended_at) if act else None

    def team_status(self, team_id: str, body: TeamStatusIn) -> dict:
        with self.store.lock:
            team = self.store.get("teams", team_id)
            if team is None:
                raise KeyError(team_id)
            if body.lat is not None and body.lng is not None:
                team.lat, team.lng = body.lat, body.lng
            team.last_seen = now_utc()
            if body.status == "location":                 # GPS ping from the PWA: move the marker only
                self.store.put("teams", team)
                self.emit("team", id=team_id)
                return {"team": team, "assignment": None, "incident": None}
            a = self.current_assignment(team_id)
            # a late tap (phone was offline, or the operator already entered it by radio) for a job that is
            # no longer this team's current job must NOT touch the new job
            if body.assignment_id and (a is None or a.id != body.assignment_id):
                self.store.put("teams", team)
                self.store.audit(body.by or team_id, "stale_status_ignored", body.assignment_id, None,
                                 {"status": body.status, "current_assignment": a.id if a else None,
                                  "client_ts": body.client_ts.isoformat() if body.client_ts else None})
                self.emit("team", id=team_id)
                return {"team": team, "assignment": a, "incident": None, "ignored": "stale assignment"}
            inc = self.store.get("incidents", a.incident_id) if a else None
            before = {"team": team.status.value, "assignment": a.status.value if a else None}
            s = body.status
            if s == "en_route" and a:
                a.status = AssignmentStatus.en_route
            elif s == "reached" and a:
                a.status = AssignmentStatus.on_scene
                inc.notes.append(f"{team.name} reached")
            elif s in ("rescued", "no_one_here") and a:
                a.status = AssignmentStatus.done
                team.status = TeamStatus.free
                if inc.location.lat is not None:
                    team.lat, team.lng = inc.location.lat, inc.location.lng
                others = [x for x in self.store.all("assignments")
                          if x.incident_id == inc.id and x.id != a.id and x.status in ACTIVE]
                if s == "rescued":
                    inc.notes.append(f"{team.name}: rescued / delivered")
                    if not others:
                        inc.status = IncidentStatus.resolved
                else:
                    inc.status = IncidentStatus.false
                    inc.notes.append(f"{team.name}: no one here -> report marked false")
                    for o in others:                      # recall the rest of the teams
                        o.status = AssignmentStatus.done
                        t2 = self.store.get("teams", o.team_id)
                        t2.status = TeamStatus.free
                        self.store.put("assignments", o)
                        self.store.put("teams", t2)
                    if inc.source == "messages":
                        for sh in inc.sender_hashes:
                            self.store.set_reputation(sh, self.store.reputation(sh) - 0.3)
            elif s == "need_backup" and a:
                inc.required_team_types.append(a.team_type_slot)
                inc.notes.append(f"{team.name} asked for backup ({a.team_type_slot.value})")
            elif s == "free":
                team.status = TeamStatus.free
            if inc:
                verifier.refresh(self.store, inc, self.clock())
                self.store.put("incidents", inc)
                text = self._field_update(s, team.name, a, inc)
                if text:
                    self._tell_reporters(inc, text)
            if a:
                self.store.put("assignments", a)
            self.store.put("teams", team)
            if body.by and inc is not None:
                inc.notes.append(f"{body.by} entered '{s}' for {team.name}" + (f" (via {body.via})" if body.via else ""))
                self.store.put("incidents", inc)
            self.store.audit(team_id, f"status_{s}", a.id if a else team_id, before,
                             {"team": team.status.value, "assignment": a.status.value if a else None,
                              "client_ts": body.client_ts.isoformat() if body.client_ts else None,
                              "by": body.by, "via": body.via})
        self.resolve("optimizer")
        self.emit("team", id=team_id)
        return {"team": team, "assignment": a, "incident": inc}

    # ================================================================== verification (FR13)
    def verify(self, incident_id: str, body: VerifyIn) -> Incident:
        with self.store.lock:
            inc = self.store.get("incidents", incident_id)
            if inc is None:
                raise KeyError(incident_id)
            before = {"confidence": inc.confidence, "callback": inc.callback.status}
            inc.callback.status, inc.callback.at, inc.callback.by = body.result, now_utc(), body.by
            if body.result == "denied":
                inc.notes.append(f"{body.by}: help not needed")
            verifier.refresh(self.store, inc, self.clock())
            if body.result == "denied" and inc.status == IncidentStatus.open:
                inc.status = IncidentStatus.resolved
            self.store.put("incidents", inc)
            self.store.audit(body.by, f"verify_{body.result}", inc.id, before,
                             {"confidence": inc.confidence, "action": inc.action})
        self.resolve("optimizer")
        self.emit("incident", id=incident_id)
        return inc

    # ================================================================== proactive check (FR15)
    def start_proactive_checks(self, actor: str = "system") -> list[VulnerableResident]:
        with self.store.lock:
            res = self.store.all("residents")
            for r in res:
                r.last_check_at, r.last_check_result = now_utc(), "calling"
                self.store.put("residents", r)
            s = surge.get(self.store)
            s["proactive_started"] = True
            self.store.kv_set("surge", s)
            self.store.audit(actor, "proactive_checks_started", "residents", None, {"count": len(res)})
        self.emit("residents")
        return res

    def proactive_result(self, resident_id: str, result: str) -> Optional[Incident]:
        """result: ok | need_help | no_answer. A missing answer becomes a high-priority incident."""
        with self.store.lock:
            r = self.store.get("residents", resident_id)
            if r is None:
                raise KeyError(resident_id)
            r.last_check_at, r.last_check_result = now_utc(), result
            self.store.put("residents", r)
            self.store.audit("ivr", f"proactive_{result}", r.id)
        self.emit("residents")
        if result == "ok":
            return None
        vul = [Vulnerable.elderly] if (r.age or 0) >= 60 else []
        if any("wheelchair" in n or "disab" in n for n in r.needs):
            vul.append(Vulnerable.disabled)
        if r.lives_alone:
            vul.append(Vulnerable.alone)
        place = geo.place_by_id(r.place_id)
        ex = Extraction(location_text=r.area, place_id=r.place_id, people_count=1, vulnerable=vul,
                        needs=[Need.welfare_check] if result == "no_answer" else [Need.rescue],
                        hazard=("water" if place and place.flooded else None),
                        extractor="proactive_registry", language="hinglish")
        text = (f"[Proactive check] Registered resident {r.name}, {r.age}, {'lives alone, ' if r.lives_alone else ''}"
                f"{r.area}: {'did NOT answer the call' if result == 'no_answer' else 'pressed 1: needs help'}")
        msg = self.ingest(MessageIn(channel=Channel.proactive, raw_text=text, sender_hash=r.phone_hash,
                                    lat=r.lat, lng=r.lng, meta={"preset_extraction": ex.model_dump(mode="json"),
                                                                "source": "proactive_check", "resident": r.id}),
                          wait=True)
        return self.store.get("incidents", msg.incident_id) if msg.incident_id else None

    # ================================================================== missed call + IVR (no typing)
    def missed_call(self, sender_id: str) -> Message:
        msg = self.ingest(MessageIn(channel=Channel.missed_call, sender_id=sender_id, raw_text="",
                                    meta={"review_reason": "missed call: IVR callback placed"}), wait=True)
        self.store.send(msg.sender_hash, "ivr",
                        "RAHAT IVR (callback): Press 1 for rescue, 2 for medical help, 3 for food or water.")
        return msg

    def ivr_answer(self, sender_id: str, digit: str, place_id: Optional[str]) -> Message:
        need = {"1": Need.rescue, "2": Need.medical, "3": Need.food}.get(digit, Need.rescue)
        place = geo.place_by_id(place_id)
        ex = Extraction(location_text=place.name if place else None, place_id=place_id, needs=[need],
                        medical=("serious" if need == Need.medical else "none"),
                        hazard=("water" if place and place.flooded and need == Need.rescue else None),
                        extractor="ivr_keypad")
        return self.ingest(MessageIn(channel=Channel.ivr, sender_id=sender_id, raw_text=f"[IVR] pressed {digit}",
                                     meta={"preset_extraction": ex.model_dump(mode="json"), "source": "ivr"}),
                           wait=True)

    # ================================================================== periodic
    def tick(self) -> None:
        """Waiting time grows every minute (PRD 6.1); re-solve when any band changes."""
        changed = False
        with self.store.lock:
            for inc in self.store.all("incidents"):
                if inc.status != IncidentStatus.open:
                    continue
                band = inc.band
                priority.score(inc, self.clock())
                verifier.apply_matrix(inc)
                self.store.put("incidents", inc)
                changed |= inc.band != band
        if changed:
            self.resolve("optimizer(waiting time)")
        self.emit("tick")

    # ================================================================== read models
    def checklist(self, incident_id: str) -> dict:
        inc = self.store.get("incidents", incident_id)
        if inc is None:
            raise KeyError(incident_id)
        return sop.checklist(inc)

    def compare(self) -> dict:
        """Pitch moment: the same incidents, all teams free at their base, greedy vs RAHAT."""
        base = [Team(**t) for t in json.loads((config.DATA_DIR / "teams.json").read_text(encoding="utf-8"))]
        incs = [i for i in self.store.all("incidents") if i.status in (IncidentStatus.open, IncidentStatus.assigned)]
        slots = optimizer.open_slots(incs, Counter())
        return {"greedy": optimizer.summary(optimizer.greedy(base, slots), slots),
                "rahat": optimizer.summary(optimizer.solve(base, slots), slots),
                "cost_mode": config.COST_MODE, "lambda": config.LAMBDA}

    def state(self) -> dict:
        band_rank = {Band.CRIT: 0, Band.HIGH: 1, Band.MED: 2, Band.LOW: 3}
        status_rank = {IncidentStatus.open: 0, IncidentStatus.assigned: 1, IncidentStatus.resolved: 2,
                       IncidentStatus.false: 3}
        incidents = sorted(self.store.all("incidents"),
                           key=lambda i: (status_rank[i.status], band_rank[i.band], -i.priority_score))
        assignments = [a for a in self.store.all("assignments") if a.status != AssignmentStatus.rejected]
        msgs = self.store.all("messages")
        active_inc = {a.incident_id for a in assignments if a.status in ACTIVE}
        open_ = [i for i in incidents if i.status == IncidentStatus.open]
        return {
            "district": config.AREA_NAME,
            "area": {"center": config.AREA_CENTER, "radius_km": config.AREA_RADIUS_KM},
            "surge": surge.get(self.store),
            "incidents": [i.model_dump(mode="json") for i in incidents],
            "teams": [t.model_dump(mode="json") for t in self.store.all("teams")],
            "assignments": [a.model_dump(mode="json") for a in assignments],
            "residents": [r.model_dump(mode="json", exclude={"phone_hash"}) for r in self.store.all("residents")],
            "review": [m.model_dump(mode="json") for m in msgs if m.status == MessageStatus.review],
            # cases with at least one message from a REAL phone (WhatsApp Cloud API webhook)
            "live_incidents": sorted({m.incident_id for m in msgs
                                      if m.incident_id and m.meta.get("source_system") == "whatsapp_cloud_api"}),
            "counts": {
                "open": len(open_),
                "assigned": sum(1 for i in incidents if i.status == IncidentStatus.assigned or i.id in active_inc),
                "waiting": sum(1 for i in open_ if i.id not in active_inc),
                "resolved": sum(1 for i in incidents if i.status == IncidentStatus.resolved),
                "false": sum(1 for i in incidents if i.status == IncidentStatus.false),
                "messages": len(msgs),
                "info_messages": sum(1 for m in msgs if m.status == MessageStatus.info),
                "review_messages": sum(1 for m in msgs if m.status == MessageStatus.review),
            },
            "metrics": {
                "last_solve_ms": self.store.kv_get("last_solve_ms"),
                "avg_pipeline_s": round(sum(self.timings) / len(self.timings), 2) if self.timings else None,
                "extractor": llm.status() if (self.use_llm if self.use_llm is not None else llm.enabled()) else "rules",
            },
            "config": {"weights": config.PRIORITY_WEIGHTS, "bands": config.BANDS, "lambda": config.LAMBDA,
                       "cost_mode": config.COST_MODE, "conf_neutral": config.CONF_NEUTRAL,
                       "conf_high": config.CONF_HIGH, "conf_suspect": config.CONF_SUSPECT},
            "places": [{"id": p.place_id, "name": p.name, "lat": p.lat, "lng": p.lng, "in_area": p.in_area,
                        "flooded": p.flooded} for p in geo.places()],
        }

    def incident_detail(self, incident_id: str) -> dict:
        inc = self.store.get("incidents", incident_id)
        if inc is None:
            raise KeyError(incident_id)
        msgs = [m for m in (self.store.get("messages", x) for x in inc.message_ids) if m]
        asg = [a for a in self.store.all("assignments") if a.incident_id == incident_id]
        return {"incident": inc.model_dump(mode="json"),
                "messages": [m.model_dump(mode="json") for m in msgs],
                "assignments": [a.model_dump(mode="json") for a in asg],
                "audit": [x.model_dump(mode="json") for x in self.store.audit_list(50, incident_id)]}
