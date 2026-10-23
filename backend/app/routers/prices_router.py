"""Where prices are coming from right now (live Angel One or end-of-day bhavcopy), and how healthy the live feed is."""
from fastapi import APIRouter

from backend.app.services.data import live_prices

router = APIRouter(tags=["prices"])


@router.get("/prices/status")
def prices_status() -> dict:
    return live_prices.status()