"""WhatsApp webhook: verification, inbound text/location, replies relayed. No real Meta calls."""
import hashlib
import hmac
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient


def payload(wa_id, text=None, location=None, name="Dost"):
    msg = {"from": wa_id, "id": f"wamid.{time.time_ns()}", "timestamp": "0"}
    if location:
        msg.update(type="location", location=location)
    else:
        msg.update(type="text", text={"body": text})
    return {"object": "whatsapp_business_account", "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "contacts": [{"wa_id": wa_id, "profile": {"name": name}}],
        "messages": [msg]}}]}]}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_DB", str(tmp_path / "wa.db"))
    monkeypatch.setenv("RAHAT_LLM", "rules")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "rahat-test")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    import shared.config, backend.whatsapp, backend.main
    importlib.reload(shared.config)
    importlib.reload(backend.whatsapp)
    main = importlib.reload(backend.main)
    sent = []
    monkeypatch.setattr(main.whatsapp, "send_text", lambda wa, text: sent.append((wa, text)) or True)
    with TestClient(main.app) as c:
        yield c, main, sent


def post(c, body, secret="s3cret"):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": sig,
                                                              "content-type": "application/json"})


def wait(main, cond, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_verify_handshake(app):
    c, _, _ = app
    ok = c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "rahat-test", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                               "hub.challenge": "42"}).status_code == 403


def test_bad_signature_rejected(app):
    c, _, _ = app
    assert post(c, payload("919800000001", "test"), secret="wrong").status_code == 401


def test_text_becomes_incident_and_gets_replies(app):
    c, main, sent = app
    assert post(c, payload("919800000001", "Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai")).status_code == 200
    assert wait(main, lambda: main.rahat.store.all("incidents"))
    inc = main.rahat.store.all("incidents")[0]
    assert inc.location.place_id == "DEEP_NAGAR"
    assert sent and sent[0][0] == "919800000001" and "control room" in sent[0][1]     # ack first
    # the raw number is never stored in the database
    assert "919800000001" not in json.dumps([m.model_dump(mode="json") for m in main.rahat.store.all("messages")])


def test_vague_then_location_pin_joins_same_incident(app):
    c, main, sent = app
    post(c, payload("919800000002", "khana chahiye 4-5 log hai"))
    assert wait(main, lambda: main.rahat.store.all("incidents"))
    assert wait(main, lambda: any("kahan" in t.lower() for _, t in sent))             # follow-up question relayed
    post(c, payload("919800000002", location={"latitude": 30.3245, "longitude": 78.0481, "name": "Parade Ground"}))
    assert wait(main, lambda: main.rahat.store.all("incidents")[0].location.lat is not None)
    incs = main.rahat.store.all("incidents")
    assert len(incs) == 1 and len(incs[0].message_ids) == 2


def test_real_whatsapp_shows_in_simulator_and_dashboard(app):
    """A real WhatsApp message appears in the simulator's live list (masked number) and is badged on the dashboard."""
    c, main, sent = app
    post(c, payload("919800003210", "Majra me chhat pe 5 log phase hai, 2 bacche bhi"))
    assert wait(main, lambda: main.rahat.store.all("incidents"))
    chats = c.get("/sim/live_chats").json()
    assert len(chats) == 1 and chats[0]["label"] == "WhatsApp ••••3210"          # masked, never the full number
    assert "919800003210" not in json.dumps(chats)
    inc_id = chats[0]["incident_id"]
    assert inc_id in c.get("/state").json()["live_incidents"]
    convo = c.get("/sim/conversation", params={"sender_hash": chats[0]["sender_hash"]}).json()
    assert convo[0]["mine"] and "Majra" in convo[0]["text"]
    assert wait(main, lambda: any(not m["mine"] for m in
                                  c.get("/sim/conversation", params={"sender_hash": chats[0]["sender_hash"]}).json()))
    # simulator messages are NOT listed as real WhatsApp
    c.post("/messages", json={"sender_id": "S-sim", "channel": "whatsapp", "raw_text": "deep nagar me paani"})
    time.sleep(0.5)
    assert len(c.get("/sim/live_chats").json()) == 1
