from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

IST = timezone(timedelta(hours=5, minutes=30))
HEADER = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"


def csv_for(d: date, rows) -> bytes:
    """rows: (symbol, series, prev, close, turnover_lacs, deliv_per)"""
    lines = [HEADER]
    for sym, ser, prev, close, lacs, dper in rows:
        lines.append(f"{sym}, {ser}, {d:%d-%b-%Y}, {prev}, {prev}, {max(prev, close)}, {min(prev, close)}, {close}, {close}, {close}, "
                     f"{int(lacs * 1e5 / max(close, 1))}, {lacs}, 500, 1000, {dper}")
    return ("\n".join(lines) + "\n").encode()


DAY = [
    ("TCS", "EQ", 100.0, 102.0, 5000.0, "60.5"),
    ("INFY", "EQ", 100.0, 99.0, 4000.0, "55.0"),
    ("ILLIQ", "EQ", 10.0, 20.0, 5.0, "-"),          # +100% but only Rs 0.05 cr traded
    ("SMEX", "SM", 50.0, 60.0, 300.0, "-"),         # +20% SME, Rs 3 cr
    ("NIFTYBEES", "EQ", 200.0, 210.0, 9000.0, "-"),  # ETF: not in equity master
    ("BEST", "BE", 40.0, 42.0, 200.0, "90.0"),
]


@pytest.fixture()
def mkt(tmp_path, monkeypatch):
    from backend.app.config.settings import settings
    from backend.app.repositories import bars_repository as bars
    from backend.app.services.data import market_data

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "stock.db"))
    bars._ready.clear()
    market_data.invalidate()
    monkeypatch.setattr(market_data, "MASTER_MIN_ROWS", 4)
    return bars


def load_day(bars, d=date(2026, 10, 6), rows=DAY, master=True):
    from backend.app.services.data import bhavcopy, market_data
    bhavcopy.ingest_day(d, csv_for(d, rows))
    if master:
        bars.upsert_companies([{"symbol": s, "name": f"{s} Ltd", "sector": "Tech"} for s in ("TCS", "INFY", "ILLIQ", "SMEX", "BEST")])
    market_data.invalidate()


# ---------------------------------------------------------------- parsing
def test_parse_handles_nse_headers_dashes_and_units():
    from backend.app.services.data import bhavcopy
    df = bhavcopy.parse_bhavcopy(csv_for(date(2026, 10, 6), DAY)).set_index("symbol")
    assert df.loc["TCS", "date"] == "2026-10-06" and df.loc["TCS", "turnover_cr"] == 50.0   # 5000 lacs = 50 cr
    assert pd.isna(df.loc["ILLIQ", "deliv_pct"]) and df.loc["TCS", "deliv_pct"] == 60.5


def test_parse_old_cm_format_and_unknown_format():
    from backend.app.services.data import bhavcopy
    old = b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\nTCS,EQ,1,2,1,2,2,1,100,50000000,06-OCT-2026,10,X\n"
    df = bhavcopy.parse_bhavcopy(old)
    assert df.loc[0, "turnover_cr"] == 5.0 and df.loc[0, "prev_close"] == 1
    with pytest.raises(ValueError, match="Columns found"):
        bhavcopy.parse_bhavcopy(b"A,B\n1,2\n")


# ------------------------------------------------------- scheduling logic
def ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_pending_dates_respects_publish_time_weekends_and_done():
    from backend.app.services.data.bhavcopy import pending_dates as pd_
    fri = date(2026, 10, 2)
    # Tue 11:37 -> today's file not due; Mon is the target
    assert pd_(ist(2026, 10, 6, 11, 37), fri, set(), 30) == [date(2026, 10, 5)]
    # Tue 19:00 -> Mon and Tue
    assert pd_(ist(2026, 10, 6, 19, 0), fri, set(), 30) == [date(2026, 10, 5), date(2026, 10, 6)]
    # Sunday: nothing new after Friday
    assert pd_(ist(2026, 10, 4, 20, 0), fri, set(), 30) == []
    # holiday already confirmed is skipped
    assert pd_(ist(2026, 10, 6, 19, 0), fri, {date(2026, 10, 5)}, 30) == [date(2026, 10, 6)]
    # empty db: backfill N weekdays ending at the last due day
    got = pd_(ist(2026, 10, 6, 19, 0), None, set(), 5)
    assert got == [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)]
    # full scan fills a gap even though last_stored is recent
    assert date(2026, 9, 30) in pd_(ist(2026, 10, 6, 19, 0), date(2026, 10, 5), set(), 5, full_scan=True)


