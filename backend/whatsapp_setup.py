"""
One-time WhatsApp webhook setup through Meta's Graph API (instead of clicking in the dashboard).

    .venv\\Scripts\\python -m backend.whatsapp_setup <APP_ID> <TUNNEL_URL>
    e.g. .venv\\Scripts\\python -m backend.whatsapp_setup 1154233990293020 https://abc.trycloudflare.com

What it does (and nothing else):
  1. POST /{app-id}/subscriptions   -> sets callback URL + verify token, field "messages".
     Meta immediately calls GET /webhooks/whatsapp on our backend to verify, so the backend
     and the tunnel must be running.
  2. reads which WhatsApp Business Account (WABA) the token can access (debug_token)
  3. POST /{waba-id}/subscribed_apps -> the WABA sends its messages to this app
  4. prints the active subscription
Sends no WhatsApp messages. Prints no token or secret. Reads WHATSAPP_* from .env.
"""
import sys

import httpx

from backend import whatsapp as w


def main(app_id: str, tunnel: str) -> int:
    if not (w.TOKEN and w.APP_SECRET and w.VERIFY_TOKEN):
        print("Missing WHATSAPP_TOKEN / WHATSAPP_APP_SECRET / WHATSAPP_VERIFY_TOKEN in .env")
        return 1
    cb = tunnel.rstrip("/") + "/webhooks/whatsapp"
    app_token = f"{app_id}|{w.APP_SECRET}"
    g = w.GRAPH

    try:
        local = httpx.get(cb.replace(tunnel.rstrip("/"), "http://127.0.0.1:8000"), timeout=5,
                          params={"hub.mode": "subscribe", "hub.verify_token": w.VERIFY_TOKEN, "hub.challenge": "1"})
        print("0. backend reachable locally:", local.status_code == 200)
    except httpx.HTTPError:
        print("0. backend is NOT running on port 8000: start it first")
        return 1

    r = httpx.post(f"{g}/{app_id}/subscriptions", timeout=30, data={
        "object": "whatsapp_business_account", "callback_url": cb, "verify_token": w.VERIFY_TOKEN,
        "fields": "messages", "access_token": app_token})
    ok = r.status_code == 200
    print("1. webhook set:", ok, "" if ok else r.json().get("error", {}).get("message"))
    if not ok:
        return 1

    d = httpx.get(f"{g}/debug_token", timeout=30,
                  params={"input_token": w.TOKEN, "access_token": app_token}).json().get("data", {})
    wabas = sorted({t for s in d.get("granular_scopes", []) if s.get("scope", "").startswith("whatsapp")
                    for t in s.get("target_ids", [])})
    print("2. token valid:", d.get("is_valid"), "| WhatsApp Business Account ids:", wabas or "none found")

    for waba in wabas:
        s = httpx.post(f"{g}/{waba}/subscribed_apps", timeout=30, headers={"Authorization": f"Bearer {w.TOKEN}"})
        print(f"3. WABA {waba} subscribed:", s.status_code == 200,
              "" if s.status_code == 200 else s.json().get("error", {}).get("message"))

    subs = httpx.get(f"{g}/{app_id}/subscriptions", timeout=30, params={"access_token": app_token}).json()
    for sub in subs.get("data", []):
        print("4. active:", sub.get("object"), "|", sub.get("callback_url"), "|",
              [f.get("name") for f in sub.get("fields", [])])
    print("\nDone. Now send a WhatsApp message to the test number.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
