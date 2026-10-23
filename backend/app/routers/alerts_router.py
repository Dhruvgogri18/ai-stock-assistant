"""Alerts (breakout + results), their notification inbox, and the event calendar / watchlist views."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.repositories import alert_repository as repo, bars_repository as bars, watchlist_repository as watchlist
from backend.app.schemas.alerts import AlertCreate, AlertUpdate, ReadBody
from backend.app.services.alerts import engine, live, rules
from backend.app.services.events import calendar
from backend.app.services.screener import engine as screener_engine

router = APIRouter(tags=["alerts"])


# ------------------------------------------------------------------- alerts
@router.get("/alerts/presets")
def presets() -> dict:
    return {"breakout": [{"id": k, **{x: v[x] for x in ("label", "description", "params")}} for k, v in rules.PRESETS.items()],
            "max_alerts": engine.MAX_ALERTS, "live": live.status()}


@router.get("/alerts")
def list_alerts() -> dict:
    return {"items": [engine.public(a) for a in repo.list_alerts()], "watchlist_size": len(watchlist.items())}


@router.post("/alerts", status_code=201)
def create_alert(body: AlertCreate) -> dict:
    return engine.public(engine.create(body.kind, body.preset, body.symbol, body.params, body.include_existing))


@router.patch("/alerts/{aid}")
def update_alert(aid: str, body: AlertUpdate) -> dict:
    a = repo.set_active(aid, body.active)
    if not a:
        raise HTTPException(404, "Alert not found")
    return engine.public(a)


@router.delete("/alerts/{aid}")
def delete_alert(aid: str) -> dict:
    if not repo.delete_alert(aid):
        raise HTTPException(404, "Alert not found")
    return {"deleted": aid}


@router.get("/alerts/live-status")
def live_status() -> dict:
    return live.status()


@router.post("/alerts/check")
def check_now() -> dict:
    """Run every active alert against the data stored right now, and take one live-price reading if the market is open."""
    res = engine.check_all()
    lv = live.poll_once()
    return {**res, "new": res["new"] + lv["fired"], "live": lv, "unread": repo.unread_count()}


# ------------------------------------------------------------ notifications
@router.get("/notifications")
def notifications(unread: bool = False, limit: int = 100) -> dict:
    return {"items": repo.list_notifications(unread, max(1, min(limit, 300))), "unread": repo.unread_count()}


@router.get("/notifications/unread-count")
def unread_count() -> dict:
    return {"unread": repo.unread_count()}


@router.post("/notifications/read")
def read(body: ReadBody) -> dict:
    return {"marked": repo.mark_read(body.ids), "unread": repo.unread_count()}


@router.delete("/notifications/{nid}")
def delete_notification(nid: int) -> dict:
    if not repo.delete_notification(nid):
        raise HTTPException(404, "Notification not found")
    return {"deleted": nid, "unread": repo.unread_count()}


# ----------------------------------------------------------------- calendar
@router.get("/calendar")
def event_calendar(days: int = 60, symbols: str = "") -> dict:
    """Upcoming results and dividend dates. Defaults to the watchlist."""
    syms = [s for s in symbols.split(",") if s.strip()] or watchlist.items()
    return {**calendar.get_calendar(syms, days), "scope": "symbols" if symbols.strip() else "watchlist"}


# ---------------------------------------------------------------- watchlist
@router.get("/watchlist/overview")
def watchlist_overview() -> dict:
    return screener_engine.lookup(watchlist.items())


@router.get("/symbols/search")
def symbol_search(q: str = "", limit: int = 8) -> list[dict]:
    q = q.strip().upper()
    if len(q) < 1:
        return []
    cmap = bars.company_map()
    starts = [s for s in cmap if s.startswith(q)]
    contains = [s for s, c in cmap.items() if s not in starts and (q in s or q in (c.get("name") or "").upper())]
    return [{"symbol": s, "name": cmap[s].get("name") or s} for s in (sorted(starts) + sorted(contains))[:max(1, min(limit, 20))]]