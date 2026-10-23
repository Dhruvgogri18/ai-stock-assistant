"""Live prices (Angel One first, bhavcopy fallback): overlay maths, coverage rules, labelling, throttling. No network."""
import time
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config.settings import settings
from backend.app.providers import angel_one
from backend.app.repositories import bars_repository as bars
from backend.app.services.data import live_prices as lp, market_data
from backend.app.services.data.bhavcopy import IST

LAST = date(2026, 10, 16)                              # last stored session (Friday)
MON = datetime(2026, 10, 19, 10, 30, tzinfo=IST)        # Monday 10:30 IST: inside the live window
SYMS = ["TCS", "INFY", "ILLIQ", "SMEX", "BEST"]


def load(n=300, end=LAST):
    start = end - timedelta(days=n - 1)
    rows = []
    for s in SYMS:
        for i in range(n):
            rows.append({"symbol": s, "date": (start + timedelta(days=i)).isoformat(), "series": {"SMEX": "SM", "BEST": "BE"}.get(s, "EQ"),
                         "prev_close": 100.0, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000, "trades": 10,
                         "turnover_cr": 5.0, "deliv_pct": 50.0})
    bars.upsert_bars(pd.DataFrame(rows))
    bars.upsert_companies([{"symbol": s, "name": f"{s} Ltd", "sector": "Tech" if s in ("TCS", "INFY") else ""} for s in SYMS])


class Feed:
    """Scripted Angel One: {symbol: price}; everything else is 'no quote'. Records every request."""
    def __init__(self, prices, error=None, **extra):
        self.prices, self.error, self.calls, self.extra = prices, error, [], extra

    def __call__(self, symbols):
        self.calls.append(list(symbols))
        if self.error:
            raise self.error
        found = {s: {"ltp": p, "open": 100.0, "high": max(p, 101.0), "low": min(p, 99.0), "prev_close": 100.0, "volume": 1_000_000.0,
                     "avg_price": 100.0, "feed_time": None, **self.extra} for s, p in self.prices.items() if s in symbols}
        return found, [s for s in symbols if s not in found]


@pytest.fixture()
def client(env, monkeypatch):
    from backend.app.main import app
    market_data.invalidate(); lp.reset_state()
    monkeypatch.setattr(market_data, "MASTER_MIN_ROWS", 4)
    monkeypatch.setattr(settings, "prices_batch_pause_sec", 0)
    monkeypatch.setattr(lp, "configured", lambda: True)
    monkeypatch.setattr(lp, "now_ist", lambda: MON)
    load()
    yield TestClient(app)
    market_data.invalidate(); lp.reset_state()


def feed(monkeypatch, prices=None, **kw):
    f = Feed({"TCS": 105.0, "INFY": 99.0, "ILLIQ": 100.0, "SMEX": 110.0, "BEST": 101.0} if prices is None else prices, **kw)
    monkeypatch.setattr(lp, "fetch_chunk", f)
    return f


def warm(feed_obj=None):
    lp.demand(); lp.refresh_universe_once()


def run(c, **d):
    r = c.post("/api/screeners/run", json={"definition": d})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ window
@pytest.mark.parametrize("when,stored,expected", [
    (datetime(2026, 10, 19, 9, 14, tzinfo=IST), LAST, False), (datetime(2026, 10, 19, 9, 15, tzinfo=IST), LAST, True),
    (datetime(2026, 10, 19, 17, 0, tzinfo=IST), LAST, True),                        # after the close, before today's bhavcopy is stored
    (datetime(2026, 10, 19, 17, 0, tzinfo=IST), date(2026, 10, 19), False),         # today's bhavcopy stored: it is final
    (datetime(2026, 10, 17, 11, 0, tzinfo=IST), LAST, False)])                      # Saturday
def test_live_window(when, stored, expected):
    assert lp.in_window(when, stored) is expected


def test_switched_off_means_never_live(monkeypatch):
    monkeypatch.setattr(settings, "prices_live", False)
    assert lp.in_window(MON, LAST) is False


