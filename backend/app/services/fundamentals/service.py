"""What the company page reads: overview, results, statements, peers, and an on-demand refresh from the provider."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from backend.app.config.settings import settings
from backend.app.repositories import bars_repository as bars, warehouse, watchlist_repository as watchlist
from backend.app.services.fundamentals import compute, loader, peers
from backend.app.services.fundamentals.labels import line_kind, order_key
from backend.app.services.screener import engine as screener_engine

_refresh_lock = threading.Lock()


class NotFound(ValueError):
    pass


class Cooldown(ValueError):
    pass


def _symbol(symbol: str) -> str:
    sym = symbol.upper().strip()
    if sym not in bars.company_map() and not warehouse.get_fundamentals_status(sym):
        raise NotFound(f"'{sym}' is not an NSE symbol we know.")
    return sym


def _fin(sym: str) -> compute.Fin:
    return compute.Fin(warehouse.get_financials_all(sym))


def _coverage(sym: str, fin: compute.Fin) -> dict:
    st = warehouse.get_fundamentals_status(sym) or {}
    return {"has_data": fin.has_data(), "annual_periods": len(fin.periods("income", "annual")), "quarterly_periods": len(fin.periods("income", "quarterly")),
            "statements": sorted(f"{s} ({p})" for s, pts in fin.d.items() for p in pts), "fetched_at": st.get("fetched_at"),
            "source": st.get("source"), "currency": st.get("currency"), "error": st.get("error"), "unit": "INR crore"}


def overview(symbol: str) -> dict:
    sym = _symbol(symbol)
    cmap, st = bars.company_map(), warehouse.get_fundamentals_status(sym)
    fin = _fin(sym)
    look = screener_engine.lookup([sym])
    row = look["rows"][0]
    price = None if row.get("missing") else row.get("LTP")
    sector, industry = peers.sector_text((cmap.get(sym) or {}).get("sector"), st)
    ratios_row = warehouse.get_ratios(sym) or {}
    view = (compute.ratios_view(fin, price, (st or {}).get("shares"), sector, industry, ratios_row.get("market_cap"), ratios_row.get("as_of"))
            if fin.has_data() else None)
    cmp = peers.compare(sym) if fin.has_data() else None
    return {"symbol": sym, "name": (cmap.get(sym) or {}).get("name") or ratios_row.get("name") or sym,
            "sector": (cmap.get(sym) or {}).get("sector"), "industry": (st or {}).get("industry"),
            "price": {"value": price, "change": row.get("Change"), "change_pct": row.get("Day Change %"), "source": row.get("price_source"),
                      "mode": look["mode"], "as_of": look["as_of"], "label": look["source"], "notes": look["notes"]},
            "coverage": _coverage(sym, fin), "ratios": view, "summary": peers.summary_lines(view, cmp),
            "results": compute.results_view(fin, "annual") if fin.has_data() else None, "in_watchlist": sym in watchlist.items()}


def results(symbol: str, period: str = "annual") -> dict:
    sym = _symbol(symbol)
    if period not in ("annual", "quarterly"):
        raise ValueError("period must be annual or quarterly")
    fin = _fin(sym)
    return {"symbol": sym, **compute.results_view(fin, period), "coverage": _coverage(sym, fin)}


def statements(symbol: str, kind: str = "income", period: str = "annual") -> dict:
    sym = _symbol(symbol)
    if kind not in ("income", "balance", "cashflow"):
        raise ValueError("kind must be income, balance or cashflow")
    if period not in ("annual", "quarterly"):
        raise ValueError("period must be annual or quarterly")
    fin = _fin(sym)
    periods = fin.periods(kind, period)
    labels: dict[str, str] = {}
    for p in periods:
        for label in fin.raw(kind, period, p):
            labels[label] = line_kind(label)
    scale = {"money": lambda v: v / compute.CRORE, "per_share": lambda v: v, "count": lambda v: v, "rate": lambda v: v * 100}
    rows = []
    for label in sorted(labels, key=lambda l: order_key(kind, l)):
        vals = {p: round(scale[labels[label]](fin.raw(kind, period, p)[label]), 4) for p in periods if label in fin.raw(kind, period, p)}
        if vals:
            rows.append({"label": label, "kind": labels[label], "values": vals})
    return {"symbol": sym, "statement": kind, "period_type": period, "periods": [{"period": p, "label": compute.label_period(p)} for p in periods],
            "rows": rows, "unit": "INR crore (per-share lines in INR, share counts as reported, rates in %)", "coverage": _coverage(sym, fin)}


def peer_table(symbol: str, limit: int = 6) -> dict:
    return peers.compare(_symbol(symbol), max(1, min(limit, 12)))


def refresh(symbol: str, factory=None) -> dict:
    """Fetch this company's statements from the provider now. Rate-limited: one refresh at a time, and not again within the cooldown."""
    sym = _symbol(symbol)
    st = warehouse.get_fundamentals_status(sym)
    if st and st.get("fetched_at") and not st.get("error") and settings.fundamentals_refresh_cooldown_min > 0:
        try:
            at = datetime.strptime(st["fetched_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            wait = settings.fundamentals_refresh_cooldown_min * 60 - (datetime.now(timezone.utc) - at).total_seconds()
        except ValueError:
            wait = 0
        if wait > 0:
            raise Cooldown(f"{sym} was refreshed recently. Try again in {int(wait // 60) + 1} minute(s).")
    if not _refresh_lock.acquire(blocking=False):
        raise Cooldown("Another refresh is running. Try again in a moment.")
    try:
        return loader.refresh_symbol(sym, factory)
    finally:
        _refresh_lock.release()