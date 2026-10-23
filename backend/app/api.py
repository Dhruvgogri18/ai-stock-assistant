from fastapi import APIRouter
from pydantic import BaseModel

from . import chat, data, watchlist

router = APIRouter()


class Filter(BaseModel):
    metric: str
    op: str
    value: float


@router.get("/health")
def health(): return {"ok": True}

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
def filings_search(q: str, symbol: str | None = None):
    return data.search_filings(q, symbol=symbol)

# Watchlist
@router.get("/watchlist")
def wl(): return watchlist.items()
@router.post("/watchlist/{s}")
def wl_add(s: str): watchlist.add(s); return watchlist.items()
@router.delete("/watchlist/{s}")
def wl_del(s: str): watchlist.remove(s); return watchlist.items()


class HistoryItem(BaseModel):
    role: str
    content: str


class Q(BaseModel):
    question: str
    history: list[HistoryItem] = []


@router.post("/chat")
def chat_ep(q: Q):
    hist = [h.model_dump() for h in q.history] if q.history else []
    return chat.ask(q.question, history=hist)
