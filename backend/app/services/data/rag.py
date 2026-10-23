"""Lightweight RAG over exchange filings / announcements.

Uses SQLite FTS5 for keyword retrieval (no embedding key required).
Optional: set EMBEDDING_API_KEY later for vector search.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.repositories import warehouse as db


def _meta() -> dict:
    return {
        "source": "Local filings index (NSE/BSE announcements & notes)",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def ensure_index() -> None:
    db.init_db()
    db.seed_demo_filings()


def search(query: str, symbol: str | None = None, limit: int = 6) -> dict:
    """Return ranked filing snippets for the LLM to cite — never invent text."""
    ensure_index()
    # FTS5: simple query sanitise
    q = " ".join(w for w in query.replace('"', " ").split() if w)
    if not q:
        return {"rows": [], "note": "Empty query.", **_meta()}

    # Prefer symbol-scoped search when provided
    rows = db.search_filings(q, symbol=symbol, limit=limit)
    if not rows and symbol:
        rows = db.search_filings(q, symbol=None, limit=limit)

    out = []
    for r in rows:
        out.append({
            "symbol": r.get("symbol"),
            "doc_type": r.get("doc_type"),
            "title": r.get("title"),
            "filed_at": r.get("filed_at"),
            "url": r.get("url"),
            "snippet": r.get("snippet") or "",
            "source": r.get("source"),
        })

    note = (
        "Snippets from indexed filings. Cite these; do not invent quotes. "
        "Run scripts.load_data to ingest more announcements."
    )
    if not out:
        note = "No matching filings in the local index. Loader may not have run yet."
    return {"rows": out, "query": query, "symbol": symbol, "note": note, **_meta()}
