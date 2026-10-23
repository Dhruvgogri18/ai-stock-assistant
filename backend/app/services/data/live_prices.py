"""Live prices: Angel One first, NSE bhavcopy as the fallback.

The price flow
    market_data.snapshot()  ->  the stored end-of-day snapshot (bhavcopy)  ->  live_prices.overlay()  ->  live snapshot
    watchlist               ->  live_prices.get_quotes(its symbols)         ->  apply_quotes()         ->  live rows

When live prices apply (in_window): on a weekday from 09:15 IST until that day's bhavcopy has been stored. After that the
bhavcopy IS the day's final price, so it is used and labelled end of day. Before 09:15, weekends, or with Angel One not
configured / failing, everything falls back to bhavcopy, and every result says which one it is (CLAUDE.md rule 4).

Rules that keep the numbers honest
  * a stock is either live or not live. In the market-wide overlay a stock with no fresh quote gets NO price (it drops out
    of price filters) instead of showing yesterday's value next to today's;
  * the market-wide overlay is only used when at least PRICES_MIN_COVERAGE of stocks have a fresh quote, otherwise the whole
    screen stays on end-of-day and says "live prices are loading (n of N)";
  * day change is measured against the exchange's own previous close from the quote (adjusted on corporate-action days);
  * Delivery % is published only after the close, so it is empty while prices are live.

Rate limits: ONE request at a time, at least PRICES_BATCH_PAUSE_SEC apart, shared by every caller. After a failure with no
data at all, calls back off (1 to 15 minutes) so wrong credentials cannot hammer the broker login.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from datetime import date, datetime, time as dtime

import numpy as np
import pandas as pd

from backend.app.config.settings import settings
from backend.app.services.data import market_data

OPEN, CLOSE = dtime(9, 15), dtime(15, 30)
BACKOFF_START, BACKOFF_MAX = 60, 900
SOURCE = "Angel One SmartAPI"
QUOTE_MAX = 50
LIVE_COLUMNS = ["LTP", "Prev Close", "Open", "High", "Low", "Change", "Day Change %", "Volume", "Turnover (Cr)", "Delivery %",
                "1M Return %", "3M Return %", "1Y Return %", "52W High", "52W Low", "% From 52W High", "% From 52W Low"]

_board: dict[str, dict] = {}                 # symbol -> {price, prev_close, open, high, low, volume, avg_price, feed_time, at}
_version = 0                                  # bumps whenever the board changes (cache key for the overlay)
_board_lock = threading.Lock()
_gap_lock = threading.Lock()
_last_call = [0.0]
_fail = {"until": 0.0, "for": 0, "error": None}
_demand_at = [0.0]
_cycle = {"seconds": None, "at": None}
_universe_cache: tuple[market_data.Snapshot, list[str]] | None = None
_overlay_cache: tuple[market_data.Snapshot, int, str, market_data.Snapshot] | None = None


# ------------------------------------------------------------ replaceable I/O
def configured() -> bool:
    try:
        from backend.app.providers import angel_one
        return angel_one.configured()
    except Exception:  # noqa: BLE001
        return False


def fetch_chunk(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """One Angel One batch request (up to 50 symbols). Network call, kept separate so tests can replace it."""
    from backend.app.providers import angel_one
    return angel_one.quote_chunk(symbols)


def now_ist() -> datetime:
    from backend.app.services.data import bhavcopy
    return bhavcopy.now_ist()


# ------------------------------------------------------------------ windows
def market_open(now: datetime) -> bool:
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def in_window(now: datetime, latest_stored: date | None) -> bool:
    """Live prices apply from 09:15 on a weekday until that day's bhavcopy is stored (then it is final)."""
    return bool(settings.prices_live and now.weekday() < 5 and now.time() >= OPEN
                and (latest_stored is None or latest_stored < now.date()))


# --------------------------------------------------------- throttle + backoff
def blocked_for() -> int:
    return max(0, int(_fail["until"] - time.time()))


