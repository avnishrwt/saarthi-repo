"""
WhatsApp Cloud API (Meta) as a REAL message source for the demo.

Inbound : Meta calls POST /webhooks/whatsapp for every message sent to our WhatsApp number.
          text -> raw_text, location pin -> lat/lng, voice note -> downloaded -> speech-to-text.
          Each one goes through the SAME POST /messages pipeline as the simulator.
Outbound: acknowledgement, follow-up questions (FR03) and "team dispatched" updates go back
          to the sender on WhatsApp (free-form text is allowed within 24 h of their message).

Privacy: we still store only the hashed sender id. The raw WhatsApp number needed for
replies is kept IN MEMORY only (never in the database) and is lost on restart.

.env:
  WHATSAPP_TOKEN            access token from Meta developer dashboard (WhatsApp > API Setup)
  WHATSAPP_PHONE_NUMBER_ID  "Phone number ID" of the test number (not the phone number itself)
  WHATSAPP_VERIFY_TOKEN     any string you choose; type the same one in Meta's webhook settings
  WHATSAPP_APP_SECRET       App secret (App settings > Basic) -> we verify Meta's signature
  WHATSAPP_API_VERSION      Graph API version, default v23.0 (check Meta's changelog)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from typing import Optional

import httpx

from shared import config

log = logging.getLogger("rahat.whatsapp")

TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{API_VERSION}"

ACK = ("RAHAT: Aapka message control room tak pahunch gaya hai (ref {ref}). "
       "Agar jaan ka khatra hai toh 112 pe bhi call karein.")
DISPATCHED = "RAHAT: {team} aapki taraf bheji gayi hai. Andaazan {eta} min. Ho sake toh oonchi jagah pe rahein."

_reply_to: dict[str, str] = {}      # sender_hash -> wa_id, memory only


def configured() -> bool:
    return bool(TOKEN and PHONE_NUMBER_ID)


def remember(sender_hash: str, wa_id: str) -> None:
    _reply_to[sender_hash] = wa_id


def is_whatsapp_sender(sender_hash: str) -> bool:
    return sender_hash in _reply_to


def verify_signature(raw_body: bytes, header: Optional[str]) -> bool:
    """Meta signs each webhook with HMAC-SHA256(app_secret, body). Without a secret set we accept
    (local testing), but log a warning once."""
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def send_text(wa_id: str, text: str) -> bool:
    if not configured():
        log.info("[whatsapp not configured] would send to %s…: %s", wa_id[:4], text[:80])
        return False
    try:
        r = httpx.post(f"{GRAPH}/{PHONE_NUMBER_ID}/messages", timeout=10,
                       headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"messaging_product": "whatsapp", "to": wa_id, "type": "text",
                             "text": {"preview_url": False, "body": text[:4000]}})
        if r.status_code >= 300:
            log.warning("WhatsApp send failed %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except httpx.HTTPError as e:
        log.warning("WhatsApp send error: %s", e)
        return False


def relay(sender_hash: str, text: str) -> None:
    """Called for every outbox entry (follow-up questions, IVR prompts, updates)."""
    wa = _reply_to.get(sender_hash)
    if wa:
        send_text(wa, text)


def download_media(media_id: str) -> Optional[str]:
    """Voice notes: GET /{media-id} gives a short-lived URL; download it with the same token."""
    if not configured():
        return None
    try:
        meta = httpx.get(f"{GRAPH}/{media_id}", timeout=10, headers={"Authorization": f"Bearer {TOKEN}"}).json()
        data = httpx.get(meta["url"], timeout=30, headers={"Authorization": f"Bearer {TOKEN}"}).content
        ext = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/amr": ".amr"}.get(
            (meta.get("mime_type") or "").split(";")[0], ".ogg")
        path = config.ROOT / "uploads" / f"wa-{uuid.uuid4().hex}{ext}"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return str(path)
    except Exception as e:
        log.warning("media download failed: %s", e)
        return None


def parse(payload: dict) -> list[dict]:
    """Flatten Meta's webhook payload into simple dicts. Status callbacks (sent/delivered/read)
    carry no 'messages' and are ignored."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                item = {"wa_id": m.get("from"), "id": m.get("id"), "type": m.get("type"),
                        "name": names.get(m.get("from")), "text": "", "lat": None, "lng": None, "media_id": None}
                t = m.get("type")
                if t == "text":
                    item["text"] = m.get("text", {}).get("body", "")
                elif t == "location":
                    loc = m.get("location", {})
                    item["lat"], item["lng"] = loc.get("latitude"), loc.get("longitude")
                    item["text"] = " ".join(x for x in (loc.get("name"), loc.get("address")) if x) or "[location pin]"
                elif t in ("audio", "voice"):
                    item["media_id"] = m.get(t, {}).get("id")
                elif t == "image":
                    item["text"] = m.get("image", {}).get("caption", "") or "[photo without caption]"
                elif t == "button":
                    item["text"] = m.get("button", {}).get("text", "")
                else:
                    item["text"] = f"[unsupported WhatsApp message type: {t}]"
                out.append(item)
    return out
