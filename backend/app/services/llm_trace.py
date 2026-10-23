"""LLM process logger: everything the model sees and says, grouped by chat session.

Three outputs, all keyed by session_id:

1. logs/sessions/<session_id>.log   human-readable transcript, read top to bottom:
      REQUEST START -> STAGE START -> LLM CALL (full prompt) -> LLM RESPONSE
      (completion / tool calls / tokens / ms) -> TOOL CALL -> STAGE END -> REQUEST END
2. logs/index.log                   one line per event across ALL sessions (grep this first)
3. SQLite table `llm_calls`         one row per model call (queryable; powers
                                    GET /api/session/{id}/llm-calls and /usage)

Token numbers come from the gateway's `usage` when it returns one
(usage_source="provider"); otherwise they are estimated (tiktoken if installed,
else chars/4) and marked usage_source="estimated".

Usage in services:

    start_request(session_id=..., user_message=...)
    with llm_stage("research"):
        ...                      # gateway_client.chat_completion() logs itself
    end_request(status="ok")

THREADS: the session/request/stage live in ContextVars. LangGraph nodes and
thread pools may not inherit them, so graph nodes call `bind()` first (see
orchestration/nodes.py) and any code that fans work out to threads should
submit `contextvars.copy_context().run(fn)`.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from backend.app.config.settings import settings

# --------------------------------------------------------------------------
# token + cost accounting
# --------------------------------------------------------------------------
try:  # optional
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore

MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    # (input, output) USD per 1M tokens. Rough, goes stale: relative signal only.
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


@lru_cache(maxsize=1)
def _encoding():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # offline / no cache
        return None


def count_tokens(text: str | None) -> int:
    if not text:
        return 0
    enc = _encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _message_text(m: Any) -> str:
    if not isinstance(m, dict):
        return str(m)
    parts = [str(m.get("content") or "")]
    if m.get("tool_calls"):
        parts.append(json.dumps(m["tool_calls"], default=str))
    return "\n".join(p for p in parts if p)


def estimate_prompt_tokens(messages: list[dict]) -> int:
    return sum(count_tokens(_message_text(m)) for m in messages or [])


def estimate_cost_usd(prompt: int, completion: int, model: str | None) -> float | None:
    row = MODEL_PRICING_PER_1M.get(settings.llm_pricing_model or model or "")
    if row is None:
        return None
    return round(prompt * row[0] / 1e6 + completion * row[1] / 1e6, 6)


# --------------------------------------------------------------------------
# paths + switches
# --------------------------------------------------------------------------
_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _sessions_dir() -> Path:
    d = settings.log_root / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_log_path(session_id: str | None) -> Path:
    stem = "".join(c if c in _SAFE else "_" for c in str(session_id or "")).strip("._") or "_unassigned"
    return _sessions_dir() / f"{stem}.log"


def _index_path() -> Path:
    settings.log_root.mkdir(parents=True, exist_ok=True)
    return settings.log_root / "index.log"


def _truncate(s: Any) -> Any:
    if not isinstance(s, str):
        return s
    n = max(1000, settings.llm_log_max_content)
    return s if len(s) <= n else s[:n] + f"\n...[truncated {len(s) - n} chars]"


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------
_request_id: ContextVar[str | None] = ContextVar("llm_request_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("llm_session_id", default=None)
_stage: ContextVar[str | None] = ContextVar("llm_stage", default=None)
# per-request LLM call counter (keyed by request_id so it survives thread hops)
_seq_lock = threading.Lock()
_seq: dict[str, int] = {}


def _next_call_no() -> int:
    rid = _request_id.get() or "_none"
    with _seq_lock:
        _seq[rid] = _seq.get(rid, 0) + 1
        return _seq[rid]

_write_lock = threading.Lock()


def get_session_id() -> str | None:
    return _session_id.get()


def get_request_id() -> str | None:
    return _request_id.get()


def get_stage() -> str | None:
    return _stage.get()


def bind(session_id: str | None, request_id: str | None) -> None:
    """Re-attach context in a thread that did not inherit it (LangGraph nodes call this)."""
    if session_id:
        _session_id.set(session_id)
    if request_id:
        _request_id.set(request_id)


# --------------------------------------------------------------------------
# live status (polled by GET /session/{id}/status) + per-request totals
# --------------------------------------------------------------------------
_status_lock = threading.Lock()
_status: dict[str, dict[str, Any]] = {}

_STAGE_LABELS = {
    "load_context": "Reading the conversation…",
    "route_turn": "Understanding your question…",
    "research": "Analysing…",
    "finalize": "Putting the answer together…",
    "persist": "Saving…",
}


def set_status(session_id: str | None, stage: str | None, *, label: str | None = None, status: str = "running") -> None:
    if not session_id:
        return
    with _status_lock:
        if status == "done":
            _status.pop(session_id, None)
            return
        _status[session_id] = {
            "stage": stage,
            "label": label or _STAGE_LABELS.get(stage or "", (stage or "working").replace("_", " ").capitalize() + "…"),
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def get_status(session_id: str) -> dict[str, Any] | None:
    with _status_lock:
        snap = _status.get(session_id)
        return dict(snap) if snap else None


_totals_lock = threading.Lock()
_totals: dict[str, dict[str, Any]] = {}


def _empty_totals() -> dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "llm_ms": 0.0}


def request_totals(request_id: str | None = None) -> dict[str, Any]:
    with _totals_lock:
        return dict(_totals.get(request_id or _request_id.get() or "", _empty_totals()))


def _add_totals(request_id: str | None, usage: dict, duration_ms: float, cost: float | None) -> None:
    if not request_id:
        return
    with _totals_lock:
        t = _totals.setdefault(request_id, _empty_totals())
        t["calls"] += 1
        t["prompt_tokens"] += usage["prompt_tokens"]
        t["completion_tokens"] += usage["completion_tokens"]
        t["total_tokens"] += usage["total_tokens"]
        t["llm_ms"] += duration_ms
        if cost is not None:
            t["cost_usd"] += cost


# --------------------------------------------------------------------------
# request / stage lifecycle
# --------------------------------------------------------------------------
def start_request(*, session_id: str | None = None, user_message: str | None = None, request_id: str | None = None) -> str:
    rid = request_id or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    if session_id:
        _session_id.set(session_id)
    log_event("request_start", session_id=session_id, user_message=_truncate(user_message))
    set_status(session_id, None, label="Starting…")
    return rid


def end_request(*, status: str | None = None, error: str | None = None, duration_ms: float | None = None) -> dict[str, Any]:
    rid = _request_id.get()
    t = request_totals(rid)
    log_event(
        "request_end",
        status=status,
        error=_truncate(error),
        duration_ms=duration_ms,
        llm_calls=t["calls"],
        llm_ms=round(t["llm_ms"], 1),
        prompt_tokens=t["prompt_tokens"],
        completion_tokens=t["completion_tokens"],
        total_tokens=t["total_tokens"],
        cost_usd=t["cost_usd"] or None,
    )
    set_status(_session_id.get(), None, status="done")
    with _totals_lock:
        _totals.pop(rid or "", None)
    with _seq_lock:
        _seq.pop(rid or "_none", None)
    return t


@contextmanager
def llm_stage(name: str, *, label: str | None = None, **meta: Any) -> Iterator[None]:
    token = _stage.set(name)
    t0 = time.perf_counter()
    log_event("stage_start", stage=name, **meta)
    set_status(_session_id.get(), name, label=label)
    try:
        yield
        log_event("stage_end", stage=name, status="ok", duration_ms=round((time.perf_counter() - t0) * 1000, 1))
    except Exception as exc:
        log_event(
            "stage_end", stage=name, status="error", duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            error=f"{type(exc).__name__}: {_truncate(str(exc))}",
        )
        raise
    finally:
        _stage.reset(token)


# --------------------------------------------------------------------------
# event writer
# --------------------------------------------------------------------------
def log_event(event: str, **fields: Any) -> None:
    """Write one event to the session's transcript + the cross-session index. Never raises."""
    if not settings.llm_log:
        return
    try:
        record = {
            "ts": datetime.now(timezone.utc),
            "event": event,
            "request_id": _request_id.get(),
            "session_id": fields.pop("session_id", None) or _session_id.get(),
            "stage": fields.pop("stage", None) or _stage.get(),
            **{k: v for k, v in fields.items() if v is not None},
        }
        block = _format_block(record)
        line = _format_index_line(record)
        with _write_lock:
            with session_log_path(record["session_id"]).open("a", encoding="utf-8") as f:
                f.write(block)
            with _index_path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:  # logging must never break a user request
        print(f"[llm_trace] log_event failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# LLM call logging
# --------------------------------------------------------------------------
@dataclass
class LlmCallHandle:
    t0: float
    started_at: str
    model: str | None
    messages: list[dict]
    est_prompt_tokens: int
    session_id: str | None
    request_id: str | None
    stage: str | None
    seq: int
    tool_names: list[str]
    temperature: float | None


def _clean_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            out.append({"role": "?", "content": str(m)})
            continue
        item = {"role": m.get("role"), "content": _truncate(m.get("content"))}
        if m.get("tool_calls"):
            item["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            item["tool_call_id"] = m["tool_call_id"]
        out.append(item)
    return out


def log_llm_call(*, messages: list[dict], model: str | None, temperature: float | None = None,
                 tools: list[dict] | None = None, extra: dict | None = None) -> LlmCallHandle:
    call_no = _next_call_no()
    est = estimate_prompt_tokens(messages)
    tool_names = [t.get("function", {}).get("name", "?") for t in tools or []]
    payload: dict[str, Any] = {
        "call_no": call_no,
        "model": model,
        "temperature": temperature,
        "message_count": len(messages or []),
        "roles": ",".join(str(m.get("role")) for m in messages or [] if isinstance(m, dict)),
        "est_prompt_tokens": est,
        "tools_offered": ",".join(tool_names) or None,
    }
    if settings.llm_log_prompts:
        payload["messages"] = _clean_messages(messages)
    else:
        payload["messages_omitted"] = True
    if extra:
        payload["extra"] = extra
    log_event("llm_call", **payload)
    return LlmCallHandle(
        t0=time.perf_counter(), started_at=datetime.now(timezone.utc).isoformat(), model=model,
        messages=messages, est_prompt_tokens=est, session_id=_session_id.get(), request_id=_request_id.get(),
        stage=_stage.get(), seq=call_no, tool_names=tool_names, temperature=temperature,
    )


def log_llm_response(*, handle: LlmCallHandle, content: str | None = None, tool_calls: list[dict] | None = None,
                     usage: dict | None = None, model: str | None = None, error: str | None = None,
                     finish_reason: str | None = None) -> dict[str, Any]:
    """Log the completion (or failure) for `handle`. Returns the usage dict that was recorded."""
    duration_ms = round((time.perf_counter() - handle.t0) * 1000, 1)
    model = model or handle.model

    if usage and usage.get("prompt_tokens") is not None:
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        source = "provider"
    else:
        pt = handle.est_prompt_tokens
        ct = count_tokens(content) + count_tokens(json.dumps(tool_calls, default=str) if tool_calls else "")
        source = "estimated"
    used = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct, "usage_source": source}
    cost = estimate_cost_usd(pt, ct, model)

    payload: dict[str, Any] = {
        "call_no": handle.seq, "model": model, "duration_ms": duration_ms, "finish_reason": finish_reason,
        **used, "cost_usd": cost, "error": _truncate(error),
    }
    if tool_calls:
        payload["tool_calls"] = tool_calls
    if settings.llm_log_prompts and content is not None:
        payload["response"] = _truncate(content)
    log_event("llm_response", stage=handle.stage, **payload)

    _add_totals(handle.request_id, used, duration_ms, cost)
    _persist_call(handle, content, tool_calls, used, duration_ms, cost, error, finish_reason, model)
    return used


def _persist_call(handle, content, tool_calls, used, duration_ms, cost, error, finish_reason, model) -> None:
    if not settings.llm_log:
        return
    try:
        from backend.app.repositories import llm_call_repository as repo

        repo.insert_call(
            session_id=handle.session_id, request_id=handle.request_id, stage=handle.stage, call_no=handle.seq,
            model=model, temperature=handle.temperature, started_at=handle.started_at, duration_ms=duration_ms,
            prompt_tokens=used["prompt_tokens"], completion_tokens=used["completion_tokens"],
            total_tokens=used["total_tokens"], usage_source=used["usage_source"], cost_usd=cost,
            tools_offered=handle.tool_names,
            messages=_clean_messages(handle.messages) if settings.llm_log_prompts else None,
            response=_truncate(content) if settings.llm_log_prompts else None,
            tool_calls=tool_calls, finish_reason=finish_reason, error=_truncate(error),
        )
    except Exception as exc:
        print(f"[llm_trace] could not persist llm call: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# readable formatting
# --------------------------------------------------------------------------
_RULE = "-" * 78
_HEADERS = {
    "request_start": "REQUEST START", "request_end": "REQUEST END",
    "stage_start": "STAGE START", "stage_end": "STAGE END",
    "llm_call": "LLM CALL", "llm_response": "LLM RESPONSE", "tool_call": "TOOL CALL",
}
_SKIP = {"ts", "event", "request_id", "session_id", "stage", "status", "duration_ms"}
_BLOCKS = {"messages", "response", "user_message", "extra", "tool_calls", "args", "error"}


def _indent(text: str, pad: str) -> str:
    return "\n".join(pad + ln for ln in str(text).splitlines()) or pad


def _scalar(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def _block_value(key: str, v: Any) -> str:
    if key == "messages" and isinstance(v, list):
        parts = []
        for m in v:
            role = m.get("role", "?")
            body = str(m.get("content") or "")
            if m.get("tool_calls"):
                body += ("\n" if body else "") + "tool_calls: " + json.dumps(m["tool_calls"], ensure_ascii=False, default=str)
            if m.get("tool_call_id"):
                role += f" (tool_call_id={m['tool_call_id']})"
            parts.append(f"[{role}]\n{_indent(body, '  ')}")
        return "\n\n".join(parts)
    if isinstance(v, str):
        try:
            return json.dumps(json.loads(v), indent=2, ensure_ascii=False)
        except Exception:
            return v
    return json.dumps(v, indent=2, ensure_ascii=False, default=str)


def _format_block(r: dict[str, Any]) -> str:
    t: datetime = r["ts"]
    title = _HEADERS.get(r["event"], r["event"].replace("_", " ").upper())
    if r.get("stage") and r["event"] not in ("request_start", "request_end"):
        title += f"  {r['stage']}" if r["event"].startswith("stage") else f"  ({r['stage']})"
    bits = [str(r["status"])] if r.get("status") else []
    if r.get("duration_ms") is not None:
        bits.append(f"{r['duration_ms']}ms")
    head = f"{t.strftime('%H:%M:%S.%f')[:-3]}  {title}" + (f"  [{', '.join(bits)}]" if bits else "")
    lines = [_RULE, head, _RULE]
    if r.get("request_id"):
        lines.append(f"  request_id    : {r['request_id']}")
    if r.get("session_id"):
        lines.append(f"  session_id    : {r['session_id']}")
    for k, v in r.items():
        if k in _SKIP or v is None:
            continue
        if k in _BLOCKS:
            lines += [f"  {k}:", _indent(_block_value(k, v), "    ")]
        else:
            lines.append(f"  {k:<14}: {_scalar(v)}")
    return "\n".join(lines) + "\n\n"


def _format_index_line(r: dict[str, Any]) -> str:
    bits = [r["ts"].strftime("%H:%M:%S.%f")[:-3], f"{r['event']:<13}", f"{(r.get('stage') or '-'):<16}"]
    bits.append(f"{(r.get('status') or '-'):<6}")
    d = r.get("duration_ms")
    bits.append(f"{d}ms" if d is not None else "-")
    if r.get("total_tokens"):
        bits.append(f"tok={r['total_tokens']}")
    if r.get("tool"):
        bits.append(f"tool={r['tool']}")
    bits.append(f"session={(r.get('session_id') or '-')[:8]}")
    bits.append(f"req={r.get('request_id') or '-'}")
    return " ".join(bits)
