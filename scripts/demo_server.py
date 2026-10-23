"""Run the full app WITHOUT an LLM key or market-data keys, using a canned model + canned data.

    python -m scripts.demo_server          # http://127.0.0.1:8001

Handy for UI work and for seeing what logs/sessions/<id>.log looks like. Uses a throwaway
database and log folder under ./demo_data, so your real chats are untouched.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import uvicorn

from backend.app.config.settings import settings

settings.session_db_path = "demo_data/sessions.db"
settings.db_path = "demo_data/stock.db"          # synthetic market lives here, not in your real warehouse
settings.bhavcopy_auto_refresh = False
settings.llm_log_dir = "demo_data/logs"
settings.llm_api_key, settings.llm_model = "demo", "demo-model"
settings.user_name = settings.user_name or "Investor"

from backend.app.services.llm import gateway_client as gw  # noqa: E402
from backend.app.services.orchestration import tools  # noqa: E402

_real_run_tool = tools.run_tool


def seed_market() -> None:
    """A fake but realistic NSE day (fictional companies) so market_movers / quote / price screens work offline."""
    import random
    from datetime import date, timedelta

    from backend.app.repositories import bars_repository as bars
    from backend.app.services.data import bhavcopy, market_data

    if bars.count_bars():
        return
    rnd = random.Random(7)
    sectors = ["Information Technology", "Financial Services", "Healthcare", "Chemicals", "Metals", "Consumer Goods", "Power"]
    syms = [f"DEMO{i:02d}" for i in range(1, 61)]
    days, d = [], date.today()
    while len(days) < 300:          # a year+ of history so indicators (EMA 50, RSI, MACD...) have warm-up
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    px = {s: rnd.uniform(20, 3000) for s in syms}
    for day in days:
        lines = ["SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"]
        for i, s in enumerate(syms):
            prev = px[s]
            move = rnd.gauss(0.2, 2.5) if day == days[-1] else rnd.gauss(0, 1.5)
            if day == days[-1] and i == 3:
                move = 105.0                          # an illiquid +105% like the Fuzz example
            close = round(prev * (1 + move / 100), 2)
            lacs = round(rnd.uniform(0.04, 1.2) if i == 3 else rnd.uniform(30, 9000) / (1 + i / 10), 2)
            ser = "SM" if i % 17 == 5 else "EQ"
            lines.append(f"{s}, {ser}, {day:%d-%b-%Y}, {prev:.2f}, {prev:.2f}, {max(prev, close):.2f}, {min(prev, close):.2f}, {close}, {close}, {close}, {int(lacs * 1e5 / close)}, {lacs}, {rnd.randint(50, 9000)}, 1000, {rnd.randint(20, 90)}")
            px[s] = close
        bhavcopy.ingest_day(day, "\n".join(lines).encode())
    bars.upsert_companies([{"symbol": s, "name": f"Demo Company {i + 1:02d}", "sector": sectors[i % len(sectors)]} for i, s in enumerate(syms)])
    market_data.invalidate()


seed_market()

ROWS = [
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT", "P/E": 24.1, "ROCE %": 58.2},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT", "P/E": 23.4, "ROCE %": 38.9},
    {"symbol": "HCLTECH", "name": "HCL Technologies", "sector": "IT", "P/E": 22.8, "ROCE %": 27.5},
    {"symbol": "ITC", "name": "ITC", "sector": "FMCG", "P/E": 24.9, "ROCE %": 36.7},
    {"symbol": "NTPC", "name": "NTPC", "sector": "Power", "P/E": 17.2, "ROCE %": 15.6},
]
ANSWER = """# 🔎 Stocks with P/E below 25 and ROCE above 15

> Five large caps pass both filters. **IT services** dominate, and **TCS** has the highest ROCE at 58.2%.

---

## 📋 Shortlist

| Company | Sector | P/E | ROCE % |
|---|---|---:|---:|
| TCS | IT | 24.1 | 58.2 |
| Infosys | IT | 23.4 | 38.9 |
| HCL Technologies | IT | 22.8 | 27.5 |
| ITC | FMCG | 24.9 | 36.7 |
| NTPC | Power | 17.2 | 15.6 |

---

## 🔍 What stands out

- **IT services lead** the list, with three of five names.
- **NTPC is the cheapest** on P/E (17.2) but has the lowest ROCE (15.6%).
- **ROCE** (return on capital employed) shows how much operating profit a company earns per rupee of capital used.

<follow_ups>
["How do TCS and Infosys compare on margins?", "Show TCS price chart for 5 years"]
</follow_ups>"""


def movers_answer(d: dict) -> str:
    rows = d["rows"][:10]
    f = d["filters"]
    table = "\n".join(f"| {i} | {r['name']} | {r['sector']} | {r['LTP']:,.2f} | {r['Change']:+,.2f} | {r['Day Change %']:+.1f}% |" for i, r in enumerate(rows, 1))
    return f"""# 📈 {"Top Losers" if d["kind"] == "losers" else "Top Gainers"} · {d["data_date"]} close

> Showing the latest end-of-day close ({d["data_date"]}), not live prices. Stocks that traded under ₹{f["min_turnover_cr"]:g} crore are excluded. (Demo data.)

---

## 🏆 Top 10

