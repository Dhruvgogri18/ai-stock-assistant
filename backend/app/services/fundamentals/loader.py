"""Fetch a company's full statements from the provider (yfinance) and store them in the warehouse.

Stores EVERY line the provider returns, annual and quarterly, for income statement, balance sheet and cash flow, under the
provider's own labels. Nothing is renamed or invented; unparseable or empty values are skipped, never stored as zero.
The provider's sector never overwrites the NSE master's (a different taxonomy); it is kept in fundamentals_status.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from backend.app.repositories import warehouse
from backend.app.services.fundamentals import compute

SOURCE = "Yahoo Finance via yfinance (statements as reported by the provider)"
RATIOS_SOURCE = "Computed from stored statements (yfinance)"
MAX_ANNUAL, MAX_QUARTERLY = 6, 10
FRAMES = [("income", "annual", "income_stmt"), ("income", "quarterly", "quarterly_income_stmt"),
          ("balance", "annual", "balance_sheet"), ("balance", "quarterly", "quarterly_balance_sheet"),
          ("cashflow", "annual", "cashflow"), ("cashflow", "quarterly", "quarterly_cashflow")]


def make_ticker(symbol: str):
    """Network object, kept separate so tests can replace it."""
    import yfinance as yf
    return yf.Ticker(f"{symbol.upper()}.NS")


@dataclass
class Fetched:
    symbol: str
    frames: dict[tuple[str, str], dict[str, dict[str, float]]] = field(default_factory=dict)   # (statement, period_type) -> period -> label -> value
    info: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _finite(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_frame(df, limit: int) -> dict[str, dict[str, float]]:
    """yfinance statement table (rows = line labels, columns = period-end timestamps, newest first) -> {period: {label: value}}."""
    if df is None or getattr(df, "empty", True):
        return {}
    out: dict[str, dict[str, float]] = {}
    for col in list(df.columns)[:limit]:
        period = str(col.date()) if hasattr(col, "date") else str(col)[:10]
        items: dict[str, float] = {}
        for label, value in df[col].items():
            v = _finite(value)
            if v is not None and str(label) not in items:
                items[str(label)] = v
        if items:
            out[period] = items
    return out


def fetch(symbol: str, factory=None, pause: float = 0.0) -> Fetched:
    """Pull everything for one company. A failing statement is recorded and skipped; it never aborts the others."""
    t = (factory or make_ticker)(symbol)
    got = Fetched(symbol.upper())
    try:
        got.info = dict(getattr(t, "info", None) or {})
    except Exception as e:  # noqa: BLE001
        got.errors.append(f"info: {type(e).__name__}: {str(e)[:100]}")
    for statement, ptype, attr in FRAMES:
        try:
            rows = parse_frame(getattr(t, attr, None), MAX_ANNUAL if ptype == "annual" else MAX_QUARTERLY)
            if rows:
                got.frames[(statement, ptype)] = rows
        except Exception as e:  # noqa: BLE001
            got.errors.append(f"{statement} {ptype}: {type(e).__name__}: {str(e)[:100]}")
        if pause:
            time.sleep(pause)
    return got


def _price(info: dict) -> float | None:
    for k in ("currentPrice", "regularMarketPrice", "previousClose"):
        v = _finite(info.get(k))
        if v and v > 0:
            return v
    return None


def store(got: Fetched) -> dict:
    """Write what was fetched. Raises ValueError when the provider returned nothing usable."""
    sym, info = got.symbol, got.info
    if not got.frames:
        why = "; ".join(got.errors) or "the provider returned no statements"
        raise ValueError(f"No financial statements found for {sym} ({why}).")
    currency = info.get("financialCurrency") or info.get("currency")
    fetched_at = warehouse.utc_now()
    base = {"fetched_at": fetched_at, "source": SOURCE, "currency": currency,
            "industry": info.get("industry"), "provider_sector": info.get("sector")}
    if currency and currency != "INR":                      # a foreign-currency report cannot be mixed into rupee ratios
        warehouse.upsert_fundamentals_status(sym, **base, error=f"Statements are in {currency}, not INR; not stored.")
        raise ValueError(f"{sym} reports in {currency}, not INR, so its statements were not stored.")

    warehouse.ensure_company(sym, info.get("longName") or info.get("shortName") or "")
    if info.get("industry"):
        warehouse.set_company_industry(sym, info["industry"])
    for (statement, ptype), periods in got.frames.items():
        for period, items in periods.items():
            warehouse.upsert_financial_rows(sym, period, ptype, statement, items)

    fin = compute.Fin(warehouse.get_financials_all(sym))
    shares = _finite(info.get("sharesOutstanding")) or fin.latest("balance", "annual", "shares") or fin.latest("balance", "quarterly", "shares")
    price = _price(info)
    n_annual = len(fin.periods("income", "annual"))
    n_quarterly = len(fin.periods("income", "quarterly"))
    warehouse.upsert_fundamentals_status(sym, **base, shares=shares, price_at_fetch=price, annual_periods=n_annual,
                                         quarterly_periods=n_quarterly, error="; ".join(got.errors) or None)
    # keep the ratios row (read by the screener and the chat's ratios tool) in step with the statements
    view = compute.ratios_view(fin, price, shares, info.get("sector"), info.get("industry"))
    row = compute.ratios_row(view)
    mcap = _finite(info.get("marketCap")) or row.get("market_cap")
    warehouse.upsert_ratios({"symbol": sym, "name": info.get("longName") or info.get("shortName") or sym,
                             "sector": info.get("sector") or "", **{k: v for k, v in row.items() if k != "market_cap"},
                             "market_cap": mcap, "source": RATIOS_SOURCE})
    return {"symbol": sym, "annual_periods": n_annual, "quarterly_periods": n_quarterly, "fetched_at": fetched_at,
            "statements": sorted({f"{s} ({p})" for s, p in got.frames}), "errors": got.errors, "price": price, "shares": shares}


def refresh_symbol(symbol: str, factory=None, pause: float = 0.0) -> dict:
    return store(fetch(symbol, factory, pause))