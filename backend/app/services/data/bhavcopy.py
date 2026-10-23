"""NSE end-of-day data (bhavcopy): download -> parse -> store -> keep fresh.

    sec_bhavdata_full_DDMMYYYY.csv   every NSE-listed security for one trading day
    EQUITY_L.csv                     equity master (symbol, company name, ISIN)
    ind_nifty500list.csv etc.        index constituents, used only to get an industry per symbol

Refresh model: a background job (start_scheduler) wakes every BHAVCOPY_CHECK_INTERVAL_MIN minutes, works out
which weekdays are missing and fetches them. Today's file is not requested before BHAVCOPY_PUBLISH_AFTER_IST.
A weekday that stays 404 is retried for 7 days and then treated as an exchange holiday.

These are public files but not an official API: URLs have moved before, and NSE can block clients it dislikes.
Every failure is written to data_refresh_log and shown by GET /api/data/status.
"""
from __future__ import annotations

import io
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd

from backend.app.config.settings import settings
from backend.app.repositories import bars_repository as bars

IST = timezone(timedelta(hours=5, minutes=30))

# canonical name -> accepted header names (after strip + upper). Covers sec_bhavdata_full and the older cm bhavcopy.
ALIASES = {
    "symbol": ["SYMBOL"],
    "series": ["SERIES"],
    "date": ["DATE1", "TIMESTAMP", "DATE"],
    "prev_close": ["PREV_CLOSE", "PREVCLOSE"],
    "open": ["OPEN_PRICE", "OPEN"],
    "high": ["HIGH_PRICE", "HIGH"],
    "low": ["LOW_PRICE", "LOW"],
    "close": ["CLOSE_PRICE", "CLOSE"],
    "volume": ["TTL_TRD_QNTY", "TOTTRDQTY"],
    "turnover_lacs": ["TURNOVER_LACS"],
    "turnover_rs": ["TOTTRDVAL"],
    "trades": ["NO_OF_TRADES", "TOTALTRADES"],
    "deliv_pct": ["DELIV_PER"],
}
DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")


class DateMismatch(ValueError):
    """The file is for a different trading day than requested (not published yet)."""


class FetchError(RuntimeError):
    """NSE answered with something other than file / 404 (blocked, throttled, server error)."""


# ------------------------------------------------------------------ time helpers
def now_ist() -> datetime:
    return datetime.now(IST)


def _publish_time() -> tuple[int, int]:
    h, m = settings.bhavcopy_publish_after_ist.split(":")
    return int(h), int(m)


def pending_dates(now: datetime, last_stored: date | None, done: set[date], backfill_weekdays: int,
                  full_scan: bool = False, first_stored: date | None = None,
                  stored: set[date] | None = None) -> list[date]:
    """Weekdays that still need a download, oldest first. Pure function (unit-tested).

    Gap healing: when `first_stored` is given, every weekday between the first stored date and today that is neither
    stored nor logged as done is fetched, so a hole in the middle of the history (an interrupted backfill)
    is filled automatically instead of staying empty forever."""
    h, m = _publish_time()
    end = now.date() if (now.hour, now.minute) >= (h, m) else now.date() - timedelta(days=1)

    def prev_weekdays(n: int) -> date:
        d, left = end, n - 1
        while left > 0:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                left -= 1
        return d

    if last_stored is None or full_scan:
        start = prev_weekdays(backfill_weekdays)
    else:
        start = last_stored + timedelta(days=1)
        if first_stored is not None:
            start = min(start, first_stored)
    skip = done | (stored or set())
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out


def next_check_hint(now: datetime | None = None) -> str:
    now = now or now_ist()
    h, m = _publish_time()
    d = now.date()
    if (now.hour, now.minute) >= (h, m) or d.weekday() >= 5:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return f"{d.isoformat()} {h:02d}:{m:02d} IST"


# ------------------------------------------------------------------- download
def _client() -> httpx.Client:
    return httpx.Client(
        timeout=40, follow_redirects=True,
        headers={"User-Agent": settings.nse_user_agent, "Accept": "text/csv,application/octet-stream,*/*",
                 "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"},
    )


