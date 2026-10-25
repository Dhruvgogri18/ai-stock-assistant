"""FastAPI entrypoint. Registers routers and serves the frontend; no business logic here.

    uvicorn backend.app.main:app --reload
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config.settings import settings
from backend.app.repositories import warehouse
from backend.app.routers.market_router import router as market_router
from backend.app.routers.alerts_router import router as alerts_router
from backend.app.routers.company_router import router as company_router
from backend.app.routers.prices_router import router as prices_router
from backend.app.routers.research_router import router as research_router
from backend.app.routers.screener_router import router as screener_router
from backend.app.routers.session_router import router as session_router
from backend.app.services.alerts import engine as alert_engine, live as alert_live
from backend.app.services.data import bhavcopy, live_prices, market_data

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _warm_snapshot() -> None:
    try:
        market_data.snapshot()
    except Exception:  # noqa: BLE001  (no data loaded yet is fine; the first request reports it)
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        warehouse.init_db()
        warehouse.seed_demo_filings()
    except Exception as e:  # noqa: BLE001
        print("warehouse init:", e)
    # Build the end-of-day snapshot in the background (slow on a large bars table) so the first Screener visit is fast.
    threading.Thread(target=_warm_snapshot, daemon=True, name="snapshot-warm").start()
    alert_engine.loop.start(settings.alerts_check_interval_min)
    alert_live.loop.start(settings.alerts_live_interval_sec)
    live_prices.refresher.start()
    # Keep NSE end-of-day data fresh: catches up on startup, then re-checks after each market close.
    # (No provider API calls happen at startup, so a restart never spends IndianAPI quota.)
    if settings.bhavcopy_auto_refresh:
        bhavcopy.scheduler.start()
    yield
    bhavcopy.scheduler.stop()
    alert_engine.loop.stop()
    alert_live.loop.stop()
    live_prices.refresher.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ValueError)
async def bad_request(_, e):
    return JSONResponse({"error": str(e)}, status_code=400)


app.include_router(session_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(research_router, prefix="/api")
app.include_router(screener_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.include_router(company_router, prefix="/api")
app.include_router(prices_router, prefix="/api")
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="web")