"""Serve Phase-1 metrics from the local warehouse (SQLite/Postgres)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .. import db


def _meta(source: str | None = None) -> dict:
    return {
        "source": source or "Local warehouse (NSE/BSE loader)",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def available() -> bool:
    try:
        db.init_db()
        return db.ratios_count() > 0
    except Exception:
        return False


def results(symbol: str, quarterly: bool = False) -> dict:
    period_type = "quarterly" if quarterly else "annual"
    rows = db.get_financials(symbol, "income", period_type)
    if not rows:
        raise ValueError(f"No financials in warehouse for '{symbol}'. Run: python -m scripts.load_data")
    # pivot to matrix: line_item → {period: value}
    matrix: dict[str, dict] = defaultdict(dict)
    for r in rows:
        matrix[r["line_item"]][r["period"]] = r["value"]
    keep = {"Total Revenue", "Operating Income", "Net Income", "Revenue", "PAT", "EBIT"}
    filtered = {k: v for k, v in matrix.items() if k in keep} or dict(matrix)
    return {
        "matrix": filtered,
        "note": "From local warehouse. Net Income = PAT where labeled.",
        **_meta(),
    }


def statements(symbol: str, kind: str = "income") -> dict:
    if kind not in ("income", "balance", "cashflow"):
        raise ValueError("kind must be income, balance or cashflow")
    rows = db.get_financials(symbol, kind, "annual")
    if not rows:
        raise ValueError(f"No {kind} statement in warehouse for '{symbol}'.")
    matrix: dict[str, dict] = defaultdict(dict)
    for r in rows:
        matrix[r["line_item"]][r["period"]] = r["value"]
    return {"matrix": dict(matrix), **_meta()}


def ratios(symbol: str) -> dict:
    row = db.get_ratios(symbol)
    if not row:
        raise ValueError(f"No ratios in warehouse for '{symbol}'. Run: python -m scripts.load_data")
    kv = {
        "P/E": row.get("pe"),
        "P/B": row.get("pb"),
        "ROCE %": row.get("roce"),
        "ROE %": row.get("roe"),
        "Net margin %": row.get("net_margin"),
        "Operating margin %": row.get("op_margin"),
        "Market cap": row.get("market_cap"),
    }
    return {
        "kv": kv,
        "name": row.get("name") or symbol.upper(),
        "sector": row.get("sector"),
        **_meta(row.get("source")),
    }


def ownership(symbol: str) -> dict:
    rows = db.get_shareholding(symbol)
    if not rows:
        raise ValueError(f"No shareholding in warehouse for '{symbol}'.")
    # latest period only
    latest = rows[0]["period"]
    kv = {r["holder_type"]: r["pct"] for r in rows if r["period"] == latest}
    return {
        "kv": kv,
        "note": f"Shareholding pattern for period {latest} (promoter/FII/DII/public).",
        **_meta(),
    }


def history(symbol: str, period: str = "5y") -> dict:
    rows = db.get_prices(symbol)
    if not rows:
        raise ValueError(f"No prices in warehouse for '{symbol}'.")
    return {
        "chart": {
            "dates": [r["date"] for r in rows],
            "close": [r["close"] for r in rows],
        },
        **_meta(),
    }


def list_universe_rows() -> list[dict]:
    rows = db.list_ratio_rows()
    out = []
    for r in rows:
        out.append({
            "symbol": r["symbol"],
            "name": r.get("name"),
            "sector": r.get("sector"),
            "P/E": r.get("pe"),
            "P/B": r.get("pb"),
            "ROCE %": r.get("roce"),
            "ROE %": r.get("roe"),
            "Net margin %": r.get("net_margin"),
            "Operating margin %": r.get("op_margin"),
            "Market cap": r.get("market_cap"),
        })
    return out