def _get(path: str, client: httpx.Client) -> bytes | None:
    """Try each configured host. None = not there (404 everywhere). Raises FetchError for anything odd."""
    last: str | None = None
    for base in settings.bhavcopy_base_urls_list:
        url = base.rstrip("/") + path
        try:
            r = client.get(url)
        except httpx.HTTPError as e:
            last = f"{url}: {type(e).__name__}: {e}"
            continue
        if r.status_code == 200 and len(r.content) > 200:
            return r.content
        if r.status_code in (404, 200):      # 200 with a tiny body is NSE's "not found" page
            continue
        last = f"{url}: HTTP {r.status_code}"
    if last:
        raise FetchError(last)
    return None


def bhav_path(d: date) -> str:
    return f"/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"


def fetch_day(d: date, client: httpx.Client | None = None) -> bytes | None:
    own = client is None
    client = client or _client()
    try:
        return _get(bhav_path(d), client)
    finally:
        if own:
            client.close()


# --------------------------------------------------------------------- parse
def _read_csv(content: bytes) -> pd.DataFrame:
    if content[:2] == b"PK":                                  # zipped
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            content = z.read(z.namelist()[0])
    return pd.read_csv(io.BytesIO(content), skipinitialspace=True, dtype=str)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", "", regex=False), errors="coerce")


def _date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index)
    for fmt in DATE_FORMATS:
        todo = out.isna()
        if not todo.any():
            break
        out[todo] = pd.to_datetime(s[todo], format=fmt, errors="coerce")
    return out


def parse_bhavcopy(content: bytes, expected: date | None = None) -> pd.DataFrame:
    """Raw CSV -> canonical columns (symbol, date, series, prev_close, open, high, low, close, volume,
    turnover_cr, trades, deliv_pct). Raises ValueError (listing the columns it saw) if the format changed."""
    raw = _read_csv(content)
    raw.columns = [str(c).strip().upper() for c in raw.columns]

    def pick(name: str):
        for a in ALIASES[name]:
            if a in raw.columns:
                return raw[a]
        return None

    sym, close = pick("symbol"), pick("close")
    if sym is None or close is None:
        raise ValueError(f"Unrecognised bhavcopy format. Columns found: {list(raw.columns)}")

    df = pd.DataFrame({"symbol": sym.astype(str).str.strip().str.upper()})
    series = pick("series")
    df["series"] = series.astype(str).str.strip().str.upper() if series is not None else None
    dcol = pick("date")
    df["date"] = _date(dcol) if dcol is not None else pd.NaT
    if expected is not None:
        df["date"] = df["date"].fillna(pd.Timestamp(expected))
    for name in ("prev_close", "open", "high", "low", "close", "volume", "trades", "deliv_pct"):
        col = pick(name)
        df[name] = _num(col) if col is not None else float("nan")
    lacs, rs = pick("turnover_lacs"), pick("turnover_rs")
    df["turnover_cr"] = _num(lacs) / 100.0 if lacs is not None else (_num(rs) / 1e7 if rs is not None else float("nan"))

    df = df.dropna(subset=["date", "close"])
    df = df[df["symbol"].ne("") & df["close"].gt(0)]
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    # one row per symbol/day: if a symbol shows up in two series, keep the busier one
    df = df.sort_values("volume", ascending=False).drop_duplicates(["symbol", "date"]).reset_index(drop=True)
    if df.empty:
        raise ValueError("Bhavcopy parsed to zero rows")
    return df


# -------------------------------------------------------------------- ingest
def ingest_day(d: date | None, content: bytes) -> dict:
    df = parse_bhavcopy(content, expected=d)
    dates = sorted(df["date"].unique())
    if d is not None and dates != [d.isoformat()]:
        # file for a different day than asked (NSE sometimes re-serves the previous file)
        raise DateMismatch(f"Requested {d} but file contains {dates}")
    n = bars.upsert_bars(df)
    return {"date": dates[-1], "rows": n}


