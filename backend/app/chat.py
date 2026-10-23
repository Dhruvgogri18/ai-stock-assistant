"""Plain-English layer. The LLM only picks a tool and explains its output;
all figures come from data.py (never invented)."""
from __future__ import annotations

import json
import time

from . import data, llm
from .config import settings
from .session_log import log_chat_turn, log_event, new_trace_id

SYSTEM = """You are a professional Indian equity research assistant (NSE/BSE).

RULES
- Always call tools for figures, prices, ownership, news, filings, and charts.
- Never invent numbers, deal sizes, guidance, AI-revenue stats, or quotes.
- If a metric is missing from tool data, write "Not available" — do not fill from memory.
- No buy/sell recommendations. No ascii sparklines or bar charts (the UI draws charts).
- Keep language clear; define jargon once the first time it appears.

OUTPUT FORMAT (use for EVERY answer)
Write like a polished research brief, not a chat bubble:

1) One-line title (bold), e.g. **TCS — key ratios** or **IT peer comparison**
2) Two-sentence executive summary
3) Sections with markdown headings (##) and tables where data exists
4) ## What stands out — 3–5 bullets grounded only in tool data
5) Closing line with source and as-of from the tools

SECTION RECIPES BY QUERY TYPE
- Results / financials:
  ## Revenue, operating profit & PAT (table by year/quarter)
  ## What stands out
- Ratios / valuation:
  ## Valuation & profitability (metric | value table)
  ## How to read these numbers (one short plain-English note)
  ## What stands out
- Peers / compare:
  ## Valuation & fundamentals (one wide table: all peers × metrics)
  ## What stands out
  Rank leaders only when numbers exist in the table
- Ownership:
  ## Shareholding pattern (table)
  ## What stands out
- News:
  ## Recent headlines (bullets with title + publisher if present)
  ## What stands out
- Events / calendar:
  ## Upcoming dates (table or bullets)
- Price chart / history:
  Brief trend commentary only; do not paste coordinate lists
  ## What stands out (high / low / last if present in tool data)
- Screener:
  ## Matches (table of symbols + metrics)
  ## Filters applied
  ## What stands out
- Filings / concalls:
  Quote only search_filings snippets; cite doc title/date when present

TABLE STYLE
- Prefer GitHub-style markdown tables
- Use Indian number sense when tools return large figures (Cr if already scaled by tools)
- Align columns consistently; put the subject company first in peer tables

TONE
Professional, concise, Fuzz-like. No emoji spam. No filler ("Sure!", "Great question").
"""

S = {"symbol": {"type": "string", "description": "NSE symbol, e.g. RELIANCE"}}


def _t(name, desc, props, req=("symbol",)):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(req)},
        },
    }


TOOLS = [
    _t("results", "Revenue, operating profit and PAT history", {**S, "quarterly": {"type": "boolean"}}),
    _t("ratios", "P/E, P/B, ROCE, ROE, margins, market cap", S),
    _t("peers", "Compare a stock with its sector peer group (IT, banks, metals, etc.)", S),
    _t(
        "compare",
        "Compare an explicit list of NSE symbols side by side",
        {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'NSE symbols e.g. ["TCS","INFY","HCLTECH"]',
            }
        },
        ("symbols",),
    ),
    _t("ownership", "Shareholding (promoter/FII/DII when available)", S),
    _t("news", "Latest company news", S),
    _t("events", "Upcoming result/dividend dates", S),
    _t("outlook", "Upside/downside vs analyst targets", S),
    _t(
        "history",
        "Stock price chart — historical close prices",
        {**S, "period": {"type": "string", "description": "1y, 5y, or max"}},
    ),
    _t(
        "screener",
        "Shortlist stocks by ratio filters",
        {
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string", "enum": data.METRICS},
                        "op": {"type": "string", "enum": list(data.OPS)},
                        "value": {"type": "number"},
                    },
                    "required": ["metric", "op", "value"],
                },
            }
        },
        ("filters",),
    ),
    _t(
        "search_filings",
        "Search local index of announcements, results notes and concall snippets.",
        {
            "query": {"type": "string", "description": "Search text"},
            "symbol": {"type": "string", "description": "Optional NSE symbol to scope the search"},
        },
        ("query",),
    ),
]
ALLOWED = {t["function"]["name"] for t in TOOLS}


