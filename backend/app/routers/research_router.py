"""Direct (non-chat) research endpoints, watchlist and app metadata. Same data the chat tools use."""
from __future__ import annotations

from fastapi import APIRouter

from backend.app.config.settings import settings
from backend.app.repositories import session_repository as repo, watchlist_repository as watchlist
from backend.app.schemas.research import Filter
from backend.app.services.data import service as data
from backend.app.services.llm import gateway_client as gw
from backend.app.services.suggestions import get_suggestions

router = APIRouter(tags=["research"])


@router.get("/health")
def health() -> dict:
    return {"ok": True, "llm_configured": gw.configured(), "router_enabled": settings.router_enabled,
            "model": (gw._providers()[0].model if gw.configured() else None), "fast_model": gw.fast_model() or None,
            "providers": [p.name for p in gw._providers()],
            "data_provider": settings.data_provider}


@router.get("/llm-check")
def llm_check() -> dict:
    return {"providers": gw.diagnose()}


@router.get("/me")
def me() -> dict:
    return {"name": settings.user_name, "app_name": settings.app_name, "conversations": repo.count_sessions()}


@router.get("/suggestions")
def suggestions(kind: str | None = None) -> dict:
    """Dynamic home-screen cards for Build Screener / Research / News.

    kind: screener | research | news | omit for all three.
    Content shifts with the latest market snapshot and recent filings.
    """
    return get_suggestions(kind)


@router.get("/symbols")
def symbols() -> list[str]:
    return data.UNIVERSE


# Phase 1
@router.get("/results/{s}")
def results(s: str, quarterly: bool = False): return data.results(s, quarterly)
@router.get("/statements/{s}")
def statements(s: str, kind: str = "income"): return data.statements(s, kind)
@router.get("/ratios/{s}")
def ratios(s: str): return data.ratios(s)
@router.get("/peers/{s}")
def peers(s: str): return data.peers(s)
@router.get("/ownership/{s}")
def ownership(s: str): return data.ownership(s)
@router.get("/news/{s}")
def news(s: str): return data.news(s)
@router.get("/history/{s}")
def history(s: str, period: str = "5y"): return data.history(s, period)
@router.post("/screener")
def screener(filters: list[Filter]): return data.screener([f.model_dump() for f in filters])

# Phase 2 / 3 taster
@router.get("/events/{s}")
def events(s: str): return data.events(s)
@router.get("/outlook/{s}")
def outlook(s: str): return data.outlook(s)

# RAG filings search
@router.get("/filings/search")
def filings_search(q: str, symbol: str | None = None): return data.search_filings(q, symbol=symbol)


# Watchlist
@router.get("/watchlist")
def wl(): return watchlist.items()
@router.post("/watchlist/{s}")
def wl_add(s: str): watchlist.add(s); return watchlist.items()
@router.post("/watchlist")
def wl_add_many(symbols: list[str]): watchlist.add_many(symbols); return watchlist.items()
@router.delete("/watchlist/{s}")
def wl_del(s: str): watchlist.remove(s); return watchlist.items()