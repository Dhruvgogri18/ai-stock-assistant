"""Watchlist (user state, lives in sessions.db)."""
from __future__ import annotations

from backend.app.repositories._sessions_db import connect


def items() -> list[str]:
    with connect() as c:
        return [r[0] for r in c.execute("SELECT symbol FROM watchlist ORDER BY symbol")]


def add(symbol: str) -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO watchlist(symbol) VALUES(?)", (symbol.upper(),))


def add_many(symbols: list[str]) -> None:
    with connect() as c:
        c.executemany("INSERT OR IGNORE INTO watchlist(symbol) VALUES(?)", [(s.upper(),) for s in symbols])


def remove(symbol: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))