def ask(question: str, history: list | None = None):
    trace_id = new_trace_id()
    t0 = time.perf_counter()
    history = history or []
    model = settings.llm_model or ""

    log_event(
        {
            "type": "chat_request",
            "trace_id": trace_id,
            "question": question,
            "history_len": len(history),
            "model": model,
        }
    )

    if not llm.configured():
        ans = "Set LLM_API_KEY and LLM_MODEL in .env to enable chat."
        log_chat_turn(
            trace_id=trace_id,
            question=question,
            history_len=len(history),
            answer=ans,
            blocks=[],
            duration_ms=int((time.perf_counter() - t0) * 1000),
            model=model,
            error="llm_not_configured",
        )
        return {"answer": ans, "blocks": [], "trace_id": trace_id}

    c = llm.client()
    msgs = [{"role": "system", "content": SYSTEM}]
    for h in history[-8:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": question})

    blocks = []
    tool_calls_log = []
    prompt_tokens = completion_tokens = total_tokens = None
    answer = ""
    err = None

    try:
        for round_i in range(4):
            try:
                resp = c.chat.completions.create(
                    model=settings.llm_model,
                    messages=msgs,
                    tools=TOOLS,
                    max_tokens=2000,
                    timeout=90,
                )
            except TypeError:
                resp = c.chat.completions.create(
                    model=settings.llm_model,
                    messages=msgs,
                    tools=TOOLS,
                    max_tokens=2000,
                )

            usage = getattr(resp, "usage", None)
            if usage:
                pt = getattr(usage, "prompt_tokens", None)
                ct = getattr(usage, "completion_tokens", None)
                tt = getattr(usage, "total_tokens", None)
                if pt is not None:
                    prompt_tokens = (prompt_tokens or 0) + pt
                if ct is not None:
                    completion_tokens = (completion_tokens or 0) + ct
                if tt is not None:
                    total_tokens = (total_tokens or 0) + tt

            m = resp.choices[0].message
            if not m.tool_calls:
                answer = m.content or ""
                break

            msgs.append(
                {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": t.id,
                            "type": "function",
                            "function": {"name": t.function.name, "arguments": t.function.arguments},
                        }
                        for t in m.tool_calls
                    ],
                }
            )
            for t in m.tool_calls:
                name = t.function.name
                try:
                    args = json.loads(t.function.arguments or "{}")
                    if name not in ALLOWED:
                        raise ValueError(f"Unknown tool {name}")
                    tool_t0 = time.perf_counter()
                    out = getattr(data, name)(**args)
                    tool_ms = int((time.perf_counter() - tool_t0) * 1000)
                except Exception as e:
                    args, out = {}, {"error": str(e)}
                    tool_ms = 0
                blocks.append({"tool": name, "args": args, "data": out})
                tool_calls_log.append({"round": round_i, "tool": name, "args": args, "ms": tool_ms})
                log_event(
                    {
                        "type": "tool_call",
                        "trace_id": trace_id,
                        "tool": name,
                        "args": args,
                        "ms": tool_ms,
                        "error": out.get("error") if isinstance(out, dict) else None,
                    }
                )
                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": t.id,
                        "content": json.dumps(out, default=str)[:24000],
                    }
                )
        else:
            answer = answer or "I couldn't complete that request."
    except Exception as e:
        err = str(e)
        answer = f"Something went wrong talking to the model or data layer: {e}"

    duration_ms = int((time.perf_counter() - t0) * 1000)
    log_chat_turn(
        trace_id=trace_id,
        question=question,
        history_len=len(history),
        answer=answer,
        blocks=blocks,
        duration_ms=duration_ms,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        tool_calls=tool_calls_log,
        error=err,
    )
    return {"answer": answer, "blocks": blocks, "trace_id": trace_id, "duration_ms": duration_ms}
