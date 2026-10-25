"""SQLite (default) or Postgres warehouse for fundamentals, ownership, filings.

Schema is created on first connect. Nightly loader: python -m scripts.load_data
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.app.config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    symbol       TEXT PRIMARY KEY,
    name         TEXT,
    isin         TEXT,
    sector       TEXT,
    industry     TEXT,
    exchange     TEXT DEFAULT 'NSE',
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS financials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    period       TEXT NOT NULL,          -- e.g. 2025-03-31 or 2025-Q2
    period_type  TEXT NOT NULL,          -- annual | quarterly
    statement    TEXT NOT NULL,          -- income | balance | cashflow
    line_item    TEXT NOT NULL,
    value        REAL,
    UNIQUE(symbol, period, period_type, statement, line_item)
);

-- When and from where each company's statements were fetched (financials rows carry no provenance themselves).
CREATE TABLE IF NOT EXISTS fundamentals_status (
    symbol             TEXT PRIMARY KEY,
    fetched_at         TEXT,
    source             TEXT,
    currency           TEXT,
    shares             REAL,                 -- shares outstanding at fetch time
    price_at_fetch     REAL,
    annual_periods     INTEGER DEFAULT 0,
    quarterly_periods  INTEGER DEFAULT 0,
    industry           TEXT,                 -- the provider's industry label (not the NSE master sector)
    provider_sector    TEXT,                 -- the provider's sector label (a different taxonomy from companies.sector)
    error              TEXT
);

CREATE TABLE IF NOT EXISTS ratios (
    symbol       TEXT PRIMARY KEY,
    name         TEXT,
    sector       TEXT,
    pe           REAL,
    pb           REAL,
    roce         REAL,
    roe          REAL,
    net_margin   REAL,
    op_margin    REAL,
    market_cap   REAL,
    as_of        TEXT,
    source       TEXT
);

CREATE TABLE IF NOT EXISTS shareholding (
    symbol       TEXT NOT NULL,
    period       TEXT NOT NULL,
    holder_type  TEXT NOT NULL,          -- promoter | fii | dii | public | other
    pct          REAL,
    PRIMARY KEY (symbol, period, holder_type)
);

CREATE TABLE IF NOT EXISTS prices (
    symbol       TEXT NOT NULL,
    date         TEXT NOT NULL,
    close        REAL,
    volume       REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    event_type   TEXT,
    event_date   TEXT,
    detail       TEXT
);

-- Filings / announcements for RAG (keyword + optional vectors later)
CREATE TABLE IF NOT EXISTS filings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    doc_type     TEXT NOT NULL,          -- announcement | concall | annual_report | result
    title        TEXT,
    filed_at     TEXT,
    url          TEXT,
    body         TEXT NOT NULL,
    source       TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS filings_fts USING fts5(
    symbol, doc_type, title, body, content='filings', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS filings_ai AFTER INSERT ON filings BEGIN
  INSERT INTO filings_fts(rowid, symbol, doc_type, title, body)
  VALUES (new.id, new.symbol, new.doc_type, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS filings_ad AFTER DELETE ON filings BEGIN
  INSERT INTO filings_fts(filings_fts, rowid, symbol, doc_type, title, body)
  VALUES ('delete', old.id, old.symbol, old.doc_type, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS filings_au AFTER UPDATE ON filings BEGIN
  INSERT INTO filings_fts(filings_fts, rowid, symbol, doc_type, title, body)
  VALUES ('delete', old.id, old.symbol, old.doc_type, old.title, old.body);
  INSERT INTO filings_fts(rowid, symbol, doc_type, title, body)
  VALUES (new.id, new.symbol, new.doc_type, new.title, new.body);
END;

CREATE INDEX IF NOT EXISTS idx_fin_symbol ON financials(symbol);
CREATE INDEX IF NOT EXISTS idx_sh_symbol ON shareholding(symbol);
CREATE INDEX IF NOT EXISTS idx_filings_symbol ON filings(symbol);
"""


def _is_postgres() -> bool:
    return bool(settings.database_url and settings.database_url.startswith("postgres"))


