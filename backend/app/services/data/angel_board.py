"""No-database mode: the whole-market price table comes from Angel One quotes instead of stock.db (NSE bhavcopy).

Used automatically when no end-of-day bars are stored and Angel One is configured. A background loop polls every
NSE equity (-EQ) in batches of 50 into the shared live board (live_prices._board); `snapshot()` turns that board into the
same table the Screener, Movers and Watchlist already read.

Not available in this mode (Angel quotes carry no history): 1M/3M/1Y returns and Delivery %. 52-week high/low are
filled in only if Angel returns them in the quote.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd

from backend.app.config.settings import settings
from backend.app.services.data import live_prices as lp, market_data

MAX_BOARD_AGE_SEC = 36 * 3600          # after the close, the last quote is still "the price"
OPEN_INTERVAL_SEC, CLOSED_INTERVAL_SEC = 90, 30 * 60
_state = {"passes": 0, "seconds": None, "at": None, "error": None, "total": 0}
_stop = threading.Event()
_thread: threading.Thread | None = None


def available() -> bool:
    return bool(settings.prices_live and lp.configured())


def symbols() -> list[str]:
    from backend.app.providers import angel_one
    return sorted(s for s, (_tok, ts) in angel_one.token_index().items() if ts.endswith("-EQ"))


def refresh_pass() -> int:
    """One sweep over every equity. Returns how many quotes were stored."""
    syms = symbols()
    _state["total"] = len(syms)
    started, got = time.time(), 0
    for i in range(0, len(syms), lp.QUOTE_MAX):
        if _stop.is_set() or lp.blocked_for():
            break
        try:
            found, _ = lp._call(syms[i:i + lp.QUOTE_MAX])
        except Exception as e:  # noqa: BLE001
            lp._failed(e)
            _state["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            print(f"[angel-board] {_state['error']}", flush=True)
            break
        lp._store(found)
        lp._ok()
        got += len(found)
    else:
        _state.update(error=None, seconds=round(time.time() - started, 1), at=datetime.now().strftime("%H:%M:%S"))
        _state["passes"] += 1
    return got


def _loop() -> None:
    while not _stop.is_set():
        wait = CLOSED_INTERVAL_SEC
        try:
            if available() and not lp.blocked_for():
                wait = OPEN_INTERVAL_SEC if lp.market_open(lp.now_ist()) else CLOSED_INTERVAL_SEC
                with lp._board_lock:
                    empty = len(lp._board) < 200
                if lp.market_open(lp.now_ist()) or empty or _state["passes"] == 0:
                    refresh_pass()
            else:
                wait = 30
        except Exception as e:  # noqa: BLE001 - the loop must survive anything
            print(f"[angel-board] {type(e).__name__}: {e}", flush=True)
        _stop.wait(wait)


def start_if_needed() -> bool:
    """Start the loop when there is no stored market data and Angel One can supply it."""
    global _thread
    from backend.app.repositories import bars_repository as bars
    try:
        has_bars = bars.latest_date() is not None
    except Exception:  # noqa: BLE001
        has_bars = False
    if has_bars or not available() or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="angel-board", daemon=True)
    _thread.start()
    print("[angel-board] no stock.db data: building the market table from Angel One quotes", flush=True)
    return True


def stop() -> None:
    _stop.set()


def snapshot() -> market_data.Snapshot:
    now = time.time()
    with lp._board_lock:
        items = {s: dict(q) for s, q in lp._board.items() if now - q["at"] <= MAX_BOARD_AGE_SEC}
    if not items:
        raise market_data.NoMarketData("The live market table is still loading from Angel One (the first pass takes about a "
                                       "minute). Try again shortly.")
    df = pd.DataFrame.from_dict(items, orient="index")
    out = pd.DataFrame(index=df.index)
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df else pd.Series(np.nan, index=df.index)  # noqa: E731
    prev = num("prev_close").where(lambda x: x > 0)
    out["series"] = "EQ"
    out["LTP"], out["Prev Close"] = num("price"), prev
    out["Open"], out["High"], out["Low"] = num("open"), num("high"), num("low")
    out["Change"] = out["LTP"] - prev
    out["Day Change %"] = out["Change"] / prev * 100
    out["Volume"] = num("volume")
    out["Turnover (Cr)"] = num("volume") * num("avg_price") / 1e7
    out["Delivery %"] = np.nan
    for c in ("1M Return %", "3M Return %", "1Y Return %"):
        out[c] = np.nan
    hi, lo = num("w52_high"), num("w52_low")
    out["52W High"], out["52W Low"] = hi.where(hi > 0), lo.where(lo > 0)
    out["% From 52W High"] = (out["LTP"] / out["52W High"] - 1) * 100
    out["% From 52W Low"] = (out["LTP"] / out["52W Low"] - 1) * 100
    out["name"], out["sector"], out["in_master"] = out.index, "—", False
    oldest = int(now - min(q["at"] for q in items.values()))
    open_now = lp.market_open(lp.now_ist())
    return market_data.Snapshot(
        as_of=lp.now_ist().date(), n_dates=0, df=out, has_master=False, mode="live",
        live_at=datetime.now().strftime("%H:%M:%S"), live_note="Prices from Angel One only (no stored history).",
        live_progress=(len(items), _state["total"] or len(items)), live_oldest_sec=oldest, live_market_open=open_now)


def status() -> dict:
    return dict(_state, running=bool(_thread and _thread.is_alive()))