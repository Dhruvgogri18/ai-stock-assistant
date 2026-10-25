"""Company page: overview, results, statements, peers, and an on-demand fundamentals refresh."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.services.fundamentals import service

router = APIRouter(prefix="/company", tags=["company"])


def _run(fn, *a):
    try:
        return fn(*a)
    except service.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except service.Cooldown as e:
        raise HTTPException(429, str(e)) from e


@router.get("/{symbol}")
def overview(symbol: str) -> dict:
    return _run(service.overview, symbol)


@router.get("/{symbol}/results")
def results(symbol: str, period: str = "annual") -> dict:
    return _run(service.results, symbol, period)


@router.get("/{symbol}/statements")
def statements(symbol: str, kind: str = "income", period: str = "annual") -> dict:
    return _run(service.statements, symbol, kind, period)


@router.get("/{symbol}/peers")
def peers(symbol: str, limit: int = 6) -> dict:
    return _run(service.peer_table, symbol, limit)


@router.post("/{symbol}/refresh")
def refresh(symbol: str) -> dict:
    return _run(service.refresh, symbol)