@contextmanager
def connect() -> Iterator[Any]:
    if _is_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(settings.database_url, cursor_factory=RealDictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        path = Path(settings.warehouse_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    """Create tables (SQLite). For Postgres, run schema with minor type tweaks."""
    if _is_postgres():
        # Minimal Postgres path: create core tables without FTS virtual table
        pg = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        # Strip FTS + triggers for Postgres (use tsvector later)
        parts = []
        skip = False
        for line in pg.splitlines():
            if "filings_fts" in line or "TRIGGER" in line or "VIRTUAL TABLE" in line:
                skip = True
            if skip:
                if line.strip().endswith(";") and "TRIGGER" not in line and "VIRTUAL" not in line:
                    skip = False
                elif "END;" in line:
                    skip = False
                continue
            parts.append(line)
        sql = "\n".join(parts)
        with connect() as conn:
            cur = conn.cursor()
            for stmt in sql.split(";"):
                s = stmt.strip()
                if s:
                    cur.execute(s)
        return

    with connect() as conn:
        conn.executescript(SCHEMA)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def ratios_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM ratios").fetchone()
        return int(row["c"] if row else 0)


def get_ratios(symbol: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM ratios WHERE symbol = ?", (symbol.upper(),)).fetchone()
        if not row:
            return None
        return dict(row)


def list_ratio_rows() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM ratios ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]


def get_financials(symbol: str, statement: str, period_type: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT period, line_item, value FROM financials
               WHERE symbol = ? AND statement = ? AND period_type = ?
               ORDER BY period DESC""",
            (symbol.upper(), statement, period_type),
        ).fetchall()
        return [dict(r) for r in rows]


def get_shareholding(symbol: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT period, holder_type, pct FROM shareholding
               WHERE symbol = ? ORDER BY period DESC, holder_type""",
            (symbol.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]


def latest_shareholding_all() -> list[dict]:
    """Latest loaded period for every symbol, all holder types (used by the screener)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.symbol, s.period, s.holder_type, s.pct FROM shareholding s
               JOIN (SELECT symbol, MAX(period) AS p FROM shareholding GROUP BY symbol) m
                 ON s.symbol = m.symbol AND s.period = m.p"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_prices(symbol: str, limit: int = 260) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT date, close FROM prices WHERE symbol = ?
               ORDER BY date DESC LIMIT ?""",
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in reversed(list(rows))]


def upsert_company(symbol: str, name: str = "", sector: str = "", isin: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO companies(symbol, name, sector, isin, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 name=COALESCE(excluded.name, companies.name),
                 sector=COALESCE(excluded.sector, companies.sector),
                 isin=COALESCE(excluded.isin, companies.isin),
                 updated_at=excluded.updated_at""",
            (symbol.upper(), name or None, sector or None, isin or None, utc_now()),
        )


def upsert_ratios(row: dict) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO ratios(symbol, name, sector, pe, pb, roce, roe, net_margin, op_margin, market_cap, as_of, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 name=excluded.name, sector=excluded.sector, pe=excluded.pe, pb=excluded.pb,
                 roce=excluded.roce, roe=excluded.roe, net_margin=excluded.net_margin,
                 op_margin=excluded.op_margin, market_cap=excluded.market_cap,
                 as_of=excluded.as_of, source=excluded.source""",
            (
                row["symbol"].upper(), row.get("name"), row.get("sector"),
                row.get("pe"), row.get("pb"), row.get("roce"), row.get("roe"),
                row.get("net_margin"), row.get("op_margin"), row.get("market_cap"),
                row.get("as_of") or utc_now(), row.get("source") or "loader",
            ),
        )


def upsert_financial_rows(symbol: str, period: str, period_type: str, statement: str, items: dict[str, float | None]) -> None:
    with connect() as conn:
        for line, val in items.items():
            conn.execute(
                """INSERT INTO financials(symbol, period, period_type, statement, line_item, value)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(symbol, period, period_type, statement, line_item)
                   DO UPDATE SET value=excluded.value""",
                (symbol.upper(), period, period_type, statement, line, val),
            )


def get_financials_all(symbol: str) -> list[dict]:
    """Every stored line for one company: statement, period_type, period, line_item, value."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT statement, period_type, period, line_item, value FROM financials WHERE symbol = ? ORDER BY period",
            (symbol.upper(),)).fetchall()
        return [dict(r) for r in rows]


def symbols_with_financials() -> list[str]:
    """Every symbol that has annual income lines stored, whether or not a fundamentals_status record exists (older loads have none)."""
    with connect() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM financials WHERE statement='income' AND period_type='annual' ORDER BY symbol")]


def upsert_fundamentals_status(symbol: str, **f) -> None:
    cols = ("fetched_at", "source", "currency", "shares", "price_at_fetch", "annual_periods", "quarterly_periods",
            "industry", "provider_sector", "error")
    vals = [f.get(c) for c in cols]
    with connect() as conn:
        conn.execute(
            f"""INSERT INTO fundamentals_status(symbol, {", ".join(cols)}) VALUES(?{",?" * len(cols)})
                ON CONFLICT(symbol) DO UPDATE SET {", ".join(f"{c}=excluded.{c}" for c in cols)}""",
            [symbol.upper(), *vals])


def get_fundamentals_status(symbol: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM fundamentals_status WHERE symbol = ?", (symbol.upper(),)).fetchone()
        return dict(r) if r else None


def list_fundamentals_status() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM fundamentals_status ORDER BY symbol").fetchall()]


def ensure_company(symbol: str, name: str = "") -> None:
    """Create the company row if it does not exist. Never changes an existing row (the NSE master owns name and sector)."""
    with connect() as conn:
        conn.execute("INSERT INTO companies(symbol, name, updated_at) VALUES(?,?,?) ON CONFLICT(symbol) DO NOTHING",
                     (symbol.upper(), name or None, utc_now()))


def set_company_industry(symbol: str, industry: str) -> None:
    """Record the provider's industry without touching the NSE master's name or sector."""
    with connect() as conn:
        conn.execute("UPDATE companies SET industry = COALESCE(NULLIF(industry, ''), ?) WHERE symbol = ?", (industry, symbol.upper()))


def upsert_shareholding(symbol: str, period: str, holders: dict[str, float | None]) -> None:
    with connect() as conn:
        for ht, pct in holders.items():
            conn.execute(
                """INSERT INTO shareholding(symbol, period, holder_type, pct)
                   VALUES(?,?,?,?)
                   ON CONFLICT(symbol, period, holder_type) DO UPDATE SET pct=excluded.pct""",
                (symbol.upper(), period, ht, pct),
            )


def insert_filing(symbol: str, doc_type: str, title: str, body: str, url: str = "", filed_at: str = "", source: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO filings(symbol, doc_type, title, filed_at, url, body, source)
               VALUES(?,?,?,?,?,?,?)""",
            (symbol.upper(), doc_type, title, filed_at or utc_now(), url, body, source),
        )
        return int(cur.lastrowid)


def search_filings(query: str, symbol: str | None = None, limit: int = 8) -> list[dict]:
    """Full-text search over filings (SQLite FTS5)."""
    q = query.strip()
    if not q:
        return []
    with connect() as conn:
        if symbol:
            rows = conn.execute(
                """SELECT f.id, f.symbol, f.doc_type, f.title, f.filed_at, f.url, f.source,
                          snippet(filings_fts, 3, '>>>', '<<<', '…', 32) AS snippet
                   FROM filings_fts
                   JOIN filings f ON f.id = filings_fts.rowid
                   WHERE filings_fts MATCH ? AND f.symbol = ?
                   ORDER BY rank LIMIT ?""",
                (q, symbol.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT f.id, f.symbol, f.doc_type, f.title, f.filed_at, f.url, f.source,
                          snippet(filings_fts, 3, '>>>', '<<<', '…', 32) AS snippet
                   FROM filings_fts
                   JOIN filings f ON f.id = filings_fts.rowid
                   WHERE filings_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (q, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def insert_event(symbol: str, event_type: str, event_date: str, detail: str = "") -> bool:
    """Store one confirmed calendar event (results date, ex-dividend date...). Duplicates are ignored."""
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM events WHERE symbol=? AND event_type=? AND event_date=?",
                         (symbol.upper(), event_type, event_date)).fetchone()["c"]
        if n:
            return False
        conn.execute("INSERT INTO events(symbol, event_type, event_date, detail) VALUES(?,?,?,?)",
                     (symbol.upper(), event_type, event_date, detail))
        return True


def list_events(symbols: list[str], date_from: str, date_to: str) -> list[dict]:
    if not symbols:
        return []
    marks = ",".join("?" for _ in symbols)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT symbol, event_type, event_date, detail FROM events WHERE symbol IN ({marks}) "
            "AND event_date >= ? AND event_date <= ? ORDER BY event_date, symbol",
            [s.upper() for s in symbols] + [date_from, date_to]).fetchall()
        return [dict(r) for r in rows]


def filings_since(symbols: list[str], since_id: int, limit: int = 500) -> list[dict]:
    """Filings newer than since_id for these symbols, oldest first."""
    if not symbols:
        return []
    marks = ",".join("?" for _ in symbols)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, symbol, doc_type, title, filed_at, url, body, source FROM filings "
            f"WHERE symbol IN ({marks}) AND id > ? ORDER BY id LIMIT ?",
            [s.upper() for s in symbols] + [since_id, limit]).fetchall()
        return [dict(r) for r in rows]


def max_filing_id() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM filings").fetchone()["m"])


def seed_demo_filings() -> None:
    """Seed a few sample filings so RAG works before the loader runs."""
    samples = [
        ("RELIANCE", "announcement", "Board meeting for Q4 results",
         "Reliance Industries Limited will consider audited financial results for the quarter and year ended 31 March. "
         "The board will also consider dividend recommendation. Retail and Jio segments expected to be discussed."),
        ("RELIANCE", "result", "Q3 FY25 consolidated results highlight",
         "Consolidated revenue grew driven by Jio and Retail. EBITDA margin stable. Net debt reduced year on year. "
         "Management guided for continued capex in new energy and telecom spectrum."),
        ("TCS", "concall", "TCS Q3 earnings call summary",
         "TCS reported deal wins in BFSI and manufacturing. Management commented on deal TCV and attrition. "
         "Margin outlook stable; wage hike impact already factored. Demand commentary cautious in Europe."),
        ("INFY", "announcement", "Infosys quarterly results schedule",
         "Infosys Limited will announce financial results for the quarter. Investor call scheduled after market hours. "
         "Guidance for full year revenue growth to be revisited."),
        ("HDFCBANK", "announcement", "Shareholding pattern update",
         "HDFC Bank filed quarterly shareholding pattern. Promoter holding stable. FII and DII holdings updated as per SEBI format."),
    ]
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM filings").fetchone()["c"]
        if n and int(n) > 0:
            return
    for sym, dt, title, body in samples:
        insert_filing(sym, dt, title, body, source="demo-seed")