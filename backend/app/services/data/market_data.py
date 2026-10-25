"""Price-based answers: live Angel One prices when they apply, otherwise the local NSE end-of-day bars (bhavcopy).

    snapshot()      one row per stock for the latest trading day, with returns and 52-week range
    movers()        top gainers / losers / most active / near 52-week high or low
    quote()         one stock: last close, day change, volume, delivery %, 52-week range
    history()       close-price series for charts
    screen_rows()   rows the screener can filter on price metrics

snapshot() is the end-of-day snapshot with live prices laid over it when services/data/live_prices says they apply (weekday,
from 09:15 IST until that day's bhavcopy is stored, Angel One configured and enough of the market answering). Otherwise it IS
the end-of-day snapshot, "as of the last close", and says so. Returns and 52-week figures are computed from stored
closes/highs/lows, which are NOT adjusted for splits or bonuses; the day change uses the exchange's own
adjusted previous close, so it is correct on corporate-action days.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import date

import pandas as pd

from backend.app.config.settings import settings
from backend.app.repositories import bars_repository as bars

SOURCE = "NSE bhavcopy (end-of-day)"
PRICE_METRICS = ["Day Change %", "LTP", "Volume", "Turnover (Cr)", "Delivery %", "1M Return %", "3M Return %",
                 "1Y Return %", "52W High", "52W Low", "% From 52W High", "% From 52W Low"]
MOVER_KINDS = ["gainers", "losers", "most_active", "near_52w_high", "near_52w_low"]
EQUITY_SERIES = ["EQ", "BE", "BZ"]
SME_SERIES = ["SM", "ST"]
# trading days of history each derived metric needs before we will report it
NEED = {"1M": 22, "3M": 64, "1Y": 251, "52W": 245}
MASTER_MIN_ROWS = 500          # only restrict to "listed equities" when the equity master is loaded
PERIOD_DAYS = {"1m": 21, "3m": 63, "6m": 126, "1y": 250, "2y": 500, "3y": 750, "5y": 1250, "10y": 2500, "max": None}


class NoMarketData(ValueError):
    """Bars have not been loaded yet."""


@dataclass
class Snapshot:
    as_of: date                # the last stored trading day (bhavcopy), also for a live snapshot
    n_dates: int
    df: pd.DataFrame           # indexed by symbol
    has_master: bool
    mode: str = "eod"          # "eod" | "live"
    live_at: str | None = None
    live_note: str | None = None                  # why live prices are not in use / what is missing
    live_progress: tuple[int, int] | None = None  # (stocks with a fresh live price, stocks in the market)
    live_oldest_sec: int | None = None
    live_market_open: bool = False


_lock = threading.Lock()
_snap: Snapshot | None = None
_snap_key: tuple | None = None


def invalidate() -> None:
    global _snap, _snap_key
    with _lock:
        _snap, _snap_key = None, None


def latest_date() -> date | None:
    try:
        return bars.latest_date()
    except Exception:
        return None


def latest_date_str() -> str | None:
    d = latest_date()
    return d.isoformat() if d else None


def _clean(v, nd: int = 2):
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, nd)
    return v


# ---------------------------------------------------------------- snapshot
def snapshot() -> Snapshot:
    """What every price answer reads: live when live prices apply, otherwise end of day (never raises because live failed)."""
    base = eod_snapshot()
    if base.n_dates == 0:                              # already a live Angel One table
        return base
    try:
        from backend.app.services.data import live_prices
        return live_prices.overlay(base) or base
    except Exception:  # noqa: BLE001 - a live-price problem must never take the end-of-day answer down
        return base


def eod_snapshot() -> Snapshot:
    """One row per stock from the stored bhavcopy bars, for the latest stored trading day."""
    global _snap, _snap_key
    latest = bars.latest_date()
    if latest is None:
        from backend.app.services.data import angel_board
        if angel_board.available():
            return angel_board.snapshot()              # no stock.db: build the table from Angel One quotes
        raise NoMarketData("Market data has not been loaded yet. Run: python -m scripts.refresh_bhavcopy "
                           "(the app also loads it in the background after startup).")
    key = (latest, bars.count_bars())
    with _lock:
        if _snap is not None and _snap_key == key:
            return _snap

    inp = bars.snapshot_inputs(260)
    dates, last, piv = inp["dates"], inp["last"], inp["piv"]
    n = len(dates)
    latest_s = dates[-1]

    out = pd.DataFrame(index=last.index)
    out["series"] = last["series"]
    out["LTP"] = last["close"]
    out["Prev Close"] = last["prev_close"]
    out["Open"], out["High"], out["Low"] = last["open"], last["high"], last["low"]
    out["Change"] = last["close"] - last["prev_close"]
    out["Day Change %"] = (out["Change"] / last["prev_close"]) * 100
    out["Volume"] = last["volume"]
    out["Turnover (Cr)"] = last["turnover_cr"]
    out["Delivery %"] = last["deliv_pct"]

    def ret(k: int, need: int) -> pd.Series:
        if n < need:
            return pd.Series(float("nan"), index=out.index)
        base = piv.iloc[-(k + 1)]
        return ((piv.iloc[-1] / base - 1) * 100).reindex(out.index)

    out["1M Return %"], out["3M Return %"], out["1Y Return %"] = ret(21, NEED["1M"]), ret(63, NEED["3M"]), ret(250, NEED["1Y"])

    def live_base(k: int, need: int) -> pd.Series:      # close k sessions before TODAY, when today's price is live and last stored = yesterday
        return (piv.iloc[-k] if n >= need else pd.Series(float("nan"), index=piv.columns)).reindex(out.index)

    out["_b1m"], out["_b3m"], out["_b1y"] = live_base(21, NEED["1M"]), live_base(63, NEED["3M"]), live_base(250, NEED["1Y"])
    if n >= NEED["52W"]:
        hi, lo = bars.range_52w(dates[-min(n, 250)])
        out["52W High"], out["52W Low"] = hi.reindex(out.index), lo.reindex(out.index)
        out["% From 52W High"] = (out["LTP"] / out["52W High"] - 1) * 100
        out["% From 52W Low"] = (out["LTP"] / out["52W Low"] - 1) * 100
    else:
        for c in ("52W High", "52W Low", "% From 52W High", "% From 52W Low"):
            out[c] = float("nan")

    cm = bars.company_map()
    out["name"] = [cm.get(s, {}).get("name") or s for s in out.index]
    out["sector"] = [cm.get(s, {}).get("sector") or "—" for s in out.index]
    out["in_master"] = [s in cm and bool(cm[s].get("name")) for s in out.index]
    has_master = len(cm) >= MASTER_MIN_ROWS
    snap = Snapshot(date.fromisoformat(latest_s), n, out, has_master)
    with _lock:
        _snap, _snap_key = snap, key
    return snap


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    rows = []
    for sym, r in df.iterrows():
        row = {"symbol": sym, "name": r["name"], "sector": r["sector"]}
        for c in cols:
            row[c] = _clean(r[c].item() if hasattr(r[c], "item") else r[c])
        rows.append(row)
    return rows


def _meta(s: Snapshot) -> dict:
    if s.mode == "live" and s.n_dates == 0:
        return {"source": "Angel One SmartAPI (market quotes)", "as_of": f"{s.live_at} ({'live' if s.live_market_open else 'latest price, market closed'})",
                "data_date": s.as_of.isoformat(), "price_mode": "live"}
    if s.mode == "live":
        age = f", up to {s.live_oldest_sec}s old" if s.live_oldest_sec is not None else ""
        state = "live" if s.live_market_open else "latest price, market closed"
        return {"source": "Angel One SmartAPI (live prices) + NSE bhavcopy (history)", "as_of": f"{s.live_at} ({state}{age})",
                "data_date": s.as_of.isoformat(), "price_mode": "live"}
    return {"source": SOURCE, "as_of": f"{s.as_of.isoformat()} (market close)", "data_date": s.as_of.isoformat(), "price_mode": "eod"}


def _history_note(s: Snapshot) -> str | None:
    if s.n_dates == 0:
        return "Return and 52-week figures are unavailable: prices come from Angel One quotes with no stored history."
    if s.n_dates < NEED["52W"]:
        return (f"Only {s.n_dates} trading days of history are stored, so 1Y return and 52-week figures are "
                f"unavailable. Load more with: python -m scripts.refresh_bhavcopy --days 400")
    return None


# ------------------------------------------------------------------ movers
def movers(kind: str = "gainers", limit: int = 25, min_turnover_cr: float | None = None, include_sme: bool = True,
           sector: str | None = None) -> dict:
    if kind not in MOVER_KINDS:
        raise ValueError(f"kind must be one of {MOVER_KINDS}")
    s = snapshot()
    df = s.df
    min_t = settings.movers_min_turnover_cr if min_turnover_cr is None else float(min_turnover_cr)
    series = EQUITY_SERIES + (SME_SERIES if include_sme else [])
    df = df[df["series"].isin(series)]
    if s.has_master:
        df = df[df["in_master"]]                       # drops ETFs, bonds, REITs not in the equity master
    if sector:
        df = df[df["sector"].str.contains(sector, case=False, na=False)]
    universe = len(df)
    df = df[df["Turnover (Cr)"].fillna(0) >= min_t]

    note = None
    if kind in ("near_52w_high", "near_52w_low"):
        col = "% From 52W High" if kind == "near_52w_high" else "% From 52W Low"
        if df[col].notna().sum() == 0:
            raise ValueError(_history_note(s) or "52-week data unavailable")
        df = df[(df[col] >= -2) if kind == "near_52w_high" else (df[col] <= 2)]
        df = df.sort_values("Turnover (Cr)", ascending=False)
    else:
        df = df[df["Day Change %"].notna()]
        if kind == "gainers":
            df = df[df["Day Change %"] > 0].sort_values("Day Change %", ascending=False)
        elif kind == "losers":
            df = df[df["Day Change %"] < 0].sort_values("Day Change %", ascending=True)
        else:
            df = df.sort_values("Turnover (Cr)", ascending=False)

    total = len(df)
    limit = max(1, min(int(limit or 25), 100))
    cols = ["LTP", "Change", "Day Change %", "Volume", "Turnover (Cr)", "Delivery %"]
    if kind.startswith("near_52w"):
        cols += ["52W High", "52W Low", "% From 52W High" if kind.endswith("high") else "% From 52W Low"]
    return {
        "kind": kind,
        "rows": _records(df.head(limit), cols),
        "total_matched": total,
        "columns": ["Turnover (Cr)", "Delivery %"] + (cols[6:] if len(cols) > 6 else []),
        "filters": {"min_turnover_cr": min_t, "series": series, "equities_only": s.has_master, "sector": sector,
                    "stocks_before_turnover_filter": universe},
        "note": note or (("Live prices from Angel One: day change vs the exchange's previous close, turnover is value traded so far today "
                          "and Delivery % is published only after the close. " if s.mode == "live" else
                          "Day change = close vs the exchange's previous close (adjusted for splits/bonus). End-of-day data, not live. ")
                         + "Stocks that traded less than the minimum turnover are excluded." + (f" {s.live_note}" if s.live_note else "")),
        **_meta(s),
    }


# ------------------------------------------------------------------- quote
def quote(symbol: str) -> dict:
    s = snapshot()
    sym = symbol.upper().strip()
    if sym not in s.df.index:
        raise ValueError(f"No end-of-day data for '{symbol}' on {s.as_of} (not traded, suspended, or wrong symbol).")
    r = s.df.loc[sym]
    keys = ["LTP", "Change", "Day Change %", "Prev Close", "Open", "High", "Low", "Volume", "Turnover (Cr)",
            "Delivery %", "1M Return %", "3M Return %", "1Y Return %", "52W High", "52W Low", "% From 52W High"]
    kv = {k: _clean(float(r[k])) for k in keys}
    out = {"kv": kv, "name": r["name"], "sector": r["sector"], "series": r["series"], **_meta(s)}
    note = _history_note(s)
    if note:
        out["note"] = note
    return out


# ----------------------------------------------------------------- history
def history(symbol: str, period: str = "5y", fallback=None) -> dict:
    """Close series from stored bars. If we hold far less than asked and a fallback provider exists, try it first."""
    p = (period or "5y").lower().replace("mo", "m").replace("yr", "y")
    want = PERIOD_DAYS.get(p, 1250)
    rows = bars.symbol_bars(symbol, limit=want or 5000)
    if not rows:
        if fallback:
            return fallback()
        raise ValueError(f"No stored prices for '{symbol}'.")
    short = want is not None and len(rows) < 0.8 * want
    if short and fallback:
        try:
            return fallback()
        except Exception:                                # provider down/over quota: serve what we have
            pass
    meta = {"source": SOURCE, "as_of": f"{rows[-1]['date']} (market close)"}
    note = (f"Only {len(rows)} trading days stored (asked for {p}). Load more with: "
            f"python -m scripts.refresh_bhavcopy --days 400") if short else None
    return {"chart": {"dates": [r["date"] for r in rows], "close": [r["close"] for r in rows]},
            **({"note": note} if note else {}), **meta}


# ---------------------------------------------------------------- screener
def screen_rows() -> list[dict]:
    """Every stock with all price metrics (equities + SME, no turnover floor): the screener decides."""
    s = snapshot()
    df = s.df[s.df["series"].isin(EQUITY_SERIES + SME_SERIES)]
    if s.has_master:
        df = df[df["in_master"]]
    return _records(df, ["Change", *PRICE_METRICS])


def screen_meta() -> dict:
    s = snapshot()
    m = _meta(s)
    note = _history_note(s)
    return {**m, **({"note": note} if note else {}), **({"live_note": s.live_note} if s.live_note else {}),
            **({"live_progress": list(s.live_progress)} if s.live_progress else {})}


def status() -> dict:
    d = latest_date()
    return {"latest_date": d.isoformat() if d else None, "days_stored": len(bars.distinct_dates()),
            "bars": bars.count_bars()}