# ----------------------------------------------------------- overlay maths
def test_apply_quotes_computes_everything_from_the_quote(client):
    base = market_data.eod_snapshot().df
    q = {"TCS": {"price": 105.0, "prev_close": 100.0, "open": 101.0, "high": 106.0, "low": 100.0, "volume": 1e6, "avg_price": 104.0}}
    r = lp.apply_quotes(base, q).loc["TCS"]
    assert r["LTP"] == 105.0 and r["Change"] == 5.0 and r["Day Change %"] == pytest.approx(5.0)
    assert r["Turnover (Cr)"] == pytest.approx(10.4) and r["Volume"] == 1e6 and pd.isna(r["Delivery %"])
    assert r["1M Return %"] == pytest.approx(5.0) and r["1Y Return %"] == pytest.approx(5.0)
    assert r["52W High"] == 106.0 and r["% From 52W High"] == pytest.approx((105 / 106 - 1) * 100)
    assert r["52W Low"] == 99.0
    same = lp.apply_quotes(base, q).loc["INFY"]
    assert same["LTP"] == 100.0 and same["Delivery %"] == 50.0                      # no quote: untouched (end of day)


def test_apply_quotes_clear_missing_drops_prices_not_names(client):
    base = market_data.eod_snapshot().df
    df = lp.apply_quotes(base, {"TCS": {"price": 105.0, "prev_close": 100.0}}, universe=base["series"].isin(["EQ", "BE", "SM"]), clear_missing=True)
    assert pd.isna(df.loc["INFY", "LTP"]) and pd.isna(df.loc["INFY", "Day Change %"]) and df.loc["INFY", "name"] == "INFY Ltd"
    assert df.loc["TCS", "LTP"] == 105.0


def test_missing_previous_close_gives_no_day_change_not_a_guess(client):
    base = market_data.eod_snapshot().df
    r = lp.apply_quotes(base, {"TCS": {"price": 105.0, "prev_close": None}}).loc["TCS"]
    assert r["LTP"] == 105.0 and pd.isna(r["Day Change %"]) and pd.isna(r["Change"])


# -------------------------------------------------------- snapshot overlay
def test_not_configured_stays_end_of_day_and_says_why(client, monkeypatch):
    monkeypatch.setattr(lp, "configured", lambda: False)
    s = market_data.snapshot()
    assert s.mode == "eod" and "not configured" in s.live_note and market_data.screen_meta()["price_mode"] == "eod"


def test_live_prices_loading_keeps_end_of_day_until_enough_stocks_answer(client, monkeypatch):
    feed(monkeypatch, {"TCS": 105.0, "INFY": 99.0})                                 # 2 of 5 stocks
    warm()
    s = market_data.snapshot()
    assert s.mode == "eod" and s.live_progress == (2, 5) and "2 of 5" in s.live_note
    assert market_data.snapshot().df.loc["TCS", "LTP"] == 100.0                     # still yesterday's close, labelled as such


def test_full_coverage_switches_everything_to_live(client, monkeypatch):
    f = feed(monkeypatch); warm()
    s = market_data.snapshot()
    assert s.mode == "live" and s.live_at.endswith("IST") and s.df.loc["TCS", "LTP"] == 105.0
    assert s.df.loc["SMEX", "Day Change %"] == pytest.approx(10.0)
    assert len(f.calls) == 1 and sorted(f.calls[0]) == sorted(SYMS)                 # whole market in one request here (<= 50)
    m = market_data.screen_meta()
    assert m["price_mode"] == "live" and "live" in m["as_of"] and "Angel One" in m["source"]


def test_stale_quotes_are_not_used(client, monkeypatch):
    feed(monkeypatch); warm()
    for q in lp._board.values():
        q["at"] -= settings.prices_max_age_sec + 5
    lp._version += 1
    monkeypatch.setattr(lp, "fetch_chunk", Feed({}))                                # and nothing fresh arrives
    assert market_data.snapshot().mode == "eod"


def test_a_stock_without_a_quote_is_left_out_of_price_filters_not_shown_stale(client, monkeypatch):
    monkeypatch.setattr(settings, "prices_min_coverage", 0.75)
    feed(monkeypatch, {"TCS": 105.0, "INFY": 99.0, "ILLIQ": 100.0, "SMEX": 110.0}); warm()                  # BEST has no quote
    r = run(client, columns=["LTP"], filters=[{"metric": "Day Change %", "op": ">=", "value": -100}])
    assert r["price_mode"] == "live" and "BEST" not in [x["symbol"] for x in r["rows"]] and r["total"] == 4
    assert any("1 stocks have no live price" in n or "1 stocks" in n for n in r["notes"])


