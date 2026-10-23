"""Data layer. Every response carries `source` and `as_of` (product principle).

Provider order (DATA_PROVIDER=auto):
  1. Local warehouse (DB) if ratios table is populated
  2. IndianAPI if INDIANAPI_KEY is set
  3. yfinance fallback (prototype)

Swap providers without changing API or UI.
"""
from __future__ import annotations

import contextvars
import operator
import time
from concurrent.futures import ThreadPoolExecutor

from backend.app.config.settings import settings
from backend.app.services.data import market_data
from backend.app.services.data.providers import db_provider, indian_api, yfinance_provider as yf_p

UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "LT", "KOTAKBANK", "AXISBANK", "MARUTI", "SUNPHARMA", "TITAN",
    "ONGC", "NTPC", "WIPRO", "ASIANPAINT", "BAJFINANCE",
]
METRICS = ["P/E", "P/B", "ROCE %", "ROE %", "Net margin %", "Operating margin %"]      # fundamentals (provider)
PRICE_METRICS = market_data.PRICE_METRICS                                               # end-of-day bars (local)
ALL_METRICS = METRICS + PRICE_METRICS
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


def _is_empty(name: str, res) -> bool:
    """A provider that answers 200 but with every metric null is a miss, not data."""
    if name == "ratios" and isinstance(res, dict):
        return not any(v is not None for v in (res.get("kv") or {}).values())
    return False


