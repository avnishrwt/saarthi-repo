"""
FastAPI app. Run from the repo root:

    .venv\\Scripts\\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

PRD section 9 endpoints + /state (one snapshot for the dashboard) + /sim/* (demo-only
simulated feed: replay, missed call, IVR, proactive check). In real deployment only
the SOURCE of POST /messages changes (112 feed, SMS gateway); nothing else.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fastapi import Request
from fastapi.responses import PlainTextResponse

from backend import whatsapp
from backend.service import Rahat
from shared import config
from shared.schemas import (ApproveIn, Channel, IncidentStatus, MessageIn, SurgeIn, TeamStatusIn, VerifyIn)
from simulation import surge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("rahat.api")

rahat = Rahat()
rahat.store.on_send = whatsapp.relay
clients: set[WebSocket] = set()
UPLOADS = config.ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)


async def _broadcaster(queue: asyncio.Queue):
    """Debounced push: the dashboard refetches /state on any event."""
    while True:
        ev = await queue.get()
        await asyncio.sleep(0.25)
        kinds = {ev["type"]}
        while not queue.empty():
            kinds.add(queue.get_nowait()["type"])
        dead = []
        for ws in list(clients):
            try:
                await ws.send_text(json.dumps({"type": "changed", "what": sorted(kinds)}))
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)


async def _ticker():
    while True:
        await asyncio.sleep(20)
        try:
            await asyncio.to_thread(rahat.tick)
            await asyncio.to_thread(_maybe_start_proactive)
        except Exception:
            log.exception("tick failed")


def _maybe_start_proactive():
    s = surge.get(rahat.store)
    if s["mode"] in ("standby", "on") and not s.get("proactive_started"):
        rahat.start_proactive_checks("surge_mode")


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    rahat.listeners.append(lambda ev: loop.call_soon_threadsafe(queue.put_nowait, ev))
    from rag import sop
    sop.warm_up()                       # official SOP PDFs loaded in the background, first checklist is instant
    tasks = [asyncio.create_task(_broadcaster(queue)), asyncio.create_task(_ticker())]
    log.info("RAHAT up. extractor=%s cost_mode=%s", rahat.state()["metrics"]["extractor"], config.COST_MODE)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="RAHAT", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _404(e: KeyError):
    raise HTTPException(404, f"not found: {e}")


# ============================================================ ingestion
@app.post("/messages", status_code=202)
def post_message(body: MessageIn):
    m = rahat.ingest(body)
    return {"id": m.id, "status": m.status, "sender_hash": m.sender_hash}


@app.get("/messages/{message_id}/audio")
def get_audio(message_id: str):
    """Original voice note, so the operator can listen and check the transcript.
    Only files inside uploads/ are served. NOTE: there is no login yet; add one before a public deploy."""
    from fastapi.responses import FileResponse
    m = rahat.store.get("messages", message_id)
    if m is None or not m.audio_url:
        raise HTTPException(404, "no voice note for this message")
    p = Path(m.audio_url).resolve()
    if UPLOADS.resolve() not in p.parents or not p.exists():
        raise HTTPException(404, "audio file not available")
    return FileResponse(p)


@app.post("/messages/voice", status_code=202)
async def post_voice(file: UploadFile = File(...), sender_id: str = Form(...), channel: str = Form("voice")):
    suffix = Path(file.filename or "note.webm").suffix or ".webm"
    path = UPLOADS / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(await file.read())
    m = rahat.ingest(MessageIn(channel=Channel(channel), sender_id=sender_id, audio_url=str(path)))
    return {"id": m.id, "audio_url": str(path)}


# ============================================================ WhatsApp Cloud API (real messages)
@app.get("/webhooks/whatsapp")
def wa_verify(request: Request):
    """Meta's one-time check when you save the webhook URL in the developer dashboard."""
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and whatsapp.VERIFY_TOKEN and q.get("hub.verify_token") == whatsapp.VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "verify token mismatch")


@app.post("/webhooks/whatsapp")
async def wa_inbound(request: Request):
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "bad signature")
    items = whatsapp.parse(json.loads(raw or b"{}"))
    for it in items:
        if not it["wa_id"]:
            continue
        audio = await asyncio.to_thread(whatsapp.download_media, it["media_id"]) if it["media_id"] else None
        body = MessageIn(channel=Channel.voice if it["media_id"] else Channel.whatsapp, sender_id=it["wa_id"],
                         raw_text=it["text"], audio_url=audio, lat=it["lat"], lng=it["lng"],
                         meta={"source_system": "whatsapp_cloud_api", "wa_message_id": it["id"],
                               "location_only": it["type"] == "location", "ack_template": whatsapp.ACK})
        if it["media_id"] and not audio:
            body.meta["review_reason"] = ("voice note received but could not be downloaded from WhatsApp: "
                                          + whatsapp.last_media_error)
        from backend.service import hash_sender
        whatsapp.remember(hash_sender(it["wa_id"]), it["wa_id"])
        await asyncio.to_thread(rahat.ingest, body)      # ack is sent first thing in processing
    return {"received": len(items)}      # Meta only needs a fast 200


