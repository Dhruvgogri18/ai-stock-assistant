"""Graph nodes. Each reads GraphState, does one job, returns a partial update.

Every node is wrapped by @traced, which (a) re-binds session/request ids for llm_trace
(LangGraph may run nodes on another thread) and (b) opens an llm_stage so every LLM call,
tool call and timing lands in the session's log under the right stage.
"""
from __future__ import annotations

import contextvars
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

from backend.app.config.settings import settings
from backend.app.repositories import session_repository as repo
from backend.app.services import llm_trace
from backend.app.services.llm import gateway_client as gw
from backend.app.services.orchestration import prompts, router, tools
from backend.app.services.orchestration.state import GraphState

HISTORY_TURNS = 8
HISTORY_CHARS = 4000
TOOL_RESULT_CHARS = 24000


def traced(stage: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(state: GraphState):
            llm_trace.bind(state.get("session_id"), state.get("request_id"))
            with llm_trace.llm_stage(stage):
                return fn(state)
        return wrapper
    return deco


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------- load_context
@traced("load_context")
def load_context(state: GraphState) -> dict:
    sess = repo.get_session(state["session_id"])
    if sess is None:
        raise LookupError(state["session_id"])
    usable = [m for m in sess.messages if not m.payload.get("error")]
    history = [{"role": m.role, "content": (m.content or "")[:HISTORY_CHARS]} for m in usable][-HISTORY_TURNS:]
    return {"history": history, "is_first_turn": len(sess.messages) == 0, "blocks": [], "steps": [], "follow_ups": []}


# ------------------------------------------------------------------ route_turn
@traced("route_turn")
def route_turn(state: GraphState) -> dict:
    if not settings.router_enabled or not gw.configured():
        return {"route": "research", "route_rationale": "router disabled"}
    t0 = time.perf_counter()
    d = router.route_turn(user_message=state["user_message"], history=state.get("history", []))
    llm_trace.log_event("route_decision", route=d.route, topic=d.topic, title=d.title, rationale=d.rationale)
    steps = state.get("steps", []) + [{"label": "Understood your question", "ms": _ms(t0), "ok": True}]
    return {"route": d.route, "route_rationale": d.rationale, "topic": d.topic, "title": d.title, "steps": steps}


def after_router(state: GraphState) -> str:
    return state.get("route") or "research"


# -------------------------------------------------------------------- research
def _run_one(call: dict, round_i: int) -> dict:
    name, raw = call["name"], call["arguments"]
    t0 = time.perf_counter()
    args: dict = {}
    try:
        args = json.loads(raw or "{}")
        out = tools.run_tool(name, args)
        err = None
    except Exception as e:  # noqa: BLE001 - a bad tool call must not kill the turn
        out, err = {"error": str(e)}, str(e)
    ms = _ms(t0)
    text = json.dumps(tools.llm_view(name, out), default=str, ensure_ascii=False)
    truncated = len(text) > TOOL_RESULT_CHARS
    if truncated:
        text = text[:TOOL_RESULT_CHARS] + '..."[truncated]"'
    llm_trace.log_event(
        "tool_call", tool=name, args=args, duration_ms=ms, round=round_i, error=err,
        result_chars=len(text), truncated=truncated or None,
        source=out.get("source") if isinstance(out, dict) else None,
        provider=out.get("provider") if isinstance(out, dict) else None,
    )
    return {"id": call["id"], "name": name, "args": args, "out": out, "text": text, "ms": ms, "error": err}


def _run_tools(calls: list[dict], round_i: int) -> list[dict]:
    if len(calls) == 1:
        return [_run_one(calls[0], round_i)]
    # data calls are independent network I/O: run them side by side, keep order
    with ThreadPoolExecutor(min(6, len(calls))) as pool:
        futs = [pool.submit(contextvars.copy_context().run, _run_one, c, round_i) for c in calls]
        return [f.result() for f in futs]


@traced("research")
def research(state: GraphState) -> dict:
    sid, topic = state["session_id"], state.get("topic")
    llm_trace.set_status(sid, "research", label=f"Analysing {topic}…" if topic else "Analysing…")
    steps, blocks = list(state.get("steps", [])), []

    if not gw.configured():
        return {"answer": "Set LLM_API_KEY and LLM_MODEL in .env to enable chat.", "error": "llm_not_configured",
                "steps": steps, "blocks": blocks}

    msgs = [{"role": "system", "content": prompts.research_system()}, *state.get("history", []),
            {"role": "user", "content": state["user_message"]}]
    answer, error, model_ms = "", None, 0
    try:
        for round_i in range(settings.max_tool_rounds):
            t_llm = time.perf_counter()
            res = gw.chat_completion(msgs, tools=tools.TOOLS)
            model_ms += _ms(t_llm)
            if not res.tool_calls:
                answer = res.content or ""
                break
            msgs.append(res.assistant_message)
            llm_trace.set_status(sid, "research", label=tools.status_label(res.tool_calls[0]["name"],
                                 _safe_args(res.tool_calls[0]["arguments"])))
            for r in _run_tools(res.tool_calls, round_i):
                blocks.append({"tool": r["name"], "args": r["args"], "data": r["out"]})
                steps.append({"label": tools.status_label(r["name"], r["args"]).rstrip("…"), "ms": r["ms"], "ok": r["error"] is None})
                msgs.append({"role": "tool", "tool_call_id": r["id"], "content": r["text"]})
        else:
            # tool budget spent: force a final answer from what we already have
            llm_trace.set_status(sid, "research", label="Writing the answer…")
            msgs.append({"role": "user", "content": "Answer now using only the data above. Do not call more tools."})
            t_llm = time.perf_counter()
            answer = gw.chat_completion(msgs).content or ""
            model_ms += _ms(t_llm)
    except gw.LLMError as e:
        error = str(e)
        answer = "I couldn't reach the model just now, so I can't answer that yet. Please try again in a moment."
    if not answer.strip() and not error:
        answer = "I couldn't complete that request."
    steps.append({"label": "Wrote the answer", "ms": model_ms, "ok": error is None})
    return {"answer": answer, "blocks": blocks, "steps": steps, "error": error}


def _safe_args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- out_of_scope
@traced("out_of_scope")
def out_of_scope(state: GraphState) -> dict:
    return {"answer": prompts.OUT_OF_SCOPE_MESSAGE, "follow_ups": prompts.OUT_OF_SCOPE_FOLLOW_UPS, "blocks": []}


# -------------------------------------------------------------------- finalize
_FU_RE = re.compile(r"<follow_ups>\s*(.*?)\s*(?:</follow_ups>|$)", re.DOTALL | re.IGNORECASE)


def split_follow_ups(text: str) -> tuple[str, list[str]]:
    """Pull the <follow_ups>["..",".."]</follow_ups> trailer off the answer."""
    m = _FU_RE.search(text or "")
    if not m:
        return (text or "").strip(), []
    body, clean = m.group(1), (text[: m.start()] + text[m.end():]).strip()
    try:
        items = json.loads(body)
        if not isinstance(items, list):
            items = []
    except Exception:
        items = re.findall(r'"([^"]{3,160})"', body)
    return clean, [str(i).strip() for i in items if str(i).strip()][:3]


@traced("finalize")
def finalize(state: GraphState) -> dict:
    answer, parsed = split_follow_ups(state.get("answer", ""))
    follow = parsed or state.get("follow_ups", [])
    seen, sources = set(), []
    for b in state.get("blocks", []):
        d = b.get("data") or {}
        key = (d.get("source"), d.get("as_of"))
        if d.get("source") and key not in seen:
            seen.add(key)
            sources.append({"tool": b["tool"], "source": d["source"], "as_of": d.get("as_of"), "provider": d.get("provider")})
    usage = llm_trace.request_totals(state.get("request_id"))
    return {"answer": answer, "follow_ups": follow, "sources": sources, "usage": usage,
            "duration_ms": _ms(state["t_start"]) if state.get("t_start") else 0}


# --------------------------------------------------------------------- persist
@traced("persist")
def persist(state: GraphState) -> dict:
    sid = state["session_id"]
    repo.append_message(sid, "user", state["user_message"])
    payload = {
        "blocks": state.get("blocks", []), "follow_ups": state.get("follow_ups", []), "steps": state.get("steps", []),
        "sources": state.get("sources", []), "usage": state.get("usage", {}), "route": state.get("route"),
        "topic": state.get("topic"), "duration_ms": state.get("duration_ms"), "request_id": state.get("request_id"),
        "error": state.get("error"),
    }
    mid = repo.append_message(sid, "assistant", state.get("answer", ""), payload)
    title = None
    if state.get("is_first_turn"):
        title = (state.get("title") or state["user_message"]).strip()[:60]
        repo.set_title(sid, title)
    return {"message_id": mid, "title": title}
