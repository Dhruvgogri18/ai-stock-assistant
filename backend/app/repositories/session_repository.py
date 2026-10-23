"""Sessions + messages. The only module that reads/writes those tables."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.app.repositories._sessions_db import connect

DEFAULT_TITLE = "New chat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Message:
    id: int
    role: str
    content: str
    created_at: str
    payload: dict = field(default_factory=dict)
    feedback: str | None = None


@dataclass
class Session:
    session_id: str
    title: str = DEFAULT_TITLE
    created_at: str = ""
    updated_at: str = ""
    messages: list[Message] = field(default_factory=list)

    @property
    def history(self) -> list[dict]:
        """OpenAI-style role/content dicts (what the LLM sees)."""
        return [{"role": m.role, "content": m.content} for m in self.messages]


def create_session(title: str = DEFAULT_TITLE) -> Session:
    sid, now = str(uuid.uuid4()), _now()
    with connect() as c:
        c.execute("INSERT INTO sessions(session_id,title,created_at,updated_at) VALUES(?,?,?,?)", (sid, title, now, now))
    return Session(session_id=sid, title=title, created_at=now, updated_at=now)


def _row_to_message(r) -> Message:
    return Message(
        id=r["id"], role=r["role"], content=r["content"], created_at=r["created_at"],
        payload=json.loads(r["payload"]) if r["payload"] else {}, feedback=r["feedback"],
    )


def get_session(session_id: str, *, with_messages: bool = True) -> Session | None:
    with connect() as c:
        row = c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        msgs = []
        if with_messages:
            msgs = [_row_to_message(r) for r in c.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))]
    return Session(row["session_id"], row["title"], row["created_at"], row["updated_at"], msgs)


def list_sessions(limit: int = 200) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT session_id,title,created_at,updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def count_sessions() -> int:
    with connect() as c:
        return int(c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])


def append_message(session_id: str, role: str, content: str, payload: dict | None = None) -> int:
    now = _now()
    with connect() as c:
        cur = c.execute(
            "INSERT INTO messages(session_id,role,content,payload,created_at) VALUES(?,?,?,?,?)",
            (session_id, role, content, json.dumps(payload, default=str) if payload else None, now),
        )
        c.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
        return int(cur.lastrowid)


def set_title(session_id: str, title: str) -> None:
    with connect() as c:
        c.execute("UPDATE sessions SET title=? WHERE session_id=?", (title.strip()[:120] or DEFAULT_TITLE, session_id))


def delete_session(session_id: str) -> bool:
    """Removes the chat from the UI. llm_calls rows and logs/sessions/<id>.log are kept on purpose (audit trail)."""
    with connect() as c:
        return c.execute("DELETE FROM sessions WHERE session_id=?", (session_id,)).rowcount > 0


def set_feedback(session_id: str, message_id: int, value: str | None) -> bool:
    with connect() as c:
        return c.execute(
            "UPDATE messages SET feedback=? WHERE id=? AND session_id=? AND role='assistant'",
            (value, message_id, session_id),
        ).rowcount > 0