# ---------------------------------------------------------------- refresh
def mock_client(files: dict, status_other=404):
    def handler(req: httpx.Request):
        name = req.url.path.rsplit("/", 1)[-1]
        if name in files:
            return httpx.Response(200, content=files[name])
        return httpx.Response(status_other, text="nope")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_loads_marks_holiday_and_is_idempotent(mkt):
    from backend.app.services.data import bhavcopy
    d5, d6 = date(2026, 10, 5), date(2026, 10, 6)
    files = {"sec_bhavdata_full_05102026.csv": csv_for(d5, DAY)}
    now = ist(2026, 10, 6, 19, 0)
    r = bhavcopy.refresh(now=now, client=mock_client(files), backfill_days=2, pause=0, with_master=False)
    assert [x["date"] for x in r["loaded"]] == ["2026-10-05"] and r["missing"] == ["2026-10-06"] and r["status"] == "ok"
    assert mkt.latest_date() == d5
    # tomorrow-ish retry: 6th is still 'missing' (not older than 7 days) so it is retried, 5th is not refetched
    seen = []
    c = httpx.Client(transport=httpx.MockTransport(lambda req: (seen.append(req.url.path), httpx.Response(404))[1]))
    bhavcopy.refresh(now=now, client=c, backfill_days=2, pause=0, with_master=False)
    assert all("06102026" in p for p in seen) and seen


def test_refresh_stops_on_block_and_logs_error(mkt):
    from backend.app.services.data import bhavcopy
    r = bhavcopy.refresh(now=ist(2026, 10, 6, 19, 0), client=mock_client({}, status_other=403), backfill_days=3,
                         pause=0, with_master=False)
    assert r["status"] == "error" and "403" in r["errors"][0]
    assert len(r["errors"]) == 1                      # did not hammer the remaining dates
    assert mkt.recent_log()[0]["status"] == "error"


def test_refresh_treats_stale_file_as_not_published(mkt):
    from backend.app.services.data import bhavcopy
    stale = csv_for(date(2026, 10, 5), DAY)           # server re-serves Monday's file for Tuesday
    r = bhavcopy.refresh(now=ist(2026, 10, 6, 19, 0), client=mock_client({"sec_bhavdata_full_06102026.csv": stale}),
                         backfill_days=1, pause=0, with_master=False)
    assert r["missing"] == ["2026-10-06"] and mkt.latest_date() is None


# ----------------------------------------------------------------- movers
def test_gainers_filters_illiquid_etf_and_sme(mkt):
    from backend.app.services.data import service as data
    load_day(mkt)
    out = data.market_movers("gainers")
    syms = [r["symbol"] for r in out["rows"]]
    assert syms == ["SMEX", "BEST", "TCS"]            # ILLIQ (turnover) and NIFTYBEES (not in master) excluded
    assert out["rows"][0]["Day Change %"] == 20.0 and out["rows"][0]["Change"] == 10.0
    assert out["total_matched"] == 3 and out["as_of"].startswith("2026-10-06")
    assert out["source"].startswith("NSE bhavcopy") and out["filters"]["min_turnover_cr"] == 1.0
    assert "SMEX" not in [r["symbol"] for r in data.market_movers("gainers", include_sme=False)["rows"]]
    assert "ILLIQ" in [r["symbol"] for r in data.market_movers("gainers", min_turnover_cr=0)["rows"]]


def test_losers_most_active_limit_and_bad_kind(mkt):
    from backend.app.services.data import service as data
    load_day(mkt)
    assert [r["symbol"] for r in data.market_movers("losers")["rows"]] == ["INFY"]
    assert data.market_movers("most_active")["rows"][0]["symbol"] == "TCS"
    assert len(data.market_movers("gainers", limit=1)["rows"]) == 1
    with pytest.raises(ValueError):
        data.market_movers("nonsense")