def _call(name: str, *args, **kwargs):
    """Dispatch to the active provider; fall back down the chain on error OR empty payload.
    The provider that finally answered is recorded in result["provider"] and every miss is
    written to the session log, so 'Not available' is always explainable."""
    from backend.app.services import llm_trace

    mode = _mode()
    errors: list[str] = []

    def mk(label, mod):
        def run():
            if not hasattr(mod, name):
                raise AttributeError(f"{label} has no {name}")
            return getattr(mod, name)(*args, **kwargs)
        run.label = label
        return run

    chain = {"db": ("db", db_provider), "indianapi": ("indianapi", indian_api), "yfinance": ("yfinance", yf_p)}
    first = [mode] + [k for k in ("db", "indianapi", "yfinance") if k != mode]
    for key in first:
        label, mod = chain.get(key, chain["yfinance"])
        try:
            res = mk(label, mod)()
            if _is_empty(name, res):
                raise ValueError("provider returned no metrics")
            if isinstance(res, dict):
                res.setdefault("provider", label)
            return res
        except Exception as e:
            errors.append(f"{label}: {e}")
    if llm_trace.get_session_id():  # only inside a chat request, not during startup warm-up
        llm_trace.log_event("data_provider_failed", tool=name, args=str(args), errors=errors)
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
    """Filings/announcements first, then yfinance headlines."""
    from datetime import datetime, timezone
    try:
        from backend.app import db as _db
        _db.init_db()
        rows = _db.search_filings(symbol.upper(), symbol=symbol.upper(), limit=10)
        if not rows:
            rows = _db.search_filings(symbol, symbol=None, limit=10)
        if rows:
            return {
                "rows": [{
                    "title": r.get("title"),
                    "publisher": r.get("source") or "Exchange filing",
                    "published": r.get("filed_at"),
                    "url": r.get("url"),
                    "snippet": r.get("snippet"),
                } for r in rows],
                "note": "From local filings (python -m scripts.load_announcements).",
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
    """Price series: Angel One (live/history) → bhavcopy EOD → other providers."""
    # 1) Angel One when configured
    try:
        from backend.app.providers import angel_one
        if angel_one.configured():
            return angel_one.history(symbol, period=period)
    except Exception:
        pass
    # 2) Local NSE EOD bars
    try:
        return market_data.history(symbol, period, fallback=lambda: _call("history", symbol, period=period))
    except ValueError:
        return _call("history", symbol, period=period)



def quote(symbol):
    """CMP / last price: Angel One LTP first, then bhavcopy EOD quote."""
    angel_note = None
    try:
        from backend.app.providers import angel_one
        if angel_one.configured():
            try:
                live = angel_one.ltp(symbol)
                ltp = live.get("ltp")
                if ltp is not None:
                    base = {}
                    try:
                        base = market_data.quote(symbol)
                    except Exception:
                        base = {}
                    kv = dict(base.get("kv") or {})
                    kv["LTP"] = float(ltp)
                    kv["CMP"] = float(ltp)
                    src = "Angel One SmartAPI (live LTP)"
                    if base.get("kv"):
                        src += " + NSE bhavcopy (returns / range)"
                    return {
                        **base,
                        "kv": kv,
                        "source": src,
                        "as_of": live.get("as_of") or base.get("as_of"),
                        "note": "CMP/LTP from Angel One. Returns and 52-week range from bhavcopy when available.",
                    }
                angel_note = "Angel One returned no LTP"
            except Exception as e:
                angel_note = f"Angel One LTP failed: {e}"
        else:
            angel_note = "Angel One not configured (set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET)"
    except Exception as e:
        angel_note = f"Angel One unavailable: {e}"

    out = market_data.quote(symbol)
    if angel_note:
        prev = out.get("note") or ""
        out["note"] = (prev + " " if prev else "") + f"[{angel_note}]"
    return out

def _candle_sources(sym: str, need: int, view: int):
    """Yield (label, loader) in priority order: Angel One -> stored NSE bars -> any provider (close only)."""
    def angel():
        from backend.app.providers import angel_one
        if not angel_one.configured():
            raise ValueError("not configured")
        return angel_one.candles(sym, bars=need)

    def stored():
        from backend.app.repositories import bars_repository as bars
        rows = bars.symbol_bars(sym, limit=need)
        if not rows:
            raise ValueError("no stored bars for this symbol")
        return rows

    def provider():
        h = _call("history", sym, period="2y")
        ch = h.get("chart") or {}
        return [{"date": d, "close": c} for d, c in zip(ch.get("dates", []), ch.get("close", []))]

    yield "Angel One SmartAPI (daily candles)", angel
    yield market_data.SOURCE, stored
    yield "Price history provider (close only)", provider


def technicals(symbol, indicators=None, period="6m"):
    """EMA / SMA / RSI / MACD / Bollinger / ATR with chart overlays, signals and support-resistance, computed in code
    from COMPLETED daily candles. History before the chart window is used as warm-up, so lines start correctly."""
    import pandas as pd

    from backend.app.services.data import technicals as tech

    sym = symbol.upper().strip()
    specs = tech.parse_specs(indicators)
    label, view = tech.parse_period(period)
    need = view + tech.warmup_bars(specs)
    enough = view + 50                                  # chart window plus a minimum of warm-up
    best, attempts = None, []
    for source, load in _candle_sources(sym, need, view):
        try:
            rows = load()
        except Exception as e:  # noqa: BLE001 - try the next source
            attempts.append(f"{source}: {str(e)[:160]}")
            continue
        n = len(rows)
        if best is None or n > best[1]:
            best = (source, n, rows)
        if n >= enough:
            break
        attempts.append(f"{source}: only {n} candles")
    if best is None:
        raise ValueError(f"No price history for '{symbol}'. Tried: {'; '.join(attempts)}")

    source, _, rows = best
    try:
        from backend.app.repositories import bars_repository as bars
        name = (bars.company_map().get(sym) or {}).get("name") or sym
    except Exception:  # noqa: BLE001
        name = sym
    out = tech.build(pd.DataFrame(rows), specs, view, label, name)
    out["data_quality"]["source_attempts"] = attempts
    last = out["summary"]["latest_date"]
    return {
        "symbol": sym, "name": name, **out, "source": source, "as_of": f"{last} (market close)",
        "note": "Indicators use completed daily candles only: a candle still forming today is excluded, so values are as "
                f"of the {last} close. They are lagging indicators, shown for context, not advice.",
    }


def market_movers(kind="gainers", limit=25, min_turnover_cr=None, include_sme=True, sector=None):
    """Market-wide gainers / losers / most active / near 52-week high or low (end of day, local data)."""
    return market_data.movers(kind, limit, min_turnover_cr, include_sme, sector)


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


def _load_universe_rows() -> list[dict]:
    # copy_context so llm_trace's session/request ids follow the work into pool threads
    with ThreadPoolExecutor(6) as pool:
        futures = [pool.submit(contextvars.copy_context().run, _safe, s) for s in UNIVERSE]
        return [r for r in (f.result() for f in futures) if r]


def universe_rows():
    if _mode() == "db":
        rows = db_provider.list_universe_rows()
        if rows:
            return rows
    return cached("universe_rows", _load_universe_rows, ttl=900)


def _meta_of(r: dict) -> dict:
    return {"source": r.get("source", "mixed"), "as_of": r.get("as_of")}


def peers(symbol):
    sym = symbol.upper()
    base = cached("r:" + sym, lambda: ratios(sym))
    me = {"symbol": sym, "name": base.get("name"), "sector": base.get("sector"), **base["kv"]}
    rows = [r for r in universe_rows() if r.get("sector") == me.get("sector") and r["symbol"] != sym]
    return {
        "rows": [me] + rows,
        "note": f"Peers = same sector within the loaded universe ({len(UNIVERSE)} symbols in default list).",
        **_meta_of(base),
    }


def compare(symbols):
    """Explicit side-by-side of a list of NSE symbols."""
    syms = [s.upper().strip() for s in (symbols or []) if s and s.strip()][:10]
    if len(syms) < 2:
        raise ValueError("compare needs at least two symbols")
    rows, missing, meta = [], [], {}
    for s in syms:
        try:
            r = cached("r:" + s, lambda s=s: ratios(s))
            rows.append({"symbol": s, "name": r.get("name"), "sector": r.get("sector"), **r["kv"]})
            meta = meta or _meta_of(r)
        except Exception as e:
            missing.append({"symbol": s, "error": str(e)[:200]})
    if not rows:
        raise ValueError("No data for any of: " + ", ".join(syms))
    out = {"rows": rows, **meta}
    if missing:
        out["missing"] = missing
    return out


def screener(filters):
    """Filter on fundamentals (provider data, default list) and/or price metrics (all NSE stocks, end of day).
    Price-only filters never touch a provider API. Mixed filters are limited to stocks that have both."""
    ok = lambda r, f: r.get(f["metric"]) is not None and OPS[f["op"]](r[f["metric"]], f["value"])
    uses_px = any(f["metric"] in PRICE_METRICS for f in filters)
    uses_fund = any(f["metric"] in METRICS for f in filters)
    note = None
    if uses_px:
        base = market_data.screen_rows()                      # raises NoMarketData with a clear hint if empty
        meta = market_data.screen_meta()
        if uses_fund:
            fund = {r["symbol"]: r for r in universe_rows()}
            base = [{**r, **{m: fund[r["symbol"]].get(m) for m in METRICS}} for r in base if r["symbol"] in fund]
            note = f"Fundamental filters only cover the {len(fund)}-stock default universe."
        rows = [r for r in base if all(ok(r, f) for f in filters)]
        rows.sort(key=lambda r: r.get("Turnover (Cr)") or 0, reverse=True)
        out = {"rows": rows[:100], "total_matched": len(rows), "filters": filters,
               **{k: v for k, v in meta.items()}}
        if meta.get("price_mode") == "live" and any(f["metric"] == "Delivery %" for f in filters):
            note = " ".join(x for x in (note, "Delivery % is published only after the close, so no stock passes a Delivery % filter while prices are live.") if x)
        if note or meta.get("note") or meta.get("live_note"):
            out["note"] = " ".join(x for x in (note, meta.get("note"), meta.get("live_note")) if x)
        return out
    rows = [r for r in universe_rows() if all(ok(r, f) for f in filters)]
    sample = rows[0] if rows else {}
    return {
        "rows": rows,
        "total_matched": len(rows),
        "filters": filters,
        "source": "Local warehouse / provider universe",
        "as_of": sample.get("as_of") or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
    }


# ---------- RAG bridge (filings) ----------
def search_filings(query: str, symbol: str | None = None) -> dict:
    from backend.app.services.data import rag
    return rag.search(query, symbol=symbol)