# -------------------------------------------------------------- master data
def _read_master_csv(content: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(content), skipinitialspace=True, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df


def refresh_master(client: httpx.Client | None = None) -> dict:
    """Company names/ISIN from EQUITY_L and an industry per symbol from the Nifty list CSVs. Best effort."""
    own = client is None
    client = client or _client()
    out = {"equity_l": None, "sectors": 0, "errors": []}
    try:
        try:
            raw = _get("/content/equities/EQUITY_L.csv", client)
            if raw:
                df = _read_master_csv(raw)
                rows = [{"symbol": r["SYMBOL"], "name": r.get("NAME OF COMPANY"), "isin": r.get("ISIN NUMBER")}
                        for r in df.to_dict("records") if r.get("SYMBOL")]
                out["equity_l"] = bars.upsert_companies(rows)
                bars.log_refresh("master", "equity_l", "ok", out["equity_l"])
            else:
                out["errors"].append("EQUITY_L.csv not found")
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"EQUITY_L: {e}")
            bars.log_refresh("master", "equity_l", "error", None, str(e)[:300])

        sectors = 0
        for path in ("/content/indices/ind_nifty500list.csv", "/content/indices/ind_niftymicrocap250_list.csv"):
            try:
                raw = _get(path, client)
                if not raw:
                    continue
                df = _read_master_csv(raw)
                rows = [{"symbol": r["SYMBOL"], "name": r.get("COMPANY NAME"), "sector": r.get("INDUSTRY")}
                        for r in df.to_dict("records") if r.get("SYMBOL")]
                sectors += bars.upsert_companies(rows)
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"{path}: {e}")
        out["sectors"] = sectors
        if sectors:
            bars.log_refresh("master", "sectors", "ok", sectors)
    finally:
        if own:
            client.close()
    return out


def master_due() -> bool:
    st = bars.log_status("master", "equity_l")
    if not st or st["status"] != "ok":
        return True
    try:
        ts = datetime.strptime(st["ts"], "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - ts > timedelta(days=7)


# ------------------------------------------------------------------- refresh
_refresh_lock = threading.Lock()


def refresh(*, now: datetime | None = None, client: httpx.Client | None = None, backfill_days: int | None = None,
            full_scan: bool = False, pause: float = 0.6, with_master: bool = True) -> dict:
    """Bring the database up to date. Safe to call repeatedly; a second concurrent call returns immediately."""
    if not _refresh_lock.acquire(blocking=False):
        return {"status": "already_running"}
    own = client is None
    client = client or _client()
    t0 = time.time()
    summary: dict = {"status": "ok", "loaded": [], "missing": [], "errors": [], "master": None}
    try:
        now = now or now_ist()
        if with_master and master_due():
            summary["master"] = refresh_master(client)
        todo = pending_dates(now, bars.latest_date(), bars.done_dates(),
                             backfill_days or settings.bhavcopy_startup_backfill_days, full_scan=full_scan,
                             first_stored=bars.first_date(), stored=bars.stored_dates())
        for i, d in enumerate(todo):
            if i and pause:
                time.sleep(pause)
            try:
                content = _get(bhav_path(d), client)
            except FetchError as e:
                bars.log_refresh("bhav", d.isoformat(), "error", None, str(e)[:300])
                summary["errors"].append(f"{d}: {e}")
                summary["status"] = "error"
                break                                   # blocked/throttled: stop, retry next cycle
            if content is None:
                bars.log_refresh("bhav", d.isoformat(), "missing", None, "no file (holiday or not yet published)")
                summary["missing"].append(d.isoformat())
                continue
            try:
                info = ingest_day(d, content)
                bars.log_refresh("bhav", d.isoformat(), "ok", info["rows"])
                summary["loaded"].append(info)
            except DateMismatch as e:
                bars.log_refresh("bhav", d.isoformat(), "missing", None, str(e)[:300])
                summary["missing"].append(d.isoformat())
            except Exception as e:  # noqa: BLE001
                bars.log_refresh("bhav", d.isoformat(), "error", None, str(e)[:300])
                summary["errors"].append(f"{d}: {e}")
                summary["status"] = "error"
    finally:
        _refresh_lock.release()
        if own:
            client.close()
        summary["seconds"] = round(time.time() - t0, 1)
        from backend.app.services.data import market_data
        market_data.invalidate()
    return summary


# ----------------------------------------------------------------- scheduler
class Scheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bhavcopy-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                res = refresh()
                if res.get("loaded") or res.get("errors"):
                    print(f"[bhavcopy] {res}")
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                print(f"[bhavcopy] refresh failed: {type(e).__name__}: {e}")
            self._stop.wait(max(1, settings.bhavcopy_check_interval_min) * 60)


scheduler = Scheduler()
