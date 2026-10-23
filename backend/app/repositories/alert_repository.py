"""Alert rules and notifications (user state, lives in sessions.db)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from backend.app.repositories._sessions_db import connect


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alert(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "preset": r["preset"], "symbol": r["symbol"], "params": json.loads(r["params"]),
            "active": bool(r["active"]), "last_eval_date": r["last_eval_date"], "baseline_id": r["baseline_id"],
            "created_at": r["created_at"], "last_checked": r["last_checked"]}


def _note(r) -> dict:
    return {"id": r["id"], "alert_id": r["alert_id"], "kind": r["kind"], "symbol": r["symbol"], "title": r["title"],
            "body": r["body"], "payload": json.loads(r["payload"]), "created_at": r["created_at"],
            "read": r["read_at"] is not None}


# ------------------------------------------------------------------- alerts
def create_alert(kind: str, preset: str, symbol: str | None, params: dict, last_eval_date: str | None = None,
                 baseline_id: int = 0) -> dict:
    aid = uuid.uuid4().hex[:10]
    with connect() as c:
        c.execute("INSERT INTO alerts(id,kind,preset,symbol,params,active,last_eval_date,baseline_id,created_at) VALUES(?,?,?,?,?,1,?,?,?)",
                  (aid, kind, preset, symbol.upper() if symbol else None, json.dumps(params), last_eval_date, baseline_id, _now()))
    return get_alert(aid)


def get_alert(aid: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    return _alert(r) if r else None


def list_alerts(active_only: bool = False) -> list[dict]:
    with connect() as c:
        q = "SELECT * FROM alerts" + (" WHERE active=1" if active_only else "") + " ORDER BY created_at DESC, id"
        return [_alert(r) for r in c.execute(q)]


def set_active(aid: str, active: bool) -> dict | None:
    with connect() as c:
        c.execute("UPDATE alerts SET active=? WHERE id=?", (1 if active else 0, aid))
    return get_alert(aid)


def delete_alert(aid: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM alerts WHERE id=?", (aid,)).rowcount > 0


def mark_checked(aid: str, last_eval_date: str | None = None, baseline_id: int | None = None) -> None:
    with connect() as c:
        c.execute("UPDATE alerts SET last_checked=?, last_eval_date=COALESCE(?, last_eval_date), "
                  "baseline_id=MAX(baseline_id, COALESCE(?, 0)) WHERE id=?", (_now(), last_eval_date, baseline_id, aid))


# ------------------------------------------------------------ notifications
def add_notification(alert_id: str | None, kind: str, symbol: str, title: str, body: str, payload: dict, dedupe_key: str) -> bool:
    """True when this is a new notification; False when the same event was already recorded."""
    with connect() as c:
        cur = c.execute("INSERT OR IGNORE INTO notifications(alert_id,kind,symbol,title,body,payload,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (alert_id, kind, symbol.upper(), title, body, json.dumps(payload), dedupe_key, _now()))
        return cur.rowcount > 0


def list_notifications(unread_only: bool = False, limit: int = 100) -> list[dict]:
    with connect() as c:
        q = "SELECT * FROM notifications" + (" WHERE read_at IS NULL" if unread_only else "") + " ORDER BY id DESC LIMIT ?"
        return [_note(r) for r in c.execute(q, (limit,))]


def unread_count() -> int:
    with connect() as c:
        return int(c.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0])


def mark_read(ids: list[int] | None = None) -> int:
    """Mark the given notifications read, or all of them when ids is None."""
    with connect() as c:
        if ids is None:
            return c.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (_now(),)).rowcount
        marks = ",".join("?" for _ in ids)
        return c.execute(f"UPDATE notifications SET read_at=? WHERE read_at IS NULL AND id IN ({marks})", [_now(), *ids]).rowcount if ids else 0


def delete_notification(nid: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM notifications WHERE id=?", (nid,)).rowcount > 0
