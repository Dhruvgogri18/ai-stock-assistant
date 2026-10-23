"""Upcoming results and dividend dates for a set of stocks.

Two sources, in order of trust:
  1. the warehouse `events` table: confirmed dates put there by a loader (shown as confirmed);
  2. the data provider's calendar (yfinance): projections, always labelled "estimated".
A symbol with nothing from either is listed under `unavailable` with the reason; it is never silently dropped.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from backend.app.repositories import bars_repository as bars, warehouse

LABELS = {"results": "Results", "dividend_ex": "Ex-dividend date", "dividend_pay": "Dividend payment",
          "board_meeting": "Board meeting", "agm": "AGM", "other": "Event"}
WAREHOUSE_SOURCE = "Exchange announcements (warehouse events)"
PROVIDER_SOURCE = "Yahoo Finance calendar (estimates)"
NOTE = ("Dates marked estimated are a data provider's projection, not a company announcement. "
        "Confirm results dates on the exchange before relying on them.")
TTL_OK, TTL_ERR = 6 * 3600, 15 * 60

_cache: dict[str, tuple[float, list[dict] | None, str | None]] = {}
_lock = threading.Lock()


def today() -> date:
    from backend.app.services.data import bhavcopy
    return bhavcopy.now_ist().date()


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def parse_provider_calendar(cal: dict, floor: date) -> list[dict]:
    """yfinance `Ticker.calendar` -> events on or after `floor`. Pure function (no network)."""
    out: list[dict] = []
    e = (cal or {}).get("Earnings Date")
    dates = sorted(d for d in (_d(x) for x in (e if isinstance(e, (list, tuple)) else [e])) if d)
    if dates and dates[0] >= floor:
        out.append({"type": "results", "date": dates[0].isoformat(),
                    "date_end": dates[-1].isoformat() if dates[-1] != dates[0] else None, "estimated": True})
    for key, typ in (("Ex-Dividend Date", "dividend_ex"), ("Dividend Date", "dividend_pay")):
        d = _d((cal or {}).get(key))
        if d and d >= floor:
            out.append({"type": typ, "date": d.isoformat(), "date_end": None, "estimated": True})
    return out


def fetch_provider_calendar(symbol: str) -> dict:
    """Network call, kept separate so tests can replace it."""
    import yfinance as yf
    return yf.Ticker(f"{symbol.upper()}.NS").calendar or {}


def _provider_events(symbol: str, floor: date) -> tuple[list[dict] | None, str | None]:
    now = time.time()
    with _lock:
        hit = _cache.get(symbol)
    if hit and now - hit[0] < (TTL_OK if hit[2] is None else TTL_ERR):
        return hit[1], hit[2]
    try:
        events, err = parse_provider_calendar(fetch_provider_calendar(symbol), floor), None
        if not events:
            err = "The data provider lists no upcoming results or dividend date for this stock."
    except Exception as e:  # noqa: BLE001 - report, do not crash the page
        events, err = None, f"Could not reach the data provider ({type(e).__name__})."
    with _lock:
        _cache[symbol] = (now, events, err)
    return events, err


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _warehouse_events(symbols: list[str], start: date, end: date) -> list[dict]:
    out = []
    for r in warehouse.list_events(symbols, start.isoformat(), end.isoformat()):
        title = None
        try:
            meta = json.loads(r["detail"] or "")
            title = meta.get("title") if isinstance(meta, dict) else None
        except ValueError:
            title = r["detail"] or None
        typ = r["event_type"] if r["event_type"] in LABELS else "other"
        out.append({"symbol": r["symbol"], "type": typ, "date": r["event_date"][:10], "date_end": None,
                    "estimated": False, "title": title, "source": WAREHOUSE_SOURCE})
    return out


def get_calendar(symbols: list[str], days: int = 60) -> dict:
    days = max(1, min(int(days or 60), 180))
    symbols = list(dict.fromkeys(s.upper().strip() for s in symbols if s and s.strip()))[:60]
    start = today()
    end = start + timedelta(days=days)
    events = _warehouse_events(symbols, start, end)
    confirmed_results = {e["symbol"] for e in events if e["type"] == "results"}
    errors: dict[str, str] = {}
    used_provider = False

    # The provider is asked for every symbol (it is the only source of dividend dates), but a provider *estimate*
    # of a results date never overrides a confirmed one from the warehouse.
    if symbols:
        with ThreadPoolExecutor(6) as pool:
            futs = {s: pool.submit(contextvars.copy_context().run, _provider_events, s, start) for s in symbols}
            for s, f in futs.items():
                got, err = f.result()
                for ev in got or []:
                    if ev["date"] > end.isoformat() or (ev["type"] == "results" and s in confirmed_results):
                        continue
                    if any(x["symbol"] == s and x["type"] == ev["type"] and x["date"] == ev["date"] for x in events):
                        continue
                    events.append({**ev, "symbol": s, "title": None, "source": PROVIDER_SOURCE})
                    used_provider = True
                if err and not got:
                    errors[s] = err

    have = {e["symbol"] for e in events}
    names = bars.company_map()
    for e in events:
        e["name"] = (names.get(e["symbol"]) or {}).get("name") or e["symbol"]
        e["label"] = LABELS.get(e["type"], "Event")
    events.sort(key=lambda e: (e["date"], e["symbol"], e["type"]))
    sources = []
    if any(not e["estimated"] for e in events):
        sources.append({"source": WAREHOUSE_SOURCE, "as_of": None})
    if used_provider:
        sources.append({"source": PROVIDER_SOURCE, "as_of": start.isoformat()})
    return {"as_of": start.isoformat(), "from": start.isoformat(), "to": end.isoformat(), "days": days, "symbols": symbols,
            "events": events, "unavailable": [{"symbol": s, "reason": errors.get(s) or "No upcoming events found."}
                                              for s in symbols if s not in have],
            "sources": sources, "notes": [NOTE] if any(e["estimated"] for e in events) else []}