| Rank | Stock | Sector | LTP (₹) | Change (₹) | % Change |
|---:|---|---|---:|---:|---:|
{table}

---

## 🔍 What stands out

- **{rows[0]["name"]}** leads with {rows[0]["Day Change %"]:+.1f}%.
- **{d["total_matched"]} stocks** matched after the turnover filter.
- ⚠️ Single-day moves in small caps can reflect low liquidity or circuit limits. Research before acting.

<follow_ups>
["Show the top losing stocks", "Which stocks traded the most today?"]
</follow_ups>"""


def tech_args(q: str) -> dict:
    import re
    m = re.search(r"demo\d\d", q)
    sym = m.group(0).upper() if m else "DEMO01"
    inds = [{"type": "ema", "period": int(n)} for n in re.findall(r"(\d+)\s*[- ]?(?:day )?ema", q)]
    inds += [{"type": "sma", "period": int(n)} for n in re.findall(r"(\d+)\s*[- ]?(?:day )?sma", q)]
    for k in ("rsi", "macd", "bollinger", "atr"):
        if k in q:
            inds.append({"type": k})
    per = re.search(r"(\d+)\s*(?:months?|m\b)", q)
    return {"symbol": sym, "indicators": inds or [{"type": "ema", "period": 20}, {"type": "ema", "period": 50}],
            "period": f"{per.group(1)}m" if per else "6m"}


def tech_answer(d: dict) -> str:
    s, ind = d["summary"], d["indicators"]
    rows = "\n".join(f"| {k} | {v:,.2f} |" for k, v in ind.items() if v is not None)
    obs = "\n".join(f"- **{x['name']}**: {x['detail']}" for x in d["signals"][:5])
    return f"""# 📈 {d["name"]}: indicators ({d["data_date"] if "data_date" in d else s["latest_date"]} close)

> Values are as of the {s["latest_date"]} close; today's forming candle is excluded. (Demo data.)

---

## 📊 Latest values

| Metric | Value |
|---|---:|
| Latest close | {s["latest_close"]:,.2f} |
| Window high | {s["window_high_close"]:,.2f} ({s["window_high_date"]}) |
| Window low | {s["window_low_close"]:,.2f} ({s["window_low_date"]}) |
| Window return | {s["window_return_pct"]:+.1f}% |
{rows}

---

## 🔍 What stands out

{obs}

<follow_ups>
["What are the key support and resistance levels?", "Show the RSI and MACD too"]
</follow_ups>"""


class DemoGateway:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        time.sleep(1.2)
        msgs, offered = kw["messages"], kw.get("tools")
        system = msgs[0]["content"]
        user_q = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "").split("New user message:\n")[-1].lower()
        techs = any(w in user_q for w in ("ema", "sma", "rsi", "macd", "bollinger", "moving average", "indicator"))
        movers = (not techs) and any(w in user_q for w in ("perform", "gainer", "loser", "today", "top stock", "moving"))
        if "route turns" in system:
            return self._resp(json.dumps({"route": "research", "topic": "Screener Results", "title": next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "Chat").split("New user message:\n")[-1][:40].rstrip("?"), "rationale": "screen"}))
        if techs and offered and msgs[-1]["role"] == "user":
            return self._resp(None, [("technicals", tech_args(user_q))])
        if techs and msgs[-1]["role"] == "tool":
            return self._resp(tech_answer(json.loads(msgs[-1]["content"])))
        if movers and offered and msgs[-1]["role"] == "user":
            return self._resp(None, [("market_movers", {"kind": "losers" if "loser" in user_q else "gainers", "limit": 50})])
        if movers and msgs[-1]["role"] == "tool":
            return self._resp(movers_answer(json.loads(msgs[-1]["content"])))
        if offered and msgs[-1]["role"] == "user":
            calls = [("screener", {"filters": [{"metric": "P/E", "op": "<", "value": 25}, {"metric": "ROCE %", "op": ">", "value": 15}]}),
                     ("history", {"symbol": "TCS", "period": "5y"})]
            return self._resp(None, calls)
        return self._resp(ANSWER)

    @staticmethod
    def _resp(content, calls=None):
        tcs = [SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls or [])]
        msg = SimpleNamespace(content=content, tool_calls=tcs or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls" if tcs else "stop")],
                               usage=SimpleNamespace(prompt_tokens=900, completion_tokens=250), model="demo-model")


def demo_tool(name, args):
    if name in ("market_movers", "quote", "technicals"):
        return _real_run_tool(name, args)          # real code path over the seeded demo market
    time.sleep(0.6)
    meta = {"source": "Demo data", "as_of": time.strftime("%Y-%m-%d"), "provider": "demo"}
    if name == "screener":
        return {"rows": ROWS, "filters": args["filters"], **meta}
    if name == "history":
        import math
        close = [round(2800 + i * 9 + 120 * math.sin(i / 6), 2) for i in range(120)]
        return {"chart": {"dates": [f"2025-{1 + i // 11:02d}-{1 + i % 11:02d}" for i in range(120)], "close": close}, **meta}
    return {"kv": {"P/E": 24.1}, **meta}


gw._client = lambda: DemoGateway()
tools.run_tool = demo_tool

if __name__ == "__main__":
    from backend.app.main import app
    uvicorn.run(app, host="127.0.0.1", port=8001)
