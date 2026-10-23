"""Saved screeners (user state, lives in sessions.db next to chats and the watchlist)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from backend.app.repositories._sessions_db import connect


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "description": r["description"], "source": r["source"],
            "definition": json.loads(r["definition"]), "created_at": r["created_at"], "updated_at": r["updated_at"]}


def create(name: str, description: str, definition: dict, source: str = "custom") -> dict:
    sid, now = uuid.uuid4().hex[:12], _now()
    with connect() as c:
        c.execute("INSERT INTO screeners(id,name,description,source,definition,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (sid, name, description, source, json.dumps(definition), now, now))
    return get(sid)


def get(sid: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM screeners WHERE id=?", (sid,)).fetchone()
    return _out(r) if r else None


def list_all() -> list[dict]:
    with connect() as c:
        return [_out(r) for r in c.execute("SELECT * FROM screeners ORDER BY updated_at DESC, created_at DESC")]


def update(sid: str, name: str | None = None, description: str | None = None, definition: dict | None = None) -> dict | None:
    cur = get(sid)
    if not cur:
        return None
    with connect() as c:
        c.execute("UPDATE screeners SET name=?, description=?, definition=?, updated_at=? WHERE id=?",
                  (cur["name"] if name is None else name, cur["description"] if description is None else description,
                   json.dumps(cur["definition"] if definition is None else definition), _now(), sid))
    return get(sid)


def delete(sid: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM screeners WHERE id=?", (sid,)).rowcount > 0
