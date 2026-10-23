"""Append-only session / LLM call logs for debugging and cost tracking.

Writes JSON lines to logs/chat_sessions.jsonl (or SESSION_LOG_PATH).
Token counts use response.usage when the gateway provides it; otherwise
a rough estimate (chars/4) so you still see relative size.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

_lock = threading.Lock()


def _log_path() -> Path:
    raw = getattr(settings, "session_log_path", "") or "logs/chat_sessions.jsonl"
    p = Path(raw)
    if not p.is_absolute():
        # repo root = parents[2] from backend/app/session_log.py
        root = Path(__file__).resolve().parents[2]
        p = root / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def estimate_tokens(text: str) -> int:
    """Rough token estimate when the API does not return usage."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def log_event(event: dict[str, Any]) -> None:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    line = json.dumps(event, default=str, ensure_ascii=False)
    path = _log_path()
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_chat_turn(
    *,
    trace_id: str,
    question: str,
    history_len: int,
    answer: str,
    blocks: list,
    duration_ms: int,
    model: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    tool_calls: list | None = None,
    error: str | None = None,
) -> None:
    tools_used = []
    for b in blocks or []:
        tools_used.append({
            "tool": b.get("tool"),
            "args": b.get("args"),
            "has_error": bool((b.get("data") or {}).get("error")),
        })
    log_event({
        "type": "chat_turn",
        "trace_id": trace_id,
        "model": model,
        "question": question,
        "history_len": history_len,
        "answer_preview": (answer or "")[:500],
        "answer_len": len(answer or ""),
        "tools": tools_used,
        "tool_calls_raw": tool_calls or [],
        "duration_ms": duration_ms,
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
            "prompt_est": estimate_tokens(question),
            "completion_est": estimate_tokens(answer or ""),
        },
        "error": error,
    })
