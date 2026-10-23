"""Tools the research agent may call. All figures come from the data layer, never from the model."""
from __future__ import annotations

from typing import Any

from backend.app.services.data import service as data

S = {"symbol": {"type": "string", "description": "NSE symbol, e.g. RELIANCE"}}


def _t(name: str, desc: str, props: dict, req=("symbol",)) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(req)}}}


TOOLS = [
    _t("results", "Revenue, operating profit and PAT history", {**S, "quarterly": {"type": "boolean"}}),
    _t("statements", "Full balance sheet, cash flow or income statement",
       {**S, "kind": {"type": "string", "enum": ["income", "balance", "cashflow"]}}),
    _t("ratios", "P/E, P/B, ROCE, ROE, margins, market cap", S),
    _t("peers", "Compare a stock with its sector peer group (IT, banks, metals, etc.)", S),
    _t("compare", "Compare an explicit list of NSE symbols side by side",
       {"symbols": {"type": "array", "items": {"type": "string"}, "description": 'e.g. ["TCS","INFY","HCLTECH"]'}},
       ("symbols",)),
    _t("ownership", "Shareholding (promoter/FII/DII when available)", S),
    _t("news", "Latest company news", S),
    _t("events", "Upcoming result/dividend dates", S),
    _t("outlook", "Upside/downside vs analyst targets", S),
    _t("history", "Stock price chart: historical close prices",
       {**S, "period": {"type": "string", "description": "1y, 5y, or max"}}),
    _t("market_movers",
       "Market-wide top gainers, top losers, most active, or stocks near a 52-week high/low, from NSE "
       "END-OF-DAY data for ALL listed stocks (last close, not live). Use for 'which stocks are performing "
       "well today', 'top losers', 'most traded stocks', 'stocks hitting 52-week highs'.",
       {"kind": {"type": "string", "enum": ["gainers", "losers", "most_active", "near_52w_high", "near_52w_low"]},
        "limit": {"type": "integer", "description": "rows to return, default 25, max 100"},
        "min_turnover_cr": {"type": "number", "description": "ignore stocks that traded less than this many Rs crore (default 1)"},
        "include_sme": {"type": "boolean", "description": "include NSE SME-series stocks (default true)"},
        "sector": {"type": "string", "description": "optional sector/industry filter, e.g. 'Information Technology'"}},
       ("kind",)),
    _t("technicals",
       "Technical analysis for ONE stock, computed in code from daily candles: EMA, SMA, RSI, MACD, Bollinger Bands, "
       "ATR, plus chart overlays, crossover/trend signals and support-resistance levels. The UI draws the chart with "
       "the indicators on it. Use for ANY request about moving averages, EMA/SMA, RSI, MACD, Bollinger, support or "
       "resistance, 'chart with X', trend or momentum signals. Never calculate indicators yourself.",
       {**S,
        "indicators": {"type": "array", "description": "What to draw. Default: EMA 20 and EMA 50. Max 6.",
                       "items": {"type": "object", "properties": {
                           "type": {"type": "string", "enum": ["ema", "sma", "rsi", "macd", "bollinger", "atr"]},
                           "period": {"type": "integer", "description": "e.g. 10, 20, 50, 200. Omit for macd."}},
                           "required": ["type"]}},
        "period": {"type": "string", "description": "Chart window: 1m, 3m, 6m, 1y, 2y. Default 6m."}}),
    _t("quote", "Last close, day change, volume, delivery %, returns and 52-week range for ONE NSE symbol (end of day)", S),
    _t("screener", "Shortlist stocks by filters on ratios (P/E, ROCE...) and/or price metrics (day change %, "
       "1M/3M/1Y return, turnover, delivery %, distance from 52-week high). Price filters cover ALL NSE stocks.",
       {"filters": {"type": "array", "items": {"type": "object", "properties": {
           "metric": {"type": "string", "enum": data.ALL_METRICS},
           "op": {"type": "string", "enum": list(data.OPS)},
           "value": {"type": "number"}}, "required": ["metric", "op", "value"]}}},
       ("filters",)),
    _t("search_filings", "Search the local index of announcements, results notes and concall snippets.",
       {"query": {"type": "string", "description": "Search text"},
        "symbol": {"type": "string", "description": "Optional NSE symbol to scope the search"}},
       ("query",)),
]
ALLOWED = {t["function"]["name"] for t in TOOLS}

# what the live status line says while a tool runs ("Pulling ratios · TCS…")
_VERB = {
    "results": "Pulling financial results", "statements": "Reading financial statements", "ratios": "Pulling ratios",
    "peers": "Comparing with peers", "compare": "Comparing companies", "ownership": "Checking shareholding",
    "news": "Reading latest news", "events": "Checking upcoming events", "outlook": "Checking analyst targets",
    "history": "Loading price history", "screener": "Running the screener", "market_movers": "Scanning the market",
    "quote": "Getting the latest close", "technicals": "Calculating indicators", "search_filings": "Searching filings",
}


def status_label(name: str, args: dict) -> str:
    subject = args.get("symbol") or args.get("kind") or ", ".join(args.get("symbols") or [])[:40] or args.get("query") or ""
    return f"{_VERB.get(name, name)}" + (f" · {subject}" if subject else "") + "…"


def run_tool(name: str, args: dict[str, Any]) -> dict:
    if name not in ALLOWED:
        raise ValueError(f"Unknown tool {name}")
    return getattr(data, name)(**args)


def llm_view(name: str, out):
    """What the MODEL sees of a tool result. The UI gets the full payload (it draws the charts); the model only
    needs the numbers, and sending whole price arrays would blow the size limit and cut off the signals."""
    if not isinstance(out, dict) or not isinstance(out.get("chart"), dict):
        return out
    ch = out["chart"]
    dates, close = ch.get("dates") or [], ch.get("close") or []
    slim = {k: v for k, v in out.items() if k != "chart"}
    base = {"points": len(dates), "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None}
    if name == "technicals":
        slim["chart"] = {**base, "note": "The UI draws the full series; use indicators, signals, levels and summary."}
        return slim
    if name == "history" and close:
        from backend.app.services.data import technicals as t
        hi, lo = max(range(len(close)), key=close.__getitem__), min(range(len(close)), key=close.__getitem__)
        slim["chart_summary"] = {
            **base, "first_close": close[0], "last_close": close[-1], "high_close": close[hi], "high_date": dates[hi],
            "low_close": close[lo], "low_date": dates[lo], "return_pct": round((close[-1] / close[0] - 1) * 100, 2),
            "data_gaps": t.find_gaps(dates)[:5],
            "note": "The UI draws the chart. Use these figures for the trend read; mention data_gaps if any."}
        return slim
    return out
