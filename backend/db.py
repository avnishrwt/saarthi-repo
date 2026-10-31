"""
SQLite store (PostgreSQL later). One table per entity, each row = id + JSON of the
pydantic model, so schemas.py stays the single source of field names. The audit log
is append-only. All access goes through one lock: demo scale is a few hundred rows.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from shared.schemas import (Assignment, AuditLog, Incident, Message, Team, VulnerableResident, now_utc)

T = TypeVar("T", bound=BaseModel)

KINDS: dict[str, Type[BaseModel]] = {
    "messages": Message,
    "incidents": Incident,
    "teams": Team,
    "assignments": Assignment,
    "residents": VulnerableResident,
}


class Store:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        with self.lock:
            for k in KINDS:
                self.conn.execute(f"CREATE TABLE IF NOT EXISTS {k} (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT, action TEXT, entity TEXT, before TEXT, after TEXT, at TEXT)""")
            self.conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS reputation (sender_hash TEXT PRIMARY KEY, score REAL)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_hash TEXT, channel TEXT, text TEXT, incident_id TEXT, at TEXT)""")
            self.conn.commit()

    # ------------------------------------------------------------------ entities
    def put(self, kind: str, obj: BaseModel) -> None:
        with self.lock:
            self.conn.execute(f"INSERT OR REPLACE INTO {kind} (id, data) VALUES (?, ?)",
                              (obj.id, obj.model_dump_json()))
            self.conn.commit()

    def get(self, kind: str, id_: str) -> Optional[Any]:
        with self.lock:
            row = self.conn.execute(f"SELECT data FROM {kind} WHERE id = ?", (id_,)).fetchone()
        return KINDS[kind].model_validate_json(row[0]) if row else None

    def all(self, kind: str) -> list[Any]:
        with self.lock:
            rows = self.conn.execute(f"SELECT data FROM {kind}").fetchall()
        return [KINDS[kind].model_validate_json(r[0]) for r in rows]

    def messages_by_sender(self, sender_hash: str) -> list[Message]:
        with self.lock:
            rows = self.conn.execute("SELECT data FROM messages WHERE json_extract(data, '$.sender_hash') = ?",
                                     (sender_hash,)).fetchall()
        return [Message.model_validate_json(r[0]) for r in rows]

    def delete(self, kind: str, id_: str) -> None:
        with self.lock:
            self.conn.execute(f"DELETE FROM {kind} WHERE id = ?", (id_,))
            self.conn.commit()

    def count(self, kind: str) -> int:
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {kind}").fetchone()[0]

    # ------------------------------------------------------------------ ids
    def next_id(self, prefix: str, width: int = 3) -> str:
        with self.lock:
            n = int(self.kv_get(f"seq:{prefix}", 0)) + 1
            self.kv_set(f"seq:{prefix}", n)
        return f"{prefix}-{n:0{width}d}"

    # ------------------------------------------------------------------ kv
    def kv_get(self, k: str, default: Any = None) -> Any:
        with self.lock:
            row = self.conn.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def kv_set(self, k: str, v: Any) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, json.dumps(v, default=str)))
            self.conn.commit()

    # ------------------------------------------------------------------ audit (FR18)
    def audit(self, actor: str, action: str, entity: str,
              before: Optional[dict] = None, after: Optional[dict] = None) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO audit (actor, action, entity, before, after, at) VALUES (?, ?, ?, ?, ?, ?)",
                (actor, action, entity, json.dumps(before, default=str) if before is not None else None,
                 json.dumps(after, default=str) if after is not None else None, now_utc().isoformat()))
            self.conn.commit()

    def audit_list(self, limit: int = 200, entity: Optional[str] = None) -> list[AuditLog]:
        q = "SELECT id, actor, action, entity, before, after, at FROM audit"
        args: tuple = ()
        if entity:
            q += " WHERE entity = ?"
            args = (entity,)
        q += " ORDER BY id DESC LIMIT ?"
        with self.lock:
            rows = self.conn.execute(q, args + (limit,)).fetchall()
        return [AuditLog(id=r[0], actor=r[1], action=r[2], entity=r[3],
                         before=json.loads(r[4]) if r[4] else None,
                         after=json.loads(r[5]) if r[5] else None,
                         at=datetime.fromisoformat(r[6])) for r in rows]

    # ------------------------------------------------------------------ sender reputation
    def reputation(self, sender_hash: str) -> float:
        with self.lock:
            row = self.conn.execute("SELECT score FROM reputation WHERE sender_hash = ?", (sender_hash,)).fetchone()
        return row[0] if row else 1.0

    def set_reputation(self, sender_hash: str, score: float) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO reputation (sender_hash, score) VALUES (?, ?)",
                              (sender_hash, max(0.0, min(1.0, score))))
            self.conn.commit()

    # ------------------------------------------------------------------ outbox (follow-up SMS, IVR prompts)
    def send(self, sender_hash: str, channel: str, text: str, incident_id: Optional[str] = None) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO outbox (sender_hash, channel, text, incident_id, at) VALUES (?, ?, ?, ?, ?)",
                              (sender_hash, channel, text, incident_id, now_utc().isoformat()))
            self.conn.commit()

    def outbox(self, sender_hash: Optional[str] = None, limit: int = 50) -> list[dict]:
        q = "SELECT sender_hash, channel, text, incident_id, at FROM outbox"
        args: tuple = ()
        if sender_hash:
            q += " WHERE sender_hash = ?"
            args = (sender_hash,)
        q += " ORDER BY id DESC LIMIT ?"
        with self.lock:
            rows = self.conn.execute(q, args + (limit,)).fetchall()
        return [dict(sender_hash=r[0], channel=r[1], text=r[2], incident_id=r[3], at=r[4]) for r in rows]

    # ------------------------------------------------------------------ reset (demo)
    def reset(self) -> None:
        with self.lock:
            for k in KINDS:
                self.conn.execute(f"DELETE FROM {k}")
            for t in ("audit", "kv", "reputation", "outbox"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.commit()
