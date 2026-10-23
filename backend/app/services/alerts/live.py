"""Live ("touch") breakout alerts: during NSE market hours, compare Angel One's last traded price with each rule.

Honesty rules (CLAUDE.md #4): every live notification says it is a live price, from which source, and at what time, and
that it can reverse before the close. If live prices are unavailable the alert is not lost: the end-of-day check
(engine.check_all) judges "touch" alerts from the day's high and low and uses the same dedupe key, so it never doubles up.

A crossing needs two prices. We compare against the last price we saw today, or, on the first look of the day, the previous
stored close. So a level crossed on an earlier day never fires again, and a restart in the middle of the day cannot miss
or repeat a crossing.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, time as dtime

from backend.app.config.settings import settings
from backend.app.repositories import alert_repository as repo, bars_repository as bars, watchlist_repository as watchlist
from backend.app.services.alerts import rules

OPEN, CLOSE = dtime(9, 15), dtime(15, 30)
STALE_DAYS = 5                      # stored prices older than this cannot serve as "previous close"
BACKOFF_START, BACKOFF_MAX = 60, 900
SOURCE = "Angel One SmartAPI"

_lock = threading.Lock()
_last: dict[tuple[str, str], float] = {}          # (ist date, symbol) -> last live price seen
_backoff_until = 0.0
_backoff_for = 0
last_poll: dict = {"status": "never", "at": None}  # shown on the Alerts page


# ------------------------------------------------------------- data access
def configured() -> bool:
    try:
        from backend.app.providers import angel_one
        return angel_one.configured()
    except Exception:  # noqa: BLE001
        return False


def fetch_ltp(symbol: str) -> float:
    """One live price. Network call, kept separate so tests can replace it."""
    from backend.app.providers import angel_one
    return float(angel_one.ltp(symbol)["ltp"])


def now_ist() -> datetime:
    from backend.app.services.data import bhavcopy
    return bhavcopy.now_ist()


def market_open(now: datetime) -> bool:
    """NSE cash session, weekdays 09:15 to 15:30 IST. Exchange holidays are not known here; on a holiday the price does
    not move, so nothing crosses and nothing fires."""
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def live_alerts() -> list[dict]:
    return [a for a in repo.list_alerts(active_only=True)
            if a["kind"] == "breakout" and a["params"].get("trigger") == "touch"]


# ------------------------------------------------------------------- status
def status() -> dict:
    """What the Alerts page shows about live checking."""
    now = now_ist()
    if settings.alerts_live_interval_sec <= 0:
        state, text = "off", "Automatic live checks are off (ALERTS_LIVE_INTERVAL_SEC=0). \"Check now\" still takes a reading during market hours."
    elif not configured():
        state, text = "unconfigured", "Angel One is not configured, so live alerts are checked after the close using the day's high and low."
    elif not market_open(now):
        state, text = "closed", "Live checks run during market hours (09:15-15:30 IST, Mon-Fri)."
    else:
        state, text = "on", "Live checks are running every %d seconds." % settings.alerts_live_interval_sec
    return {"state": state, "text": text, "configured": configured(), "market_open": market_open(now),
            "interval_sec": settings.alerts_live_interval_sec, "last_poll": last_poll, "source": SOURCE}


def last_price(symbol: str) -> tuple[float, str] | None:
    """Most recent live price seen today, for the alert status text."""
    today = now_ist().date().isoformat()
    v = _last.get((today, symbol))
    return (v, last_poll.get("at") or "") if v is not None else None


# --------------------------------------------------------------------- poll
def poll_once(now: datetime | None = None) -> dict:
    """Check every live alert once. Never raises; returns what happened."""
    global _backoff_until, _backoff_for, last_poll
    now = now or now_ist()
    stamp_day, stamp = now.date().isoformat(), now.strftime("%H:%M IST")
    out = {"status": "ok", "at": now.strftime("%Y-%m-%d %H:%M:%S IST"), "symbols": 0, "fired": 0, "errors": [], "skipped": []}
    if not _lock.acquire(blocking=False):
        return {**out, "status": "already_running"}
    try:
        alerts = live_alerts()
        if not alerts:
            out["status"] = "idle"
        elif not market_open(now):
            out["status"] = "closed"
        elif not configured():
            out["status"] = "unconfigured"
        elif time.time() < _backoff_until:
            out["status"] = "backoff"
        else:
            _run(alerts, now, stamp_day, stamp, out)
    except Exception as e:  # noqa: BLE001 - the loop must survive anything
        out.update(status="error", errors=out["errors"] + [f"{type(e).__name__}: {e}"])
    finally:
        _lock.release()
    if out["status"] not in ("already_running",):
        last_poll = {k: out[k] for k in ("status", "at", "symbols", "fired")} | {"errors": out["errors"][:3]}
    return out


def _targets(alerts: list[dict]) -> dict[str, list[dict]]:
    by_symbol: dict[str, list[dict]] = {}
    wl = watchlist.items() if any(not a["symbol"] for a in alerts) else []
    for a in alerts:
        for sym in ([a["symbol"]] if a["symbol"] else wl):
            by_symbol.setdefault(sym, []).append(a)
    return by_symbol


def _run(alerts: list[dict], now: datetime, today: str, stamp: str, out: dict) -> None:
    global _backoff_until, _backoff_for
    by_symbol = _targets(alerts)
    symbols = list(by_symbol)[:max(1, settings.alerts_live_max_symbols)]
    if len(by_symbol) > len(symbols):
        out["skipped"].append(f"{len(by_symbol) - len(symbols)} symbols over the per-poll limit")
    failures = 0
    for n, sym in enumerate(symbols):
        stored = bars.symbol_bars(sym, limit=400)
        if not stored:
            out["skipped"].append(f"{sym}: no stored prices")
            continue
        if stored[-1]["date"] >= today:                      # today's bar already loaded: the session is over
            continue
        age = (date.fromisoformat(today) - date.fromisoformat(stored[-1]["date"])).days
        if age > STALE_DAYS:
            out["skipped"].append(f"{sym}: stored prices are {age} days old")
            continue
        if n and settings.alerts_live_pause_sec > 0:
            time.sleep(settings.alerts_live_pause_sec)
        try:
            ltp = fetch_ltp(sym)
        except Exception as e:  # noqa: BLE001
            failures += 1
            out["errors"].append(f"{sym}: {type(e).__name__}: {str(e)[:120]}")
            continue
        out["symbols"] += 1
        baseline = _last.get((today, sym), stored[-1]["close"])
        for a in by_symbol[sym]:
            res = rules.evaluate_live(a["preset"], a["params"], stored, ltp, baseline, sym, stamp, SOURCE)
            if res.triggered and repo.add_notification(
                    a["id"], "breakout", sym, res.title, res.body, {"preset": a["preset"], **res.facts, "caveat": res.note or None},
                    f"breakout:{a['id']}:{sym}:{today}"):                      # same key the end-of-day check uses
                out["fired"] += 1
        _last[(today, sym)] = ltp
    for k in [k for k in _last if k[0] != today]:           # forget yesterday
        del _last[k]
    if failures and not out["symbols"]:                      # everything failed (login, outage): back off, do not hammer the broker
        _backoff_for = min(BACKOFF_MAX, (_backoff_for or BACKOFF_START // 2) * 2)
        _backoff_until = time.time() + _backoff_for
        out["status"] = "error"
        out["errors"].append(f"Backing off for {_backoff_for}s.")
    else:
        _backoff_for = 0


def reset_state() -> None:
    """For tests."""
    global _backoff_until, _backoff_for, last_poll
    _last.clear(); _backoff_until = 0.0; _backoff_for = 0; last_poll = {"status": "never", "at": None}


# ---------------------------------------------------------------------- loop
class LiveLoop:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, interval_sec: int) -> None:
        if interval_sec <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(interval_sec,), name="alerts-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, interval_sec: int) -> None:
        while not self._stop.is_set():
            try:
                res = poll_once()
                if res["fired"] or res["status"] == "error":
                    print(f"[alerts-live] {res}")
            except Exception as e:  # noqa: BLE001
                print(f"[alerts-live] poll failed: {type(e).__name__}: {e}")
            self._stop.wait(interval_sec)


loop = LiveLoop()