# ----------------------------------------------------- screener, end to end
def test_screener_uses_live_prices_and_explains_delivery(client, monkeypatch):
    feed(monkeypatch); warm()
    r = run(client, filters=[{"metric": "Day Change %", "op": ">", "value": 3}], columns=["Day Change %", "Delivery %", "Turnover (Cr)"], sort={"metric": "Day Change %", "dir": "desc"})
    assert r["price_mode"] == "live" and "(live" in r["as_of"] and "Angel One" in r["source"]
    assert [x["symbol"] for x in r["rows"]] == ["SMEX", "TCS"] and r["rows"][0]["Day Change %"] == 10.0
    assert all(x["Delivery %"] is None for x in r["rows"]) and r["rows"][1]["Turnover (Cr)"] == 10.0
    assert any("Delivery % is published only after the close" in n for n in r["notes"])
    assert not any("No Delivery %" in n for n in r["notes"])                         # empty by design, not "not loaded"
    assert run(client, filters=[{"metric": "Delivery %", "op": ">", "value": 10}])["total"] == 0


def test_screener_falls_back_to_bhavcopy_when_angel_fails_and_does_not_hammer_it(client, monkeypatch):
    f = feed(monkeypatch, error=ConnectionError("Angel login failed: invalid totp"))
    lp.demand(); lp.refresh_universe_once()
    r = run(client, filters=[{"metric": "Day Change %", "op": ">", "value": -100}])
    assert r["price_mode"] == "eod" and "(market close)" in r["as_of"] and any("failing" in n for n in r["notes"])
    assert r["rows"][0]["LTP"] == 100.0
    calls = len(f.calls)
    lp.refresh_universe_once(); run(client); client.get("/api/watchlist/overview")
    assert len(f.calls) == calls and lp.blocked_for() > 0                            # backed off: no new broker calls


def test_movers_say_live_when_live(client, monkeypatch):
    feed(monkeypatch); warm()
    m = market_data.movers("gainers", min_turnover_cr=0)
    assert m["price_mode"] == "live" and m["rows"][0]["symbol"] == "SMEX" and "Live prices from Angel One" in m["note"]


# ---------------------------------------------------------------- watchlist
def test_watchlist_is_live_per_row_and_refreshes_itself(client, monkeypatch):
    f = feed(monkeypatch)
    client.post("/api/watchlist/TCS"); client.post("/api/watchlist/INFY")
    d = client.get("/api/watchlist/overview").json()
    assert d["mode"] == "live" and d["refresh_sec"] == 15 and "live" in d["as_of"] and "Angel One" in d["source"]
    by = {r["symbol"]: r for r in d["rows"]}
    assert by["TCS"]["LTP"] == 105.0 and by["TCS"]["Day Change %"] == pytest.approx(5.0) and by["TCS"]["price_source"] == "live"
    n = len(f.calls)
    client.get("/api/watchlist/overview")
    assert len(f.calls) == n                                                         # inside the 10 s cache: no new request


def test_watchlist_mixes_live_and_end_of_day_and_labels_each_row(client, monkeypatch):
    feed(monkeypatch, {"TCS": 105.0})
    client.post("/api/watchlist/TCS"); client.post("/api/watchlist/INFY"); client.post("/api/watchlist/ZZZNOPE")
    d = client.get("/api/watchlist/overview").json()
    by = {r["symbol"]: r for r in d["rows"]}
    assert d["mode"] == "mixed" and by["TCS"]["price_source"] == "live" and by["INFY"]["price_source"] == "eod" and by["INFY"]["LTP"] == 100.0
    assert by["ZZZNOPE"]["missing"] is True and any("INFY" in n for n in d["notes"])


def test_watchlist_falls_back_to_bhavcopy_when_angel_is_down(client, monkeypatch):
    feed(monkeypatch, error=TimeoutError("timed out"))
    client.post("/api/watchlist/TCS")
    d = client.get("/api/watchlist/overview").json()
    assert d["mode"] == "eod" and d["refresh_sec"] == 0 and "(market close)" in d["as_of"] and d["rows"][0]["price_source"] == "eod"
    assert any("TimeoutError" in n for n in d["notes"])


def test_watchlist_after_bhavcopy_is_stored_makes_no_live_call(client, monkeypatch):
    f = feed(monkeypatch)
    monkeypatch.setattr(lp, "now_ist", lambda: datetime(2026, 10, 19, 19, 0, tzinfo=IST))
    bars.upsert_bars(pd.DataFrame([{"symbol": "TCS", "date": "2026-10-19", "series": "EQ", "prev_close": 100.0, "open": 100.0, "high": 104.0,
                                    "low": 99.0, "close": 103.0, "volume": 5, "trades": 1, "turnover_cr": 1.0, "deliv_pct": 40.0}]))
    market_data.invalidate()
    client.post("/api/watchlist/TCS")
    d = client.get("/api/watchlist/overview").json()
    assert f.calls == [] and d["mode"] == "eod" and d["rows"][0]["LTP"] == 103.0


