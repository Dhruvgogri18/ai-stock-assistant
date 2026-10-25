"""NSE end-of-day bars + refresh log, stored in the warehouse database (SQLite).

One row per (symbol, date). Written by services/data/bhavcopy.py, read by services/data/market_data.py.
Tables are created lazily here, so nothing else has to call init_db() first.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Iterable

import pandas as pd

from backend.app.repositories.warehouse import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,             -- YYYY-MM-DD
    series      TEXT,                      -- EQ, BE, SM, ST ...
    prev_close  REAL,                      -- exchange-adjusted for splits/bonus on the ex-date
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    turnover_cr REAL,                      -- traded value, Rs crore
    trades      REAL,
    deliv_pct   REAL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(date);

CREATE TABLE IF NOT EXISTS data_refresh_log (
    kind     TEXT NOT NULL,                -- bhav | master
    key      TEXT NOT NULL,                -- YYYY-MM-DD for bhav, 'equity_l' / 'sectors' for master
    status   TEXT NOT NULL,                -- ok | missing | error
    rows     INTEGER,
    message  TEXT,
    ts       TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

_lock = threading.Lock()
_ready: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _conn():
    """warehouse.connect() plus a one-time schema check per database file."""
    from backend.app.config.settings import settings

    from backend.app.repositories import warehouse

    key = settings.warehouse_path
    if key not in _ready:
        with _lock:
            if key not in _ready:
                warehouse.init_db()            # companies table etc.
                with connect() as c0:
                    c0.executescript(SCHEMA)
                _ready.add(key)
    ctx = connect()
    return ctx, ctx.__enter__()


class _Session:
    def __enter__(self):
        self._ctx, self.conn = _conn()
        return self.conn

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def session() -> _Session:
    return _Session()


# ------------------------------------------------------------------ writes
def upsert_bars(df: pd.DataFrame) -> int:
    cols = ["symbol", "date", "series", "prev_close", "open", "high", "low", "close", "volume",
            "turnover_cr", "trades", "deliv_pct"]
    rows = [tuple(None if pd.isna(v) else v for v in r) for r in df[cols].itertuples(index=False, name=None)]
    with session() as c:
        c.executemany(
            f"""INSERT INTO daily_bars({",".join(cols)}) VALUES({",".join("?" * len(cols))})
                ON CONFLICT(symbol, date) DO UPDATE SET
                  series=excluded.series, prev_close=excluded.prev_close, open=excluded.open, high=excluded.high,
                  low=excluded.low, close=excluded.close, volume=excluded.volume,
                  turnover_cr=excluded.turnover_cr, trades=excluded.trades, deliv_pct=excluded.deliv_pct""",
            rows,
        )
    return len(rows)


def upsert_companies(rows: Iterable[dict]) -> int:
    """rows: symbol, name?, isin?, sector?  (only non-empty values overwrite)."""
    data = [(r["symbol"].upper(), r.get("name") or None, r.get("isin") or None, r.get("sector") or None, _now())
            for r in rows if r.get("symbol")]
    with session() as c:
        c.executemany(
            """INSERT INTO companies(symbol, name, isin, sector, updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 name=COALESCE(excluded.name, companies.name),
                 isin=COALESCE(excluded.isin, companies.isin),
                 sector=COALESCE(excluded.sector, companies.sector),
                 updated_at=excluded.updated_at""",
            data,
        )
    return len(data)


def log_refresh(kind: str, key: str, status: str, rows: int | None = None, message: str | None = None) -> None:
    with session() as c:
        c.execute(
            """INSERT INTO data_refresh_log(kind,key,status,rows,message,ts) VALUES(?,?,?,?,?,?)
               ON CONFLICT(kind,key) DO UPDATE SET status=excluded.status, rows=excluded.rows,
                 message=excluded.message, ts=excluded.ts""",
            (kind, key, status, rows, message, _now()),
        )


# ------------------------------------------------------------------- reads
def latest_date() -> date | None:
    with session() as c:
        v = c.execute("SELECT MAX(date) AS d FROM daily_bars").fetchone()["d"]
    return date.fromisoformat(v) if v else None


def first_date() -> date | None:
    with session() as c:
        v = c.execute("SELECT MIN(date) AS d FROM daily_bars").fetchone()["d"]
    return date.fromisoformat(v) if v else None


def stored_dates() -> set[date]:
    """Every date that has bars (independent of the refresh log)."""
    with session() as c:
        return {date.fromisoformat(r["date"]) for r in c.execute("SELECT DISTINCT date FROM daily_bars")}


def distinct_dates(limit: int | None = None) -> list[str]:
    with session() as c:
        rows = c.execute("SELECT DISTINCT date FROM daily_bars ORDER BY date DESC" + (f" LIMIT {int(limit)}" if limit else ""))
        return [r["date"] for r in rows]


def count_bars() -> int:
    with session() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM daily_bars").fetchone()["n"])


def done_dates() -> set[date]:
    """Dates we should not fetch again: loaded OK, or confirmed missing for > 7 days (exchange holiday)."""
    out: set[date] = set()
    cutoff = date.today().toordinal() - 7
    with session() as c:
        for r in c.execute("SELECT key, status FROM data_refresh_log WHERE kind='bhav'"):
            d = date.fromisoformat(r["key"])
            if r["status"] == "ok" or (r["status"] == "missing" and d.toordinal() < cutoff):
                out.add(d)
    return out


def recent_log(limit: int = 10) -> list[dict]:
    with session() as c:
        return [dict(r) for r in c.execute(
            "SELECT kind,key,status,rows,message,ts FROM data_refresh_log ORDER BY ts DESC LIMIT ?", (limit,))]


def log_status(kind: str, key: str) -> dict | None:
    with session() as c:
        r = c.execute("SELECT * FROM data_refresh_log WHERE kind=? AND key=?", (kind, key)).fetchone()
    return dict(r) if r else None


def snapshot_inputs(days: int = 260) -> dict | None:
    """Memory-lean inputs for the market snapshot: the latest day's rows, a (date x symbol) close matrix for the
    symbols listed on the latest day (forward-filled), and the dates. Never holds all `days` x symbols rows in pandas
    (that costs several hundred MB and gets a 512 MB host killed)."""
    import numpy as np
    with session() as c:
        dates = sorted(r["date"] for r in c.execute("SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ?", (days,)))
        if not dates:
            return None
        last = pd.read_sql_query(
            "SELECT symbol,date,series,prev_close,open,high,low,close,volume,turnover_cr,deliv_pct "
            "FROM daily_bars WHERE date = ?", c, params=(dates[-1],)).set_index("symbol")
        cols = {s: i for i, s in enumerate(dict.fromkeys(last.index))}
        rows = {d: i for i, d in enumerate(dates)}
        arr = np.full((len(dates), len(cols)), np.nan)
        for d, sym, close in c.execute("SELECT date,symbol,close FROM daily_bars WHERE date >= ?", (dates[0],)):
            j = cols.get(sym)
            if j is not None and close is not None:
                arr[rows[d], j] = close
    piv = pd.DataFrame(arr, index=dates, columns=list(cols)).ffill()
    return {"dates": dates, "last": last, "piv": piv}


def range_52w(from_date: str) -> tuple[pd.Series, pd.Series]:
    """Highest high / lowest low per symbol since `from_date` (aggregated inside SQLite)."""
    with session() as c:
        df = pd.read_sql_query("SELECT symbol, MAX(high) AS hi, MIN(low) AS lo FROM daily_bars WHERE date >= ? "
                               "GROUP BY symbol", c, params=(from_date,)).set_index("symbol")
    return df["hi"], df["lo"]


def bars_window(days: int = 260) -> pd.DataFrame:
    """All symbols over the most recent `days` trading dates (for snapshots / returns / 52-week range)."""
    with session() as c:
        dates = [r["date"] for r in c.execute("SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ?", (days,))]
        if not dates:
            return pd.DataFrame()
        return pd.read_sql_query(
            "SELECT symbol,date,series,prev_close,open,high,low,close,volume,turnover_cr,deliv_pct "
            "FROM daily_bars WHERE date >= ?", c, params=(dates[-1],))


def symbol_bars(symbol: str, limit: int = 1300) -> list[dict]:
    with session() as c:
        rows = c.execute(
            "SELECT date,series,prev_close,open,high,low,close,volume,turnover_cr,deliv_pct FROM daily_bars "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?", (symbol.upper(), limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def top_by_turnover(n: int, sessions: int = 60) -> list[str]:
    """The n most liquid symbols (highest average value traded over the last `sessions` stored sessions)."""
    with session() as c:
        dates = [r[0] for r in c.execute("SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ?", (sessions,))]
        if not dates:
            return []
        marks = ",".join("?" for _ in dates)
        rows = c.execute(f"SELECT symbol FROM daily_bars WHERE date IN ({marks}) GROUP BY symbol "
                         "ORDER BY AVG(turnover_cr) DESC LIMIT ?", [*dates, n]).fetchall()
        return [r[0] for r in rows]


def company_map() -> dict[str, dict]:
    with session() as c:
        return {r["symbol"]: dict(r) for r in c.execute("SELECT symbol,name,sector,isin FROM companies")}