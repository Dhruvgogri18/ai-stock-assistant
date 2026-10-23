"""Per-call LLM records (written by services/llm_trace.py)."""
from __future__ import annotations

import json

from backend.app.repositories._sessions_db import connect


def _j(v):
    return json.dumps(v, ensure_ascii=False, default=str) if v is not None else None


def insert_call(*, session_id, request_id, stage, call_no, model, temperature, started_at, duration_ms,
                prompt_tokens, completion_tokens, total_tokens, usage_source, cost_usd, tools_offered,
                messages, response, tool_calls, finish_reason, error) -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO llm_calls(session_id,request_id,stage,call_no,model,temperature,started_at,duration_ms,
               prompt_tokens,completion_tokens,total_tokens,usage_source,cost_usd,tools_offered,messages,response,
               tool_calls,finish_reason,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, request_id, stage, call_no, model, temperature, started_at, duration_ms, prompt_tokens,
             completion_tokens, total_tokens, usage_source, cost_usd, _j(tools_offered), _j(messages), response,
             _j(tool_calls), finish_reason, error),
        )


def list_calls(session_id: str, *, include_prompts: bool = True) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM llm_calls WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("tools_offered", "messages", "tool_calls"):
            d[k] = json.loads(d[k]) if d[k] else None
        if not include_prompts:
            d.pop("messages", None)
            d.pop("response", None)
        out.append(d)
    return out


def usage_summary(session_id: str) -> dict:
    """Totals for a whole session, with a per-stage breakdown."""
    with connect() as c:
        tot = c.execute(
            """SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) prompt_tokens,
               COALESCE(SUM(completion_tokens),0) completion_tokens, COALESCE(SUM(total_tokens),0) total_tokens,
               COALESCE(SUM(duration_ms),0) llm_ms, COALESCE(SUM(cost_usd),0) cost_usd,
               COALESCE(SUM(error IS NOT NULL),0) errors,
               COALESCE(SUM(usage_source='estimated'),0) estimated_calls
               FROM llm_calls WHERE session_id=?""", (session_id,)).fetchone()
        stages = c.execute(
            """SELECT COALESCE(stage,'-') stage, COUNT(*) calls, SUM(total_tokens) total_tokens, SUM(duration_ms) llm_ms
               FROM llm_calls WHERE session_id=? GROUP BY stage""", (session_id,)).fetchall()
    return {**dict(tot), "stages": {s["stage"]: {"calls": s["calls"], "total_tokens": s["total_tokens"],
                                                 "llm_ms": round(s["llm_ms"] or 0, 1)} for s in stages}}
