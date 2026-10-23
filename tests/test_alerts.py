"""Breakout alerts, result alerts, the event calendar and the watchlist view. No network, temp databases."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.repositories import bars_repository as bars, warehouse
from backend.app.services.alerts import engine, rules
from backend.app.services.events import calendar


def make_bars(n=300, step=1.0, start=100.0):
    """Rising series: close = start + step*i, high/low one rupee either side. Dates are sortable strings."""
    d0 = date(2025, 1, 1)
    return [{"date": (d0 + timedelta(days=i)).isoformat(), "open": start + step * i, "high": start + step * i + 1,
             "low": start + step * i - 1, "close": start + step * i} for i in range(n)]


# ------------------------------------------------------------------ pure rules
def test_52w_high_needs_close_above_prior_high_not_equal():
    b = make_bars(300)
    res = rules.evaluate("high_52w", {}, b, 299, "X")
    assert res.ready and not res.triggered                 # close 399 vs prior high 399 (close+1 of bar 298): equal, no fire
    b[299]["close"] = 405.0
    res = rules.evaluate("high_52w", {}, b, 299, "X")
    assert res.triggered and "closed above its 52-week high" in res.title and "Rs 405.00" in res.body


def test_52w_low_fires_below_prior_low():
    b = make_bars(300, step=-0.1, start=500)
    b[299]["close"] = 1.0
    assert rules.evaluate("low_52w", {}, b, 299, "X").triggered


def test_not_enough_history_is_not_ready_not_false():
    res = rules.evaluate("high_52w", {}, make_bars(60), 59, "X")
    assert not res.ready and not res.triggered and "at least 120" in res.note
    assert not rules.evaluate("ema_cross", {"fast": 20, "slow": 50, "direction": "up"}, make_bars(60), 59, "X").ready


def test_ema_cross_up_fires_only_on_the_cross_day():
    closes = [100.0] * 150 + [90.0] * 30 + [150.0] * 30       # slump then sharp rally
    b = [{"date": (date(2025, 1, 1) + timedelta(days=i)).isoformat(), "high": c, "low": c, "close": c} for i, c in enumerate(closes)]
    params = {"fast": 5, "slow": 20, "direction": "up"}
    fired = [i for i in range(60, len(b)) if rules.evaluate("ema_cross", params, b, i, "X").triggered]
    assert len(fired) == 1 and fired[0] >= 180
    down = [i for i in range(60, len(b)) if rules.evaluate("ema_cross", {**params, "direction": "down"}, b, i, "X").triggered]
    assert down and down[0] < fired[0]                          # the slump crossed down first
    assert "context, not a call" in rules.evaluate("ema_cross", params, b, fired[0], "X").body


def test_level_cross_and_direction():
    b = make_bars(10, start=100)
    p = {"level": 104.5, "direction": "above"}
    hits = [i for i in range(1, 10) if rules.evaluate("level", p, b, i, "X").triggered]
    assert hits == [5]                                          # closes 104 -> 105 on bar 5
    assert not [i for i in range(1, 10) if rules.evaluate("level", {**p, "direction": "below"}, b, i, "X").triggered]


def test_split_like_jump_adds_a_caveat():
    b = make_bars(300)
    b[200]["close"] *= 0.4
    res = rules.evaluate("high_52w", {}, b, 299, "X")
    assert "not adjusted for splits" in (res.note + res.body)


@pytest.mark.parametrize("preset,params", [("ema_cross", {"fast": 50, "slow": 20}), ("ema_cross", {"fast": 1, "slow": 20}),
                                           ("level", {}), ("level", {"level": "abc"}), ("level", {"level": 5, "direction": "up"}),
                                           ("nope", {})])
def test_bad_params_rejected(preset, params):
    with pytest.raises(ValueError):
        rules.normalize_params(preset, params)


def test_results_classifier_separates_outcome_from_intimation():
    ok = lambda t, **k: rules.is_results_announcement({"title": t, "doc_type": "announcement", **k})
    assert ok("Unaudited Financial Results for the quarter ended 30 Sep 2026")
    assert ok("Outcome of Board Meeting - Q2 results")
    assert not ok("Board Meeting Intimation for consideration of Financial Results")
    assert not ok("Intimation of board meeting to consider financial results")
    assert not ok("Trading window closure")
    assert not ok("Unaudited Financial Results", source="demo-seed")           # sample data never counts
    assert rules.is_results_announcement({"title": "Q3 highlights", "doc_type": "result", "source": "x"})


# ------------------------------------------------------------------ with data
@pytest.fixture()
def client(env):
    from backend.app.main import app
    calendar.clear_cache()
    bars.upsert_companies([{"symbol": s, "name": n, "sector": "Tech"} for s, n in
                           (("TCS", "Tata Consultancy"), ("INFY", "Infosys"), ("RELIANCE", "Reliance Industries"))])
    return TestClient(app)


def load_bars(symbol, closes, start=date(2025, 1, 1)):
    import pandas as pd
    rows = [{"symbol": symbol, "date": (start + timedelta(days=i)).isoformat(), "series": "EQ", "prev_close": closes[max(0, i - 1)],
             "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000, "trades": 100, "turnover_cr": 5.0, "deliv_pct": 50.0}
            for i, c in enumerate(closes)]
    bars.upsert_bars(pd.DataFrame(rows))


def test_breakout_alert_waits_for_new_bars_then_fires_once(client):
    load_bars("TCS", [100 + i * 0.5 for i in range(300)])
    a = client.post("/api/alerts", json={"kind": "breakout", "preset": "high_52w", "symbol": "tcs"}).json()
    assert a["symbol"] == "TCS" and a["label"] == "Close above 52-week high" and a["last_eval_date"]
    assert a["status"]["items"][0]["state"] == "waiting"
    r = client.post("/api/alerts/check").json()
    assert r["new"] == 0                                                     # no back-fill of history

    # a new session closes well above everything before it
    load_bars("TCS", [100 + i * 0.5 for i in range(300)] + [400.0])
    r = client.post("/api/alerts/check").json()
    assert r["new"] == 1 and r["unread"] == 1
    assert client.post("/api/alerts/check").json()["new"] == 0              # idempotent
    n = client.get("/api/notifications").json()["items"][0]
    assert n["symbol"] == "TCS" and "52-week high" in n["title"] and not n["read"] and n["payload"]["close"] == 400.0
    assert client.get("/api/alerts").json()["items"][0]["status"]["items"][0]["state"] == "triggered"


def test_watchlist_scope_covers_the_watchlist_and_ignores_others(client):
    flat = [100.0] * 300
    for s in ("TCS", "INFY", "RELIANCE"):
        load_bars(s, flat)
    client.post("/api/watchlist/TCS"); client.post("/api/watchlist/INFY")
    client.post("/api/alerts", json={"kind": "breakout", "preset": "level", "params": {"level": 150, "direction": "above"}})
    for s in ("TCS", "INFY", "RELIANCE"):
        load_bars(s, flat + [160.0])
    assert client.post("/api/alerts/check").json()["new"] == 2
    assert {n["symbol"] for n in client.get("/api/notifications").json()["items"]} == {"TCS", "INFY"}


def test_inactive_alert_does_not_fire_and_delete_works(client):
    load_bars("TCS", [100.0] * 300)
    a = client.post("/api/alerts", json={"kind": "breakout", "preset": "level", "symbol": "TCS", "params": {"level": 150}}).json()
    assert client.patch(f"/api/alerts/{a['id']}", json={"active": False}).json()["active"] is False
    load_bars("TCS", [100.0] * 300 + [200.0])
    assert client.post("/api/alerts/check").json()["alerts"] == 0
    assert client.delete(f"/api/alerts/{a['id']}").status_code == 200
    assert client.delete(f"/api/alerts/{a['id']}").status_code == 404
    assert client.patch("/api/alerts/zzz", json={"active": True}).status_code == 404


def test_catch_up_after_downtime_fires_for_the_missed_session(client):
    load_bars("TCS", [100.0] * 300)
    client.post("/api/alerts", json={"kind": "breakout", "preset": "level", "symbol": "TCS", "params": {"level": 150}})
    load_bars("TCS", [100.0] * 300 + [160.0, 161.0, 162.0])                  # three sessions arrive before the next check
    assert client.post("/api/alerts/check").json()["new"] == 1               # one crossing, on the first of them


def test_create_validation(client):
    bad = lambda **b: client.post("/api/alerts", json=b)
    assert bad(kind="breakout", preset="level", symbol="NOPE", params={"level": 1}).status_code == 400
    assert bad(kind="breakout", preset="ema_cross", params={"fast": 60, "slow": 20}).status_code == 400
    assert bad(kind="breakout", preset="zzz").status_code == 400
    assert bad(kind="weird").status_code == 400
    assert client.get("/api/alerts/presets").json()["breakout"][0]["id"] == "high_52w"


def test_alert_limit(client, monkeypatch):
    monkeypatch.setattr(engine, "MAX_ALERTS", 2)
    for _ in range(2):
        assert client.post("/api/alerts", json={"kind": "result"}).status_code == 201
    assert client.post("/api/alerts", json={"kind": "result"}).status_code == 400


# ---------------------------------------------------------------- results alerts
def filing(symbol, title, **kw):
    return warehouse.insert_filing(symbol, kw.pop("doc_type", "announcement"), title, kw.pop("body", title), source=kw.pop("source", "bse-announcements"), **kw)


def test_result_alert_fires_for_results_not_intimations_and_dedupes_loader_reruns(client):
    client.post("/api/watchlist/TCS")
    a = client.post("/api/alerts", json={"kind": "result"}).json()                       # whole watchlist
    filing("TCS", "Board Meeting Intimation for Q2 Financial Results")
    filing("TCS", "Unaudited Financial Results for the quarter ended 30 Sep 2026", url="https://x/y.pdf")
    filing("TCS", "Unaudited Financial Results for the quarter ended 30 Sep 2026")       # loader re-run duplicate
    filing("INFY", "Unaudited Financial Results for the quarter ended 30 Sep 2026")      # not on the watchlist
    assert client.post("/api/alerts/check").json()["new"] == 1
    n = client.get("/api/notifications").json()["items"]
    assert len(n) == 1 and n[0]["symbol"] == "TCS" and n[0]["title"] == "TCS announced results"
    assert n[0]["payload"]["source"] == "bse-announcements"
    assert client.post("/api/alerts/check").json()["new"] == 0
    assert client.get("/api/alerts").json()["items"][0]["status"]["text"].startswith("Latest results notice seen: TCS")
    assert a["status"]["pending"] == 0


def test_result_alert_ignores_existing_unless_asked_and_never_demo_seed(client):
    filing("TCS", "Unaudited Financial Results for the quarter ended 30 Jun 2026")
    filing("TCS", "Q3 FY25 consolidated results highlight", doc_type="result", source="demo-seed")
    client.post("/api/alerts", json={"kind": "result", "symbol": "TCS"})
    assert client.post("/api/alerts/check").json()["new"] == 0                          # existed before the alert
    client.post("/api/alerts", json={"kind": "result", "symbol": "TCS", "include_existing": True})
    assert client.post("/api/alerts/check").json()["new"] == 1                          # real one only, not demo-seed


# ------------------------------------------------------------------ notifications
def test_mark_read_and_delete(client):
    from backend.app.repositories import alert_repository as repo
    for i in range(3):
        repo.add_notification(None, "breakout", "TCS", f"t{i}", "", {}, f"k{i}")
    assert not repo.add_notification(None, "breakout", "TCS", "dup", "", {}, "k0")       # same key: ignored
    assert client.get("/api/notifications/unread-count").json() == {"unread": 3}
    ids = [n["id"] for n in client.get("/api/notifications").json()["items"]]
    assert client.post("/api/notifications/read", json={"ids": ids[:1]}).json()["unread"] == 2
    assert len(client.get("/api/notifications?unread=true").json()["items"]) == 2
    assert client.post("/api/notifications/read", json={}).json()["unread"] == 0
    assert client.delete(f"/api/notifications/{ids[0]}").status_code == 200
    assert client.delete(f"/api/notifications/{ids[0]}").status_code == 404


# ----------------------------------------------------------------------- calendar
TODAY = date(2026, 10, 16)


@pytest.fixture()
def cal(client, monkeypatch):
    monkeypatch.setattr(calendar, "today", lambda: TODAY)
    feeds = {"TCS": {"Earnings Date": [date(2026, 10, 20), date(2026, 10, 23)], "Ex-Dividend Date": date(2026, 10, 25),
                     "Dividend Date": date(2026, 11, 20)},
             "INFY": {"Earnings Date": [date(2026, 10, 15)]},            # already past -> nothing upcoming
             "RELIANCE": None}                                           # provider failure

    def fake(symbol):
        if feeds.get(symbol) is None:
            raise ConnectionError("blocked")
        return feeds[symbol]
    monkeypatch.setattr(calendar, "fetch_provider_calendar", fake)
    return client


def test_calendar_labels_estimates_and_reports_what_it_could_not_find(cal):
    for s in ("TCS", "INFY", "RELIANCE"):
        cal.post(f"/api/watchlist/{s}")
    d = cal.get("/api/calendar?days=60").json()
    assert d["scope"] == "watchlist" and d["from"] == "2026-10-16"
    kinds = [(e["symbol"], e["type"], e["date"], e["estimated"]) for e in d["events"]]
    assert kinds == [("TCS", "results", "2026-10-20", True), ("TCS", "dividend_ex", "2026-10-25", True),
                     ("TCS", "dividend_pay", "2026-11-20", True)]
    assert d["events"][0]["date_end"] == "2026-10-23" and d["events"][0]["name"] == "Tata Consultancy"
    assert {u["symbol"]: u["reason"] for u in d["unavailable"]}.keys() == {"INFY", "RELIANCE"}
    assert any("Could not reach" in u["reason"] for u in d["unavailable"] if u["symbol"] == "RELIANCE")
    assert d["notes"] and d["sources"][0]["source"] == calendar.PROVIDER_SOURCE


def test_calendar_window_limits_events(cal):
    cal.post("/api/watchlist/TCS")
    d = cal.get("/api/calendar?days=7").json()                       # window ends 23 Oct
    assert [e["type"] for e in d["events"]] == ["results"]            # ex-div (25 Oct) and payment (Nov) fall outside it


def test_confirmed_warehouse_event_beats_provider_estimate(cal):
    warehouse.insert_event("TCS", "results", "2026-10-21", '{"title": "Board meeting to approve Q2 results"}')
    assert not warehouse.insert_event("TCS", "results", "2026-10-21")           # duplicate ignored
    cal.post("/api/watchlist/TCS")
    d = cal.get("/api/calendar?days=60").json()
    res = [e for e in d["events"] if e["type"] == "results"]
    assert len(res) == 1 and res[0]["date"] == "2026-10-21" and res[0]["estimated"] is False
    assert res[0]["title"] == "Board meeting to approve Q2 results"
    assert [e["type"] for e in d["events"]] == ["results", "dividend_ex", "dividend_pay"]   # provider still supplies dividends
    assert d["events"][0]["source"] == calendar.WAREHOUSE_SOURCE and len(d["sources"]) == 2
