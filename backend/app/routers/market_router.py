"""End-of-day market data: movers, quotes and the refresh job. All served from the local database."""
from __future__ import annotations

import threading

from fastapi import APIRouter

from backend.app.config.settings import settings
from backend.app.repositories import bars_repository as bars
from backend.app.services.data import bhavcopy, market_data
from backend.app.services.data import service as data

router = APIRouter(tags=["market"])


@router.get("/movers")
def movers(kind: str = "gainers", limit: int = 25, min_turnover_cr: float | None = None,
           include_sme: bool = True, sector: str | None = None):
    return data.market_movers(kind, limit, min_turnover_cr, include_sme, sector)


@router.get("/technicals/{symbol}")
def technicals(symbol: str, indicators: str = "ema20,ema50", period: str = "6m"):
    """Same calculation the chat uses, without the LLM. indicators = comma list, e.g. ema10,rsi,macd,bollinger."""
    return data.technicals(symbol, [s for s in indicators.split(",") if s.strip()], period)


@router.get("/quote/{symbol}")
def quote(symbol: str):
    return data.quote(symbol)


@router.get("/data/status")
def data_status() -> dict:
    """What market data we hold, how fresh it is, and what the refresh job last did."""
    st = market_data.status()
    return {
        **st,
        "companies": len(bars.company_map()),
        "auto_refresh": settings.bhavcopy_auto_refresh,
        "next_check": bhavcopy.next_check_hint(),
        "recent_refreshes": bars.recent_log(8),
        "source": market_data.SOURCE,
    }


@router.post("/data/refresh")
def data_refresh(days: int | None = None) -> dict:
    """Start a refresh now (runs in the background). `days` = how many weekdays to scan back for gaps."""
    threading.Thread(
        target=lambda: bhavcopy.refresh(backfill_days=days, full_scan=bool(days)), daemon=True, name="bhavcopy-manual"
    ).start()
    return {"started": True, "note": "Poll GET /api/data/status for progress."}
