"""
Surge Mode (PRD 6.6, FR17). RAHAT does not predict disasters; it switches mode.

    official alert + message spike -> ON automatically
    message spike only             -> PENDING_CONFIRM (ask the duty officer)
    official alert only            -> STANDBY (teams ready, proactive calls start)
    officer switches it on         -> ON

A spike = help messages from ONE area within SURGE_SPIKE_WINDOW_MIN >= SURGE_SPIKE_COUNT.
(Prototype rule: a fixed threshold. Real version: compare with that area's normal rate.)
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Optional

from shared import config
from shared.schemas import MessageStatus, now_utc

DEFAULT = {"mode": "off", "alert": None, "spike": None, "reason": "no alert, no spike", "since": None,
           "hazard": "flood", "proactive_started": False}


def get(store) -> dict:
    return {**DEFAULT, **(store.kv_get("surge") or {})}


def _set(store, state: dict, actor: str, why: str) -> dict:
    before = get(store)
    if before["mode"] != state["mode"]:
        state["since"] = now_utc().isoformat()
        store.audit(actor, f"surge_{state['mode']}", "surge", {"mode": before["mode"]}, {"mode": state["mode"], "why": why})
    state["reason"] = why
    store.kv_set("surge", state)
    return state


def detect_spike(store, now=None) -> Optional[dict]:
    now = now or now_utc()
    window = timedelta(minutes=config.SURGE_SPIKE_WINDOW_MIN)
    areas = Counter()
    for m in store.all("messages"):
        if m.status in (MessageStatus.merged,) and now - m.received_at <= window and m.incident_id:
            inc = store.get("incidents", m.incident_id)
            if inc and inc.location.in_area:
                areas["affected area"] += 1
    if not areas:
        return None
    area, n = areas.most_common(1)[0]
    if n >= config.SURGE_SPIKE_COUNT:
        return {"area": area, "count": n, "window_min": config.SURGE_SPIKE_WINDOW_MIN}
    return None


def evaluate(store, actor: str = "system") -> dict:
    """Re-check the trigger table. Called after every message and after an alert."""
    s = get(store)
    if s["mode"] == "on":
        return s
    spike = detect_spike(store)
    s["spike"] = spike
    if s["alert"] and spike:
        return _set(store, {**s, "mode": "on"}, actor, f"official alert + spike ({spike['count']} msgs / {spike['window_min']} min)")
    if spike and not s["alert"]:
        return _set(store, {**s, "mode": "pending_confirm"}, actor, f"spike only ({spike['count']} msgs): duty officer must confirm")
    if s["alert"]:
        return _set(store, {**s, "mode": "standby"}, actor, "official alert only: teams on standby, proactive calls")
    store.kv_set("surge", s)
    return s


def receive_alert(store, alert: dict) -> dict:
    s = get(store)
    s["alert"] = alert
    s["hazard"] = alert.get("hazard", s["hazard"])
    store.kv_set("surge", s)
    store.audit(alert.get("source", "alert_feed"), "alert_received", "surge", None, alert)
    return evaluate(store, alert.get("source", "alert_feed"))


def manual(store, on: bool, actor: str) -> dict:
    s = get(store)
    if on:
        return _set(store, {**s, "mode": "on"}, actor, f"switched on by {actor}")
    return _set(store, {**s, "mode": "off", "alert": None, "spike": None}, actor, f"switched off by {actor}")
