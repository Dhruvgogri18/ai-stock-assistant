"""Live ("touch") breakout alerts: Angel One prices during market hours, end-of-day fallback, dedupe, backoff. No network."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config.settings import settings
from backend.app.repositories import bars_repository as bars
from backend.app.services.alerts import live, rules
from backend.app.services.data.bhavcopy import IST

LAST_STORED = date(2026, 10, 16)                       # a Friday: the previous session
MON_1030 = datetime(2026, 10, 19, 10, 30, tzinfo=IST)  # a Monday during market hours
TODAY = "2026-10-19"


def load(symbol, closes, end=LAST_STORED, spread=1.0):
    start = end - timedelta(days=len(closes) - 1)
    rows = [{"symbol": symbol, "date": (start + timedelta(days=i)).isoformat(), "series": "EQ", "prev_close": closes[max(0, i - 1)],
             "open": c, "high": c + spread, "low": c - spread, "close": c, "volume": 1000, "trades": 10, "turnover_cr": 5.0, "deliv_pct": 50.0}
            for i, c in enumerate(closes)]
    bars.upsert_bars(pd.DataFrame(rows))


class Feed:
    """Scripted stand-in for Angel One: prices by symbol, or an exception."""
    def __init__(self, **prices):
        self.prices, self.calls = prices, []

    def __call__(self, symbol):
        self.calls.append(symbol)
        v = self.prices[symbol]
        if isinstance(v, Exception):
            raise v
        return v


@pytest.fixture()
def client(env, monkeypatch):
    from backend.app.main import app
    live.reset_state()
    monkeypatch.setattr(settings, "alerts_live_pause_sec", 0)
    monkeypatch.setattr(live, "configured", lambda: True)
    monkeypatch.setattr(live, "now_ist", lambda: MON_1030)
    bars.upsert_companies([{"symbol": s, "name": s, "sector": "x"} for s in ("TCS", "INFY", "RELIANCE")])
    return TestClient(app)


def feed(monkeypatch, **prices):
    f = Feed(**prices)
    monkeypatch.setattr(live, "fetch_ltp", f)
    return f


def alert(c, symbol="TCS", preset="level", level=150, direction="above", trigger="touch"):
    params = {"trigger": trigger} if preset != "level" else {"level": level, "direction": direction, "trigger": trigger}
    r = c.post("/api/alerts", json={"kind": "breakout", "preset": preset, "symbol": symbol, "params": params})
    assert r.status_code == 201, r.text
    return r.json()


def notes(c):
    return c.get("/api/notifications").json()["items"]


# ------------------------------------------------------------------ hours
@pytest.mark.parametrize("when,expected", [((2026, 10, 19, 9, 14), False), ((2026, 10, 19, 9, 15), True), ((2026, 10, 19, 15, 30), True),
                                           ((2026, 10, 19, 15, 31), False), ((2026, 10, 17, 11, 0), False), ((2026, 10, 18, 11, 0), False)])
def test_market_hours_are_weekday_0915_to_1530_ist(when, expected):
    assert live.market_open(datetime(*when, tzinfo=IST)) is expected


# --------------------------------------------------------------- creation
def test_trigger_is_validated_and_shown_in_the_label(client):
    a = alert(client)
    assert a["params"]["trigger"] == "touch" and a["label"] == "Price trades above Rs 150.00 (live)"
    assert alert(client, trigger="close")["label"] == "Close crosses above Rs 150.00"
    bad = client.post("/api/alerts", json={"kind": "breakout", "preset": "level", "params": {"level": 1, "trigger": "sometimes"}})
    assert bad.status_code == 400
    ema = client.post("/api/alerts", json={"kind": "breakout", "preset": "ema_cross", "params": {"trigger": "touch"}}).json()
    assert "trigger" not in ema["params"]                                     # moving averages are close-based only


# ------------------------------------------------------------- live firing
def test_live_cross_fires_once_with_honest_wording(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client)
    f = feed(monkeypatch, TCS=160.0)
    r = live.poll_once()
    assert r["status"] == "ok" and r["fired"] == 1 and r["symbols"] == 1
    n = notes(client)[0]
    assert n["title"] == "TCS traded above Rs 150.00"
    assert "Live price from Angel One SmartAPI at 10:30 IST" in n["body"] and "move back before the close" in n["body"]
    assert n["payload"]["mode"] == "live" and n["payload"]["ltp"] == 160.0
    assert live.poll_once()["fired"] == 0                                      # still above: no repeat
    f.prices["TCS"] = 140.0; live.poll_once(); f.prices["TCS"] = 161.0
    assert live.poll_once()["fired"] == 0 and len(notes(client)) == 1          # flapping across the level: once per day


def test_level_crossed_on_an_earlier_day_never_fires(client, monkeypatch):
    load("TCS", [100.0] * 299 + [160.0])                                       # already closed above 150 on Friday
    alert(client)
    feed(monkeypatch, TCS=170.0)
    assert live.poll_once()["fired"] == 0


def test_restart_mid_day_neither_misses_nor_repeats(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client)
    feed(monkeypatch, TCS=160.0)
    live.reset_state()                                                         # app restarted after the cross
    assert live.poll_once()["fired"] == 1                                      # baseline = previous close, so it still sees the cross
    live.reset_state()                                                         # and restarts again
    assert live.poll_once()["fired"] == 0 and len(notes(client)) == 1


def test_52_week_high_and_low_live(client, monkeypatch):
    load("TCS", [100 + i * 0.1 for i in range(300)])                           # high of the window is about 131.9 (close + 1 spread)
    alert(client, preset="high_52w"); alert(client, preset="low_52w")
    feed(monkeypatch, TCS=140.0)
    live.poll_once()
    n = notes(client)
    assert [x["title"] for x in n] == ["TCS traded above its 52-week high"]
    assert n[0]["payload"]["level"] == pytest.approx(130.9, abs=0.2)


def test_watchlist_scope_uses_only_watchlist_symbols(client, monkeypatch):
    for s in ("TCS", "INFY", "RELIANCE"):
        load(s, [100.0] * 300)
    client.post("/api/watchlist/TCS"); client.post("/api/watchlist/INFY")
    client.post("/api/alerts", json={"kind": "breakout", "preset": "level", "params": {"level": 150, "trigger": "touch"}})
    f = feed(monkeypatch, TCS=160.0, INFY=120.0, RELIANCE=999.0)
    r = live.poll_once()
    assert sorted(f.calls) == ["INFY", "TCS"] and r["fired"] == 1


# --------------------------------------------------- when it must stay quiet
def test_closed_market_unconfigured_and_close_only_alerts_make_no_price_calls(client, monkeypatch):
    load("TCS", [100.0] * 300)
    f = feed(monkeypatch, TCS=160.0)
    assert live.poll_once()["status"] == "idle"                                # no live alerts yet
    alert(client, trigger="close")
    assert live.poll_once()["status"] == "idle"                                # a close-only alert is not polled
    alert(client)
    assert live.poll_once(datetime(2026, 10, 19, 16, 0, tzinfo=IST))["status"] == "closed"
    assert live.poll_once(datetime(2026, 10, 17, 11, 0, tzinfo=IST))["status"] == "closed"
    monkeypatch.setattr(live, "configured", lambda: False)
    assert live.poll_once()["status"] == "unconfigured"
    assert f.calls == []


def test_stale_or_missing_stored_prices_are_skipped_not_guessed(client, monkeypatch):
    load("TCS", [100.0] * 300, end=date(2026, 10, 1))                         # 18 days old
    alert(client); alert(client, symbol="INFY")                               # INFY has no bars at all
    f = feed(monkeypatch, TCS=160.0, INFY=160.0)
    r = live.poll_once()
    assert r["fired"] == 0 and f.calls == [] and any("days old" in s for s in r["skipped"]) and any("no stored prices" in s for s in r["skipped"])


def test_one_failing_symbol_does_not_stop_the_others(client, monkeypatch):
    for s in ("TCS", "INFY"):
        load(s, [100.0] * 300)
    alert(client, symbol="TCS"); alert(client, symbol="INFY")
    feed(monkeypatch, TCS=ConnectionError("boom"), INFY=160.0)
    r = live.poll_once()
    assert r["status"] == "ok" and r["fired"] == 1 and any("TCS" in e for e in r["errors"])


def test_total_failure_backs_off_instead_of_hammering_the_broker(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client)
    f = feed(monkeypatch, TCS=ValueError("Angel login failed: invalid totp"))
    assert live.poll_once()["status"] == "error"
    calls = len(f.calls)
    r = live.poll_once()
    assert r["status"] == "backoff" and len(f.calls) == calls                  # no new broker call
    live._backoff_until = 0                                                    # time passes
    f.prices["TCS"] = 160.0
    assert live.poll_once()["fired"] == 1 and live._backoff_for == 0


# ---------------------------------------------------- end-of-day relationship
def test_end_of_day_check_does_not_double_notify_a_live_alert(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client)
    feed(monkeypatch, TCS=160.0)
    assert live.poll_once()["fired"] == 1
    load("TCS", [100.0] * 299 + [120.0], end=date(2026, 10, 19), spread=45)   # today's bar arrives: high 165, close 120
    assert client.post("/api/alerts/check").json()["new"] == 0
    assert len(notes(client)) == 1


def test_touch_alert_still_fires_after_the_close_when_live_was_unavailable(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client, trigger="touch"); alert(client, trigger="close")
    monkeypatch.setattr(live, "configured", lambda: False)
    load("TCS", [100.0] * 299 + [120.0], end=date(2026, 10, 19), spread=45)   # day high 165 crossed 150, closed 120 below it
    assert client.post("/api/alerts/check").json()["new"] == 1                # only the touch alert
    n = notes(client)[0]
    assert n["title"] == "TCS traded above Rs 150.00" and "closed at Rs 120.00" in n["body"] and n["payload"]["mode"] == "touch"


def test_check_now_runs_one_live_reading_too(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client)
    feed(monkeypatch, TCS=160.0)
    r = client.post("/api/alerts/check").json()
    assert r["live"]["status"] == "ok" and r["new"] == 1 and r["unread"] == 1


# ------------------------------------------------------------------ status
def test_live_status_endpoint_explains_each_state(client, monkeypatch):
    s = client.get("/api/alerts/live-status").json()
    assert s["state"] == "on" and s["configured"] and s["market_open"] and "every 60 seconds" in s["text"]
    monkeypatch.setattr(live, "configured", lambda: False)
    assert client.get("/api/alerts/live-status").json()["state"] == "unconfigured"
    monkeypatch.setattr(live, "configured", lambda: True)
    monkeypatch.setattr(live, "now_ist", lambda: datetime(2026, 10, 19, 20, 0, tzinfo=IST))
    assert client.get("/api/alerts/live-status").json()["state"] == "closed"
    monkeypatch.setattr(settings, "alerts_live_interval_sec", 0)
    assert client.get("/api/alerts/live-status").json()["state"] == "off"
    p = client.get("/api/alerts/presets").json()
    assert "live" in p and any(x["key"] == "trigger" for x in p["breakout"][0]["params"])


def test_alert_status_text_shows_the_live_price(client, monkeypatch):
    load("TCS", [100.0] * 300)
    alert(client)
    feed(monkeypatch, TCS=120.0)
    live.poll_once()
    txt = client.get("/api/alerts").json()["items"][0]["status"]["items"][0]["text"]
    assert "Live price 120.00" in txt and "Level 150.00" in txt