@app.get("/webhooks/whatsapp/status")
def wa_status():
    return {"configured": whatsapp.configured(), "verify_token_set": bool(whatsapp.VERIFY_TOKEN),
            "signature_check": bool(whatsapp.APP_SECRET), "api_version": whatsapp.API_VERSION,
            "senders_in_memory": len(whatsapp._reply_to)}


# ============================================================ incidents
@app.get("/incidents")
def list_incidents(status: Optional[str] = None):
    inc = rahat.state()["incidents"]
    return [i for i in inc if status is None or i["status"] == status]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    try:
        return rahat.incident_detail(incident_id)
    except KeyError as e:
        _404(e)


@app.post("/incidents/{incident_id}/verify")
def verify_incident(incident_id: str, body: VerifyIn):
    try:
        return rahat.verify(incident_id, body)
    except KeyError as e:
        _404(e)


@app.get("/incidents/{incident_id}/checklist")
def get_checklist(incident_id: str):
    try:
        return rahat.checklist(incident_id)
    except KeyError as e:
        _404(e)


# ============================================================ optimizer + operator
@app.post("/optimize")
def optimize():
    t0 = time.perf_counter()
    out = rahat.resolve("operator(optimize)")
    return {"recommended": out, "solve_ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.get("/optimize/compare")
def compare():
    return rahat.compare()


@app.post("/assignments/{assignment_id}/approve")
def approve(assignment_id: str, body: ApproveIn):
    try:
        return rahat.decide(assignment_id, body)
    except KeyError as e:
        _404(e)
    except ValueError as e:
        raise HTTPException(409, str(e))


# ============================================================ teams
@app.get("/teams")
def teams():
    return rahat.store.all("teams")


@app.get("/teams/{team_id}/assignment")
def team_assignment(team_id: str):
    team = rahat.store.get("teams", team_id)
    if team is None:
        raise HTTPException(404, team_id)
    a = rahat.current_assignment(team_id)
    if a is None:
        return {"team": team, "assignment": None}
    inc = rahat.store.get("incidents", a.incident_id)
    return {"team": team, "assignment": a, "incident": inc, "checklist": rahat.checklist(inc.id)}


@app.patch("/teams/{team_id}/status")
def team_status(team_id: str, body: TeamStatusIn):
    try:
        return rahat.team_status(team_id, body)
    except KeyError as e:
        _404(e)


# ============================================================ surge
@app.post("/surge")
def set_surge(body: SurgeIn):
    s = surge.manual(rahat.store, body.on, body.actor)
    _maybe_start_proactive()
    rahat.emit("surge")
    return s


@app.post("/alerts")
def post_alert(alert: dict):
    s = surge.receive_alert(rahat.store, alert)
    _maybe_start_proactive()
    rahat.emit("surge")
    return s


# ============================================================ read models
@app.get("/state")
def state():
    return rahat.state()


@app.get("/audit")
def audit(limit: int = 200, entity: Optional[str] = None):
    return rahat.store.audit_list(limit, entity)


@app.websocket("/live")
async def live(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()      # keep-alive pings from the client
    except WebSocketDisconnect:
        clients.discard(ws)


# ============================================================ simulation (demo only)
class ReplayIn(BaseModel):
    speed: float = 4.0                   # 4 = four times faster than the scenario timings
    scenario: str = "demo_scenario.json"


class IvrIn(BaseModel):
    sender_id: str
    digit: str = "1"
    place_id: Optional[str] = None


class SenderIn(BaseModel):
    sender_id: str


class ProactiveIn(BaseModel):
    result: str                          # ok | need_help | no_answer


@app.post("/sim/reset")
def sim_reset():
    rahat.reset()
    return {"ok": True}


@app.post("/sim/replay")
async def sim_replay(body: ReplayIn):
    # only demo scenario files from data/: the endpoint is reachable through the public tunnel
    name = Path(body.scenario).name
    if name != body.scenario or not name.startswith("demo_scenario") or not name.endswith(".json"):
        raise HTTPException(400, "scenario must be a data/demo_scenario*.json file name")
    path = config.DATA_DIR / name
    if not path.exists():
        raise HTTPException(404, f"no such scenario: {name}")
    data = json.loads(path.read_text(encoding="utf-8"))

    async def run():
        t_prev = 0.0
        for m in data["messages"]:
            await asyncio.sleep(max(0.0, (m["t"] - t_prev) / max(body.speed, 0.1)))
            t_prev = m["t"]
            await asyncio.to_thread(rahat.ingest, MessageIn(channel=Channel(m["channel"]), sender_id=m["sender"],
                                                            raw_text=m["text"]))

    asyncio.create_task(run())
    return {"queued": len(data["messages"]), "speed": body.speed}


@app.post("/sim/alert")
def sim_alert():
    alert = json.loads((config.DATA_DIR / "alert_imd_sample.json").read_text(encoding="utf-8"))
    return post_alert(alert)


@app.post("/sim/missed_call")
def sim_missed_call(body: SenderIn):
    return rahat.missed_call(body.sender_id)


@app.post("/sim/ivr")
def sim_ivr(body: IvrIn):
    return rahat.ivr_answer(body.sender_id, body.digit, body.place_id)


@app.post("/sim/proactive/start")
def sim_proactive_start():
    return rahat.start_proactive_checks("operator")


@app.post("/sim/proactive/{resident_id}")
def sim_proactive(resident_id: str, body: ProactiveIn):
    try:
        inc = rahat.proactive_result(resident_id, body.result)
    except KeyError as e:
        _404(e)
    return {"incident": inc}


@app.get("/sim/outbox")
def sim_outbox(sender_id: Optional[str] = None):
    from backend.service import hash_sender
    return rahat.store.outbox(hash_sender(sender_id) if sender_id else None)


def _wa_label(sender_hash: str) -> str:
    """Masked label for a real WhatsApp sender: never the full number (this page is reachable via the tunnel)."""
    wa = whatsapp._reply_to.get(sender_hash)
    return f"WhatsApp ••••{wa[-4:]}" if wa else f"WhatsApp user {sender_hash[:6]}"


@app.get("/sim/live_chats")
def sim_live_chats():
    """Real WhatsApp conversations (messages that came in through the Cloud API webhook), newest first."""
    chats: dict[str, dict] = {}
    for m in rahat.store.all("messages"):
        if m.meta.get("source_system") != "whatsapp_cloud_api":
            continue
        c = chats.setdefault(m.sender_hash, {"sender_hash": m.sender_hash, "label": _wa_label(m.sender_hash),
                                             "count": 0, "last_at": None, "last_text": "", "incident_id": None})
        c["count"] += 1
        if c["last_at"] is None or m.received_at.isoformat() > c["last_at"]:
            c["last_at"], c["last_text"] = m.received_at.isoformat(), (m.raw_text or f"[{m.channel.value}]")[:80]
            c["incident_id"] = m.incident_id
    return sorted(chats.values(), key=lambda c: c["last_at"] or "", reverse=True)


@app.get("/sim/conversation")
def sim_conversation(sender_id: Optional[str] = None, sender_hash: Optional[str] = None):
    """Citizen simulator: what this phone sent + what RAHAT replied, oldest first (survives a page reload).
    sender_hash opens a REAL WhatsApp chat from /sim/live_chats (read-only in the simulator)."""
    from backend.service import hash_sender
    if not (sender_id or sender_hash):
        raise HTTPException(400, "give sender_id or sender_hash")
    h = sender_hash or hash_sender(sender_id)
    def shown(m):
        if m.audio_url:          # voice note: show the transcript once it is ready
            return f"🎙 {m.raw_text}" if m.raw_text else "🎙 Voice note (turning it into text…)"
        return m.raw_text or f"[{m.channel.value}]"
    sent = [{"mine": True, "text": shown(m), "at": m.received_at.isoformat(),
             "channel": m.channel.value} for m in rahat.store.messages_by_sender(h)]
    got = [{"mine": False, "text": o["text"], "at": o["at"], "channel": o["channel"]} for o in rahat.store.outbox(h, 200)]
    from datetime import datetime
    return sorted(sent + got, key=lambda x: datetime.fromisoformat(x["at"]))


@app.get("/sim/open_incidents_for_callback")
def sim_callbacks():
    return [i for i in rahat.store.all("incidents")
            if i.callback.status == "pending" and i.status in (IncidentStatus.open, IncidentStatus.assigned)]


# ============================================================ built frontend (npm run build)
DIST = config.ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/ui", StaticFiles(directory=DIST, html=True), name="ui")


@app.get("/")
def root():
    return RedirectResponse("/ui/" if DIST.exists() else "/docs")