def test_no_data_gives_actionable_error(mkt):
    from backend.app.services.data import service as data
    with pytest.raises(ValueError, match="refresh_bhavcopy"):
        data.market_movers("gainers")


def test_quote_and_history(mkt):
    from backend.app.services.data import service as data
    for i, d in enumerate([date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]):
        load_day(mkt, d, [("TCS", "EQ", 100 + i, 101 + i, 5000.0, "50")], master=False)
    q = data.quote("tcs")
    assert q["kv"]["LTP"] == 103.0 and q["kv"]["Day Change %"] == pytest.approx(0.98, abs=0.01)
    assert "note" in q                                  # < 245 days of history: says so, doesn't fake 52W
    assert q["kv"]["52W High"] is None
    h = data.history("TCS", "5y")
    assert h["chart"]["close"] == [101.0, 102.0, 103.0] and "Only 3 trading days" in h["note"]
    with pytest.raises(ValueError):
        data.quote("NOPE")


def test_52_week_metrics_and_near_high(mkt):
    from backend.app.repositories import bars_repository as bars
    from backend.app.services.data import service as data, market_data
    days = [date(2025, 10, 1) + timedelta(days=i) for i in range(400)]
    days = [d for d in days if d.weekday() < 5][:255]
    rows = []
    for i, d in enumerate(days):
        up, down = 100 + i, 400 - i                       # UP climbs every day, DOWN falls
        for sym, px in (("UP", up), ("DOWN", down)):
            rows.append((sym, d.isoformat(), "EQ", px - 1, px, px + 1, px - 2, px, 1000.0, 5.0, 10, 50.0))
    cols = ["symbol", "date", "series", "prev_close", "open", "high", "low", "close", "volume", "turnover_cr", "trades", "deliv_pct"]
    df = pd.DataFrame(rows, columns=cols)
    bars.upsert_bars(df)
    market_data.invalidate()
    near = data.market_movers("near_52w_high", min_turnover_cr=0)
    assert [r["symbol"] for r in near["rows"]] == ["UP"]
    low = data.market_movers("near_52w_low", min_turnover_cr=0)
    assert [r["symbol"] for r in low["rows"]] == ["DOWN"]
    q = data.quote("UP")["kv"]
    assert q["1Y Return %"] is not None and q["52W High"] >= q["LTP"]


# --------------------------------------------------------------- screener
def test_screener_price_filters_do_not_call_providers(mkt, monkeypatch):
    from backend.app.services.data import service as data
    load_day(mkt)
    monkeypatch.setattr(data, "universe_rows", lambda: (_ for _ in ()).throw(AssertionError("provider called")))
    out = data.screener([{"metric": "Day Change %", "op": ">", "value": 1}])
    # no turnover floor in the screener; liquid stocks are listed first
    assert [r["symbol"] for r in out["rows"]] == ["TCS", "SMEX", "BEST", "ILLIQ"]
    assert out["total_matched"] == 4 and out["source"].startswith("NSE bhavcopy")


def test_screener_mixed_filters_limited_to_default_universe(mkt, monkeypatch):
    from backend.app.services.data import service as data
    load_day(mkt)
    monkeypatch.setattr(data, "universe_rows", lambda: [{"symbol": "TCS", "P/E": 24.0}, {"symbol": "INFY", "P/E": 40.0}])
    out = data.screener([{"metric": "Day Change %", "op": ">", "value": 0}, {"metric": "P/E", "op": "<", "value": 30}])
    assert [r["symbol"] for r in out["rows"]] == ["TCS"] and "default universe" in out["note"]


# ------------------------------------------------------------------- API
def test_api_movers_and_status(mkt):
    from backend.app.main import app
    load_day(mkt)
    c = TestClient(app)
    j = c.get("/api/movers?kind=gainers&limit=2").json()
    assert [r["symbol"] for r in j["rows"]] == ["SMEX", "BEST"]
    s = c.get("/api/data/status").json()
    assert s["latest_date"] == "2026-10-06" and s["bars"] == len(DAY) and "next_check" in s
    assert c.get("/api/quote/TCS").json()["kv"]["LTP"] == 102.0
    assert c.get("/api/movers?kind=bad").status_code == 400
