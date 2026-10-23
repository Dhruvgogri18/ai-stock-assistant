"""Create alerts and run them against stored data.

check_all() is idempotent: breakout rules only look at bars newer than the alert's last evaluated date, and every
notification carries a dedupe key, so running it twice (background loop + "Check now") never notifies twice.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

from backend.app.repositories import alert_repository as repo, bars_repository as bars, warehouse
from backend.app.repositories import watchlist_repository as watchlist
from backend.app.services.alerts import rules

MAX_ALERTS = 50
MAX_CATCHUP_DAYS = 10            # if the app was off for a while, evaluate at most this many missed sessions
STATUS_SYMBOL_LIMIT = 25
_lock = threading.Lock()


# ------------------------------------------------------------------ creation
def _known(symbol: str) -> bool:
    cmap = bars.company_map()
    return not cmap or symbol in cmap          # no master loaded yet: accept, the bars lookup will say "no data"


def create(kind: str, preset: str | None, symbol: str | None, params: dict | None, include_existing: bool = False) -> dict:
    if len(repo.list_alerts()) >= MAX_ALERTS:
        raise ValueError(f"You can have at most {MAX_ALERTS} alerts. Delete one first.")
    symbol = symbol.upper().strip() if symbol else None
    if symbol and not _known(symbol):
        raise ValueError(f"Unknown NSE symbol '{symbol}'.")
    if kind == "breakout":
        p = rules.normalize_params(preset or "", params)
        latest = bars.latest_date()
        # no back-fill: an alert only reports sessions that arrive after it was created
        return repo.create_alert("breakout", preset, symbol, p, last_eval_date=latest.isoformat() if latest else None)
    if kind == "result":
        return repo.create_alert("result", "", symbol, {}, baseline_id=0 if include_existing else warehouse.max_filing_id())
    raise ValueError("kind must be 'breakout' or 'result'.")


# ------------------------------------------------------------------- helpers
def _targets(alert: dict) -> list[str]:
    return [alert["symbol"]] if alert["symbol"] else watchlist.items()


def _result_key(alert_id: str, f: dict) -> str:
    norm = " ".join((f.get("title") or "").lower().split())
    return f"result:{alert_id}:{f['symbol']}:{hashlib.sha1(norm.encode()).hexdigest()[:12]}"   # loader re-runs duplicate rows


def _eval_breakout(alert: dict, summary: dict) -> str | None:
    """Evaluate every not-yet-seen session for each target. Returns the newest bar date seen."""
    newest = None
    for sym in _targets(alert):
        rows = bars.symbol_bars(sym, limit=400)
        if not rows:
            continue
        newest = max(newest or rows[-1]["date"], rows[-1]["date"])
        since = alert["last_eval_date"] or ""
        todo = [i for i, b in enumerate(rows) if b["date"] > since][-MAX_CATCHUP_DAYS:]
        for i in todo:
            res = rules.evaluate(alert["preset"], alert["params"], rows, i, sym)
            if res.triggered:
                new = repo.add_notification(alert["id"], "breakout", sym, res.title, res.body,
                                            {"preset": alert["preset"], **res.facts, "caveat": res.note or None},
                                            f"breakout:{alert['id']}:{sym}:{rows[i]['date']}")
                summary["new"] += int(new)
    return newest


def _eval_results(alert: dict, summary: dict) -> int:
    top = alert["baseline_id"]
    for f in warehouse.filings_since(_targets(alert), alert["baseline_id"]):
        top = max(top, f["id"])
        if not rules.is_results_announcement(f):
            continue
        title, body = (f.get("title") or "").strip(), " ".join((f.get("body") or "").split())
        if body.startswith(title):                       # many filings repeat the title at the start of the body
            body = body[len(title):].lstrip(" .-:")
        text = f"{title}. {body[:240]}".strip() if body else title
        new = repo.add_notification(
            alert["id"], "result", f["symbol"], f"{f['symbol']} announced results", text,
            {"filing_id": f["id"], "url": f.get("url") or None, "filed_at": f.get("filed_at"), "source": f.get("source"), "title": f.get("title")},
            _result_key(alert["id"], f))
        summary["new"] += int(new)
    return top


def check_all() -> dict:
    """Run every active alert once. Safe to call from the background loop and from the API at the same time."""
    if not _lock.acquire(blocking=False):
        return {"status": "already_running", "alerts": 0, "new": 0, "errors": []}
    summary = {"status": "ok", "alerts": 0, "new": 0, "errors": []}
    try:
        for a in repo.list_alerts(active_only=True):
            summary["alerts"] += 1
            try:
                if a["kind"] == "breakout":
                    newest = _eval_breakout(a, summary)
                    repo.mark_checked(a["id"], last_eval_date=newest)
                else:
                    repo.mark_checked(a["id"], baseline_id=_eval_results(a, summary))
            except Exception as e:  # noqa: BLE001 - one bad alert must not stop the others
                summary["errors"].append(f"{a['id']}: {type(e).__name__}: {e}")
        if summary["errors"]:
            summary["status"] = "error"
    finally:
        _lock.release()
    return summary


# -------------------------------------------------------------------- status
def status(alert: dict) -> dict:
    """Where the rule stands right now, without notifying. This is what the Alerts page shows next to each rule."""
    targets = _targets(alert)
    if alert["kind"] == "result":
        fl = [f for f in warehouse.filings_since(targets, 0, limit=2000) if rules.is_results_announcement(f)]
        waiting = [f for f in fl if f["id"] > alert["baseline_id"]]
        last = fl[-1] if fl else None
        return {"targets": len(targets), "text": (f"Latest results notice seen: {last['symbol']}, {last['title'][:70]}" if last
                                                  else "No results announcements stored yet."),
                "pending": len(waiting)}
    items, shown = [], targets[:STATUS_SYMBOL_LIMIT]
    for sym in shown:
        rows = bars.symbol_bars(sym, limit=400)
        if not rows:
            items.append({"symbol": sym, "state": "no_data", "text": "No stored prices for this symbol."})
            continue
        res = rules.evaluate(alert["preset"], alert["params"], rows, len(rows) - 1, sym)
        items.append({"symbol": sym, "date": rows[-1]["date"],
                      "state": "no_data" if not res.ready else "triggered" if res.triggered else "waiting",
                      "text": res.note if not res.ready else (res.body if res.triggered else _waiting_text(alert, res, rows[-1]))})
    return {"targets": len(targets), "items": items, "truncated": len(targets) > len(shown),
            "text": "Add stocks to your watchlist to use this alert." if not targets else ""}


def _waiting_text(alert: dict, res: rules.Check, last: dict) -> str:
    f = res.facts
    if alert["params"].get("trigger") == "touch":
        from backend.app.services.alerts import live
        seen = live.last_price(alert["symbol"] or last.get("symbol", "")) if alert["symbol"] else None
        base = "Waiting for the price to touch it. " + (f"Live price {seen[0]:,.2f}. " if seen else "No live price yet. ")
        if alert["preset"] in ("high_52w", "low_52w"):
            return base + f"52-week {'high' if alert['preset'] == 'high_52w' else 'low'} is {f['level']:,.2f}."
        return base + f"Level {alert['params']['level']:,.2f}, last close {last['close']:,.2f}."
    if alert["preset"] in ("high_52w", "low_52w"):
        return f"Not triggered. Close {last['close']:,.2f} vs 52-week {'high' if alert['preset'] == 'high_52w' else 'low'} {f['level']:,.2f}."
    if alert["preset"] == "ema_cross":
        return f"Not triggered. {alert['params']['fast']}-day EMA {f['fast_ema']:,.2f} vs {alert['params']['slow']}-day EMA {f['slow_ema']:,.2f}."
    return f"Not triggered. Last close {last['close']:,.2f}, level {alert['params']['level']:,.2f}."


def public(alert: dict) -> dict:
    return {**alert, "label": rules.describe(alert["kind"], alert["preset"], alert["params"]), "status": status(alert)}


# ---------------------------------------------------------- background loop
class Loop:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, interval_min: int) -> None:
        if interval_min <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(interval_min,), name="alerts-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, interval_min: int) -> None:
        while not self._stop.is_set():
            try:
                res = check_all()
                if res["new"] or res["errors"]:
                    print(f"[alerts] {res}")
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                print(f"[alerts] check failed: {type(e).__name__}: {e}")
            self._stop.wait(interval_min * 60)


loop = Loop()