# ---------------------------------------------------- throttle and batching
def test_requests_are_split_into_batches_of_50(client, monkeypatch):
    f = feed(monkeypatch, {f"S{i}": 10.0 for i in range(120)})
    got, info = lp.get_quotes([f"S{i}" for i in range(120)])
    assert [len(c) for c in f.calls] == [50, 50, 20] and len(got) == 120 and info["errors"] == []


def test_requests_are_spaced_by_the_shared_pause(client, monkeypatch):
    monkeypatch.setattr(settings, "prices_batch_pause_sec", 0.25)
    stamps = []
    def fake(symbols):
        stamps.append(time.time()); return {}, list(symbols)
    monkeypatch.setattr(lp, "fetch_chunk", fake)
    lp.get_quotes([f"S{i}" for i in range(120)])
    assert len(stamps) == 3 and all(b - a >= 0.24 for a, b in zip(stamps, stamps[1:]))


def test_switched_off_makes_no_calls(client, monkeypatch):
    f = feed(monkeypatch)
    monkeypatch.setattr(settings, "prices_live", False)
    client.post("/api/watchlist/TCS")
    assert client.get("/api/watchlist/overview").json()["mode"] == "eod" and f.calls == []
    assert market_data.snapshot().mode == "eod"


def test_status_endpoint_reports_each_state(client, monkeypatch):
    feed(monkeypatch); warm()
    s = client.get("/api/prices/status").json()
    assert s["state"] == "live" and s["coverage"] == {"fresh": 5, "total": 5} and s["window"] and s["market_open"]
    monkeypatch.setattr(lp, "configured", lambda: False)
    assert client.get("/api/prices/status").json()["state"] == "unconfigured"
    monkeypatch.setattr(lp, "configured", lambda: True)
    monkeypatch.setattr(lp, "now_ist", lambda: datetime(2026, 10, 17, 11, 0, tzinfo=IST))
    assert client.get("/api/prices/status").json()["state"] == "eod"
    monkeypatch.setattr(lp, "now_ist", lambda: MON)
    lp._failed(RuntimeError("bad totp"))
    s = client.get("/api/prices/status").json()
    assert s["state"] == "failing" and s["backoff_sec"] > 0 and "bad totp" in s["last_error"]


# ------------------------------------------------------- Angel provider parts
def test_token_index_prefers_equity_series_and_handles_hyphenated_names(monkeypatch):
    monkeypatch.setattr(angel_one, "_master", [
        {"exch_seg": "NSE", "symbol": "RELIANCE-EQ", "token": "2885", "name": "RELIANCE"}, {"exch_seg": "NSE", "symbol": "RELIANCE-BE", "token": "9", "name": "RELIANCE"},
        {"exch_seg": "NSE", "symbol": "BAJAJ-AUTO-EQ", "token": "16669", "name": "BAJAJ-AUTO"}, {"exch_seg": "NSE", "symbol": "XYZ-SM", "token": "77", "name": "XYZ"},
        {"exch_seg": "NFO", "symbol": "RELIANCE25OCTFUT", "token": "5", "name": "RELIANCE"}, {"exch_seg": "BSE", "symbol": "ABC", "token": "1", "name": "ABC"}])
    monkeypatch.setattr(angel_one, "_master_loaded_at", time.time()); monkeypatch.setattr(angel_one, "_index", None)
    assert angel_one.token_index() == {"RELIANCE": ("2885", "RELIANCE-EQ"), "BAJAJ-AUTO": ("16669", "BAJAJ-AUTO-EQ"), "XYZ": ("77", "XYZ-SM")}


def test_quote_parser_keeps_good_rows_and_rejects_bad_shapes():
    ok = {"status": True, "data": {"fetched": [
        {"symbolToken": "2885", "ltp": "1215.5", "open": 1200, "high": 1220, "low": 1195, "close": 1202, "tradeVolume": 5e6, "avgPrice": 1210.2, "exchFeedTime": "t"},
        {"symbolToken": "77", "ltp": 0}, {"symbolToken": "999", "ltp": 5}, {"symbolToken": "2885"}], "unfetched": []}}
    out = angel_one.parse_quote_response(ok, {"2885": "RELIANCE", "77": "XYZ"})
    assert list(out) == ["RELIANCE"] and out["RELIANCE"]["ltp"] == 1215.5 and out["RELIANCE"]["prev_close"] == 1202.0
    for bad in ({"status": False, "message": "Invalid Token"}, {"status": True, "data": []}, {"status": True, "data": {"nope": 1}}):
        with pytest.raises(ValueError):
            angel_one.parse_quote_response(bad, {})