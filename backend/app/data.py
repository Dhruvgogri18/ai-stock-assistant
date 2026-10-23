"""Data layer. Every response carries `source` and `as_of` (product principle).

Provider order (DATA_PROVIDER=auto):
  1. Local warehouse (DB) if ratios table is populated
  2. IndianAPI if INDIANAPI_KEY is set
  3. yfinance fallback (prototype)

Swap providers without changing API or UI.
"""
from __future__ import annotations

import operator
import time
from concurrent.futures import ThreadPoolExecutor

from .config import settings
from .providers import db_provider, indian_api, yfinance_provider as yf_p

UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "LT", "KOTAKBANK", "AXISBANK", "MARUTI", "SUNPHARMA", "TITAN",
    "ONGC", "NTPC", "WIPRO", "ASIANPAINT", "BAJFINANCE",
]
METRICS = ["P/E", "P/B", "ROCE %", "ROE %", "Net margin %", "Operating margin %"]
OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}
_cache: dict = {}


def cached(key, fn, ttl=3600):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _mode() -> str:
    m = (settings.data_provider or "auto").lower()
    if m != "auto":
        return m
    if db_provider.available():
        return "db"
    if indian_api.configured():
        return "indianapi"
    return "yfinance"


def _call(name: str, *args, **kwargs):
    """Dispatch to active provider with graceful fallback."""
    mode = _mode()
    errors = []

    def try_db():
        return getattr(db_provider, name)(*args, **kwargs)

    def try_indian():
        if not hasattr(indian_api, name):
            raise AttributeError(f"IndianAPI has no {name}")
        return getattr(indian_api, name)(*args, **kwargs)

    def try_yf():
        return getattr(yf_p, name)(*args, **kwargs)

    order = {
        "db": [try_db, try_indian, try_yf],
        "indianapi": [try_indian, try_db, try_yf],
        "yfinance": [try_yf, try_db, try_indian],
    }.get(mode, [try_db, try_indian, try_yf])

    for fn in order:
        try:
            return fn()
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
            continue
    raise ValueError(f"No data for this request. Tried: {'; '.join(errors)}")


# ---------- Phase 1 public API (stable signatures) ----------
def results(symbol, quarterly=False):
    return _call("results", symbol, quarterly=quarterly)


def statements(symbol, kind="income"):
    return _call("statements", symbol, kind=kind)


def ratios(symbol):
    return _call("ratios", symbol)


def ownership(symbol):
    return _call("ownership", symbol)


def news(symbol):
    """Prefer local BSE/NSE filings; fall back to yfinance headlines."""
    from datetime import datetime, timezone
    try:
        from . import db as _db
        _db.init_db()
        rows = _db.search_filings(symbol.upper(), symbol=symbol.upper(), limit=10)
        if not rows:
            # broader keyword search
            rows = _db.search_filings(f"{symbol}", symbol=None, limit=10)
        if rows:
            return {
                "rows": [
                    {
                        "title": r.get("title"),
                        "publisher": r.get("source") or "Exchange filing",
                        "published": r.get("filed_at"),
                        "url": r.get("url"),
                        "snippet": r.get("snippet"),
                    }
                    for r in rows
                ],
                "note": "From local filings index (run: python -m scripts.load_announcements).",
                "source": "BSE/NSE announcements (warehouse)",
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            }
    except Exception:
        pass
    try:
        return yf_p.news(symbol)
    except Exception as e:
        raise ValueError(str(e))


def history(symbol, period="5y"):
    """Angel One → bhavcopy EOD → other providers."""
    try:
        from .providers import angel_one
        if angel_one.configured():
            return angel_one.history(symbol, period=period)
    except Exception:
        pass
    try:
        from backend.app.services.data import market_data as md
        h = md.history(symbol, period=period)
        if h and h.get("chart", {}).get("close"):
            return h
    except Exception:
        pass
    return _call("history", symbol, period=period)


def quote(symbol):
    """CMP: Angel LTP first, else bhavcopy EOD."""
    try:
        from .providers import angel_one
        if angel_one.configured():
            live = angel_one.ltp(symbol)
            if live.get("ltp") is not None:
                from backend.app.services.data import market_data as md
                base = {}
                try:
                    base = md.quote(symbol)
                except Exception:
                    base = {}
                kv = dict(base.get("kv") or {})
                kv["LTP"] = float(live["ltp"])
                kv["CMP"] = float(live["ltp"])
                return {
                    **base,
                    "kv": kv,
                    "source": "Angel One SmartAPI (LTP)" + (" + NSE bhavcopy" if base else ""),
                    "as_of": live.get("as_of") or base.get("as_of"),
                    "note": "CMP from Angel One live LTP.",
                }
    except Exception:
        pass
    from backend.app.services.data import market_data as md
    return md.quote(symbol)


def events(symbol):
    try:
        return yf_p.events(symbol)
    except Exception as e:
        raise ValueError(str(e))


def outlook(symbol):
    try:
        return yf_p.outlook(symbol)
    except Exception as e:
        raise ValueError(str(e))


def _row(s):
    r = cached("r:" + s, lambda: ratios(s))
    return {"symbol": s, "name": r.get("name"), "sector": r.get("sector"), **r["kv"]}


def _safe(s):
    try:
        return _row(s)
    except Exception:
        return None


def universe_rows():
    if _mode() == "db":
        rows = db_provider.list_universe_rows()
        if rows:
            return rows
    return cached("universe_rows", lambda: [r for r in ThreadPoolExecutor(6).map(_safe, UNIVERSE) if r], ttl=900)


def peers(symbol):
    me = _row(symbol.upper())
    rows = [r for r in universe_rows() if r.get("sector") == me.get("sector") and r["symbol"] != me["symbol"]]
    return {
        "rows": [me] + rows,
        "note": f"Peers = same sector within the loaded universe ({len(UNIVERSE)} symbols in default list).",
        "source": me.get("source") if False else ratios(symbol).get("source", "mixed"),
        "as_of": ratios(symbol).get("as_of"),
    }


def screener(filters):
    ok = lambda r, f: r.get(f["metric"]) is not None and OPS[f["op"]](r[f["metric"]], f["value"])
    rows = [r for r in universe_rows() if all(ok(r, f) for f in filters)]
    sample = rows[0] if rows else {}
    return {
        "rows": rows,
        "filters": filters,
        "source": "Local warehouse / provider universe",
        "as_of": sample.get("as_of") or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
    }


# ---------- RAG bridge (filings) ----------
def search_filings(query: str, symbol: str | None = None) -> dict:
    from . import rag
    return rag.search(query, symbol=symbol)
