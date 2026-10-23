"""Legacy yfinance provider (prototype only — unofficial, limited India fields)."""
from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf


def _sym(s: str) -> str:
    s = s.upper().strip()
    return s if "." in s else s + ".NS"


def _meta() -> dict:
    return {
        "source": "Yahoo Finance via yfinance (prototype)",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _matrix(df, keep=None):
    if df is None or getattr(df, "empty", True):
        return {}
    df = df.copy()
    df.columns = [str(c.date()) if hasattr(c, "date") else str(c) for c in df.columns]
    d = df.astype(object).where(df.notna(), None).to_dict(orient="index")
    return {k: v for k, v in d.items() if not keep or k in keep}


def results(symbol: str, quarterly: bool = False) -> dict:
    t = yf.Ticker(_sym(symbol))
    df = t.quarterly_income_stmt if quarterly else t.income_stmt
    m = _matrix(df, ["Total Revenue", "Operating Income", "Net Income"])
    if not m:
        raise ValueError(f"No data for '{symbol}'. Use an NSE symbol such as RELIANCE.")
    return {"matrix": m, "note": "Net Income = profit after tax (PAT).", **_meta()}


def statements(symbol: str, kind: str = "income") -> dict:
    attr = {"income": "income_stmt", "balance": "balance_sheet", "cashflow": "cashflow"}.get(kind)
    if not attr:
        raise ValueError("kind must be income, balance or cashflow")
    m = _matrix(getattr(yf.Ticker(_sym(symbol)), attr))
    if not m:
        raise ValueError(f"No data for '{symbol}'.")
    return {"matrix": m, **_meta()}


def ratios(symbol: str) -> dict:
    t = yf.Ticker(_sym(symbol))
    i = t.info or {}
    if not i.get("longName"):
        raise ValueError(f"No data for '{symbol}'. Use an NSE symbol such as RELIANCE.")
    roce = None
    try:
        b = t.balance_sheet
        roce = round(float(t.income_stmt.loc["EBIT"].iloc[0] /
                           (b.loc["Total Assets"].iloc[0] - b.loc["Current Liabilities"].iloc[0])) * 100, 2)
    except Exception:
        pass
    pct = lambda x: round(x * 100, 2) if x is not None else None
    kv = {
        "P/E": i.get("trailingPE"), "P/B": i.get("priceToBook"), "ROCE %": roce,
        "ROE %": pct(i.get("returnOnEquity")), "Net margin %": pct(i.get("profitMargins")),
        "Operating margin %": pct(i.get("operatingMargins")), "Market cap": i.get("marketCap"),
    }
    return {"kv": kv, "name": i["longName"], "sector": i.get("sector"), **_meta()}


def ownership(symbol: str) -> dict:
    mh = yf.Ticker(_sym(symbol)).major_holders
    kv = {str(k): v for k, v in mh["Value"].items()} if mh is not None and not mh.empty else {}
    if not kv:
        raise ValueError(f"No ownership data for '{symbol}'.")
    return {
        "kv": kv,
        "note": "yfinance major holders only. Prefer DB/IndianAPI for promoter/FII/DII split.",
        **_meta(),
    }


def news(symbol: str) -> dict:
    rows = []
    for n in (yf.Ticker(_sym(symbol)).news or [])[:10]:
        c = n.get("content", n)
        rows.append({
            "title": c.get("title"),
            "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher"),
            "published": c.get("pubDate") or c.get("providerPublishTime"),
            "url": (c.get("canonicalUrl") or {}).get("url") or c.get("link"),
        })
    if not rows:
        raise ValueError(f"No news for '{symbol}'.")
    return {"rows": rows, **_meta()}


def history(symbol: str, period: str = "5y") -> dict:
    h = yf.Ticker(_sym(symbol)).history(
        period=period, interval="1wk" if period in ("2y", "5y", "10y", "max") else "1d"
    )
    if h is None or h.empty:
        raise ValueError(f"No price history for '{symbol}'.")
    return {
        "chart": {
            "dates": [str(d.date()) for d in h.index],
            "close": [round(float(c), 2) for c in h["Close"]],
        },
        **_meta(),
    }


def events(symbol: str) -> dict:
    cal = yf.Ticker(_sym(symbol)).calendar or {}
    kv = {k: ", ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in cal.items()}
    if not kv:
        raise ValueError(f"No events for '{symbol}'.")
    return {"kv": kv, **_meta()}


def outlook(symbol: str) -> dict:
    i = yf.Ticker(_sym(symbol)).info or {}
    p = i.get("currentPrice")
    if not p:
        raise ValueError(f"No outlook data for '{symbol}'.")
    up = lambda x: round((x / p - 1) * 100, 1) if x else None
    lo, mean, hi = i.get("targetLowPrice"), i.get("targetMeanPrice"), i.get("targetHighPrice")
    return {
        "kv": {
            "Price": p, "Analyst low target": lo, "Analyst mean target": mean, "Analyst high target": hi,
            "Downside to low %": up(lo), "Upside to mean %": up(mean), "Upside to high %": up(hi),
        },
        "note": "Indicative range from analyst targets. Not a forecast.",
        **_meta(),
    }