def _failed(err: Exception) -> None:
    _fail["for"] = min(BACKOFF_MAX, (_fail["for"] or BACKOFF_START // 2) * 2)
    _fail["until"] = time.time() + _fail["for"]
    _fail["error"] = f"{type(err).__name__}: {str(err)[:160]}"


def _ok() -> None:
    _fail.update(until=0.0, **{"for": 0}, error=None)


def _call(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """The only way anything in this module talks to Angel One: serialised, and spaced by PRICES_BATCH_PAUSE_SEC."""
    with _gap_lock:
        wait = settings.prices_batch_pause_sec - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            return fetch_chunk(symbols)
        finally:
            _last_call[0] = time.time()


def _store(found: dict[str, dict]) -> None:
    global _version
    now = time.time()
    with _board_lock:
        for sym, f in found.items():
            _board[sym] = {"price": f["ltp"], "prev_close": f.get("prev_close"), "open": f.get("open"), "high": f.get("high"),
                           "low": f.get("low"), "volume": f.get("volume"), "avg_price": f.get("avg_price"),
                           "feed_time": f.get("feed_time"), "at": now}
        _version += 1


def _age(q: dict) -> float:
    return time.time() - q["at"]


# ----------------------------------------------------------------- quotes
def get_quotes(symbols: list[str], ttl: int | None = None) -> tuple[dict[str, dict], dict]:
    """Quotes for these symbols: cached ones younger than `ttl` are reused, the rest are fetched (50 per request).
    Never raises. Returns ({symbol: quote}, {"errors": [...], "missing": [...]}); a quote older than PRICES_MAX_AGE_SEC is dropped."""
    ttl = settings.prices_ttl_sec if ttl is None else ttl
    syms = list(dict.fromkeys(s.upper().strip() for s in symbols if s and s.strip()))
    info: dict = {"errors": [], "missing": []}
    need = [s for s in syms if s not in _board or _age(_board[s]) > ttl]
    if need:
        if not settings.prices_live:
            pass
        elif not configured():
            info["errors"].append("Angel One is not configured")
        elif blocked_for():
            info["errors"].append(f"Angel One is failing; retrying in {blocked_for()}s ({_fail['error']})")
        else:
            for i in range(0, len(need), QUOTE_MAX):
                try:
                    found, missing = _call(need[i:i + QUOTE_MAX])
                except Exception as e:  # noqa: BLE001
                    _failed(e)
                    info["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")
                    break
                _store(found)
                info["missing"] += missing
                _ok()
    with _board_lock:
        fresh = {s: dict(_board[s]) for s in syms if s in _board and _age(_board[s]) <= settings.prices_max_age_sec}
    return fresh, info


# ------------------------------------------------------------ overlay maths
def apply_quotes(df: pd.DataFrame, quotes: dict[str, dict], universe: pd.Series | None = None, clear_missing: bool = False) -> pd.DataFrame:
    """Copy of an end-of-day snapshot frame with live prices applied to every row that has a quote.
    clear_missing: rows in `universe` without a quote lose their price columns (market-wide screens); otherwise they keep
    their end-of-day values (the watchlist shows them as end of day)."""
    df = df.copy()
    uni = pd.Series(True, index=df.index) if universe is None else universe.reindex(df.index).fillna(False)

    def col(key: str) -> pd.Series:
        return pd.Series({s: q.get(key) for s, q in quotes.items()}, dtype="float64").reindex(df.index)

    price, prev = col("price"), col("prev_close")
    has = price.notna() & uni
    if clear_missing:
        df.loc[uni & ~has, LIVE_COLUMNS] = np.nan
    if not has.any():
        return df
    prev = prev.where(prev > 0)
    df.loc[has, "LTP"] = price[has]
    df.loc[has, "Prev Close"] = prev[has]
    for k, name in (("open", "Open"), ("high", "High"), ("low", "Low")):
        df.loc[has, name] = col(k)[has]
    df.loc[has, "Change"] = (price - prev)[has]
    df.loc[has, "Day Change %"] = ((price - prev) / prev * 100)[has]
    vol, avg = col("volume"), col("avg_price")
    df.loc[has, "Volume"] = vol[has]
    df.loc[has, "Turnover (Cr)"] = (vol * avg / 1e7)[has]          # value traded so far today, from volume x average price
    df.loc[has, "Delivery %"] = np.nan                              # published only after the close
    for name, ref in (("1M Return %", "_b1m"), ("3M Return %", "_b3m"), ("1Y Return %", "_b1y")):
        if ref in df:
            df.loc[has, name] = ((price / df[ref] - 1) * 100)[has]
    hi_today, lo_today = col("high"), col("low")
    hi = df["52W High"].combine(hi_today, np.fmax)
    lo = df["52W Low"].combine(lo_today, np.fmin)
    df.loc[has & df["52W High"].notna(), "52W High"] = hi[has & df["52W High"].notna()]
    df.loc[has & df["52W Low"].notna(), "52W Low"] = lo[has & df["52W Low"].notna()]
    df.loc[has, "% From 52W High"] = ((price / df["52W High"] - 1) * 100)[has]
    df.loc[has, "% From 52W Low"] = ((price / df["52W Low"] - 1) * 100)[has]
    return df


def _universe(base: market_data.Snapshot) -> pd.Series:
    m = base.df["series"].isin(market_data.EQUITY_SERIES + market_data.SME_SERIES)
    return m & base.df["in_master"] if base.has_master else m


def universe_symbols(base: market_data.Snapshot) -> list[str]:
    global _universe_cache
    if _universe_cache is None or _universe_cache[0] is not base:
        _universe_cache = (base, list(base.df.index[_universe(base)]))
    return _universe_cache[1]


def overlay(base: market_data.Snapshot) -> market_data.Snapshot | None:
    """The live version of the end-of-day snapshot, or `base` plus a note when live prices are not ready, or None when live
    prices do not apply at all (outside the window / switched off)."""
    global _overlay_cache
    now = now_ist()
    if not in_window(now, base.as_of):
        return None
    if not configured():
        return dataclasses.replace(base, live_note="Angel One is not configured, so prices are end of day (NSE bhavcopy).")
    demand()
    syms = universe_symbols(base)
    with _board_lock:
        fresh = {s: dict(_board[s]) for s in syms if s in _board and _age(_board[s]) <= settings.prices_max_age_sec}
    total = len(syms)
    if total and len(fresh) / total < settings.prices_min_coverage:
        if blocked_for():
            note = (f"Live prices are unavailable: Angel One is failing ({_fail['error']}). Showing end-of-day prices (NSE bhavcopy); "
                    f"retrying in {blocked_for()}s.")
        else:
            note = (f"Live prices are loading ({len(fresh):,} of {total:,} stocks). Showing end-of-day prices (NSE bhavcopy) until enough are in.")
        return dataclasses.replace(base, live_progress=(len(fresh), total), live_note=note)
    minute = now.strftime("%H%M")
    if _overlay_cache and _overlay_cache[0] is base and _overlay_cache[1] == _version and _overlay_cache[2] == minute:
        return _overlay_cache[3]
    df = apply_quotes(base.df, fresh, _universe(base), clear_missing=True)
    ages = [_age(q) for q in fresh.values()]
    stamp = datetime.fromtimestamp(max(q["at"] for q in fresh.values()), now.tzinfo).strftime("%H:%M:%S IST") if fresh else ""
    note = None
    if total - len(fresh):
        note = (f"{total - len(fresh):,} stocks have no live price right now and are left out of price filters.")
    snap = dataclasses.replace(base, df=df, mode="live", live_at=stamp, live_note=note, live_progress=(len(fresh), total),
                               live_oldest_sec=int(max(ages)) if ages else None, live_market_open=market_open(now))
    _overlay_cache = (base, _version, minute, snap)
    return snap


# ------------------------------------------------------------- refresher
def demand() -> None:
    """Someone is looking at market-wide prices: keep the board warm for a while."""
    _demand_at[0] = time.time()


def refresh_universe_once() -> dict:
    """One pass over the whole market, stalest first, stopping early when nobody is looking any more."""
    base = market_data.eod_snapshot()
    syms = universe_symbols(base)
    started, done, errors = time.time(), 0, []
    with _board_lock:
        order = sorted(syms, key=lambda s: _board[s]["at"] if s in _board else 0.0)
    for i in range(0, len(order), QUOTE_MAX):
        if time.time() - _demand_at[0] > settings.prices_demand_window_sec or blocked_for():
            break
        try:
            found, _ = _call(order[i:i + QUOTE_MAX])
        except Exception as e:  # noqa: BLE001
            _failed(e)
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            break
        _store(found)
        _ok()
        done += len(found)
    else:
        _cycle.update(seconds=round(time.time() - started, 1), at=datetime.now().strftime("%H:%M:%S"))
    return {"quotes": done, "errors": errors}


class Refresher:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.prices_live or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="live-prices", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            idle = True
            try:
                base = market_data.eod_snapshot()
                if (time.time() - _demand_at[0] < settings.prices_demand_window_sec and configured() and not blocked_for()
                        and in_window(now_ist(), base.as_of)):
                    idle = False
                    refresh_universe_once()
            except market_data.NoMarketData:
                pass
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                print(f"[live-prices] refresh failed: {type(e).__name__}: {e}")
            self._stop.wait(2 if idle else 0.2)


refresher = Refresher()


# ------------------------------------------------------------------ status
def status() -> dict:
    now = now_ist()
    try:
        base = market_data.eod_snapshot()
        latest, syms = base.as_of, universe_symbols(base)
    except market_data.NoMarketData:
        latest, syms = None, []
    with _board_lock:
        fresh = sum(1 for s in syms if s in _board and _age(_board[s]) <= settings.prices_max_age_sec)
    window = in_window(now, latest)
    if not settings.prices_live:
        state, text = "off", "Live prices are switched off (PRICES_LIVE=false); using NSE bhavcopy (end of day)."
    elif not configured():
        state, text = "unconfigured", "Angel One is not configured; using NSE bhavcopy (end of day)."
    elif not window:
        state, text = "eod", "Live prices run on weekdays from 09:15 IST until the day's bhavcopy is stored; using NSE bhavcopy (end of day) now."
    elif blocked_for():
        state, text = "failing", f"Angel One is failing ({_fail['error']}); using NSE bhavcopy. Retrying in {blocked_for()}s."
    else:
        state, text = "live", "Live prices from Angel One."
    return {"state": state, "text": text, "configured": configured(), "window": window, "market_open": market_open(now),
            "coverage": {"fresh": fresh, "total": len(syms)}, "backoff_sec": blocked_for(), "last_error": _fail["error"],
            "last_cycle": dict(_cycle), "source": SOURCE}


def reset_state() -> None:
    """For tests."""
    global _version, _universe_cache, _overlay_cache
    with _board_lock:
        _board.clear()
        _version += 1
    _fail.update(until=0.0, **{"for": 0}, error=None)
    _last_call[0] = 0.0
    _demand_at[0] = 0.0
    _cycle.update(seconds=None, at=None)
    _universe_cache = _overlay_cache = None