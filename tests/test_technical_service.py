from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

IST = timezone(timedelta(hours=5, minutes=30))
COLS = ["symbol", "date", "series", "prev_close", "open", "high", "low", "close", "volume", "turnover_cr", "trades", "deliv_pct"]


def weekdays(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def seed(bars, symbol="MAXTEST", n=300, end=date(2026, 10, 7), skip=None):
    days = [d for d in weekdays(end, n) if not (skip and skip[0] <= d <= skip[1])]
    px = np.linspace(400, 600, len(days)) + np.sin(np.arange(len(days)) / 6) * 15
    rows = [(symbol, d.isoformat(), "EQ", p - 1, p, p + 2, p - 2, p, 1000.0, 5.0, 10, 50.0) for d, p in zip(days, px)]
    bars.upsert_bars(pd.DataFrame(rows, columns=COLS))
    bars.upsert_companies([{"symbol": symbol, "name": "Max Test Ltd"}])


@pytest.fixture()
def bars(tmp_path, monkeypatch):
    from backend.app.config.settings import settings
    from backend.app.repositories import bars_repository
    from backend.app.providers import angel_one
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "stock.db"))
    bars_repository._ready.clear()
    monkeypatch.setattr(angel_one, "configured", lambda: False)
    return bars_repository


def tech(*a, **k):
    from backend.app.services.data import service as data
    return data.technicals(*a, **k)


def test_uses_stored_bars_and_warms_up(bars):
    seed(bars)
    out = tech("maxtest", [{"type": "ema", "period": 10}], "3m")
    assert out["source"].startswith("NSE bhavcopy") and out["name"] == "Max Test Ltd"
    assert len(out["chart"]["dates"]) == 63 and out["chart"]["overlays"][0]["name"] == "EMA 10"
    assert all(v and v > 300 for v in out["chart"]["overlays"][0]["values"])
    assert out["as_of"] == "2026-10-07 (market close)" and "Max Test Ltd – Close vs EMA 10 (3 Months)" == out["title"]
    assert out["data_quality"]["warnings"] == [] and out["summary"]["candles_used_for_calculation"] == 163


def test_angel_is_preferred_and_failure_falls_back(bars, monkeypatch):
    from backend.app.providers import angel_one
    seed(bars)
    monkeypatch.setattr(angel_one, "configured", lambda: True)
    days = weekdays(date(2026, 10, 7), 300)
    angel_rows = [{"date": d.isoformat(), "open": 1.0, "high": 2.0, "low": 0.5, "close": 100 + i * 0.1, "volume": 5.0}
                  for i, d in enumerate(days)]
    monkeypatch.setattr(angel_one, "candles", lambda sym, bars=0: angel_rows)
    out = tech("MAXTEST", ["ema 20"], "3m")
    assert out["source"].startswith("Angel One") and out["summary"]["latest_close"] == round(100 + 299 * 0.1, 2)

    def boom(*a, **k):
        raise RuntimeError("Angel login failed: bad TOTP")
    monkeypatch.setattr(angel_one, "candles", boom)
    out = tech("MAXTEST", ["ema 20"], "3m")
    assert out["source"].startswith("NSE bhavcopy")
    assert any("Angel One" in a and "bad TOTP" in a for a in out["data_quality"]["source_attempts"])


def test_close_only_provider_fallback(bars, monkeypatch):
    from backend.app.services.data import service as data
    days = weekdays(date(2026, 10, 7), 260)
    chart = {"dates": [d.isoformat() for d in days], "close": list(np.linspace(50, 80, 260))}
    monkeypatch.setattr(data, "_call", lambda name, *a, **k: {"chart": chart})
    out = tech("NOBARS", ["atr", "ema 20"], "3m")
    assert out["source"].startswith("Price history provider")
    assert out["data_quality"]["has_ohlc"] is False and out["chart"]["panels"] == []
    assert any("ATR skipped" in w for w in out["data_quality"]["warnings"])


def test_no_data_anywhere_is_a_clear_error(bars, monkeypatch):
    from backend.app.services.data import service as data
    monkeypatch.setattr(data, "_call", lambda *a, **k: (_ for _ in ()).throw(ValueError("provider down")))
    with pytest.raises(ValueError, match="No price history for 'ZZZ'.*provider down"):
        tech("ZZZ")


def test_hole_in_stored_history_is_reported(bars):
    seed(bars, skip=(date(2026, 6, 9), date(2026, 8, 24)), n=400)
    out = tech("MAXTEST", ["ema 20", "ema 50"], "6m")
    assert out["data_quality"]["gaps"] and out["data_quality"]["gaps"][0]["calendar_days"] > 70
    assert any("hole" in w for w in out["data_quality"]["warnings"])


def test_api_endpoint_and_tool_wiring(bars):
    from backend.app.main import app
    from backend.app.services.orchestration import tools
    seed(bars)
    j = TestClient(app).get("/api/technicals/MAXTEST?indicators=ema10,rsi,macd&period=3m").json()
    assert set(j["indicators"]) >= {"EMA 10", "RSI 14", "MACD"} and len(j["chart"]["panels"]) == 2
    j2 = tools.run_tool("technicals", {"symbol": "MAXTEST", "indicators": [{"type": "sma", "period": 50}], "period": "1y"})
    assert j2["chart"]["overlays"][0]["name"] == "SMA 50" and len(j2["chart"]["dates"]) == 250
    spec = next(t for t in tools.TOOLS if t["function"]["name"] == "technicals")["function"]["parameters"]
    assert spec["required"] == ["symbol"] and "indicators" in spec["properties"]


# ---------------------------------------------------------------- gap healing
def test_pending_dates_finds_a_hole_in_the_middle():
    from backend.app.services.data.bhavcopy import pending_dates
    now = datetime(2026, 10, 8, 19, 0, tzinfo=IST)
    all_days = set(weekdays(date(2026, 10, 7), 120))
    hole = {d for d in all_days if date(2026, 7, 1) <= d <= date(2026, 7, 10)}
    stored = all_days - hole
    first, last = min(stored), max(stored)
    # old behaviour (no first_stored): only looks after the newest stored day, never sees the hole
    assert pending_dates(now, last, set(), 30, stored=stored) == [date(2026, 10, 8)]
    got = pending_dates(now, last, set(), 30, first_stored=first, stored=stored)
    assert set(got) == hole | {date(2026, 10, 8)} and got == sorted(got)
    # a confirmed holiday inside the hole is not retried
    done = {date(2026, 7, 3)}
    assert date(2026, 7, 3) not in pending_dates(now, last, done, 30, first_stored=first, stored=stored)


def test_refresh_fills_a_hole_automatically(bars):
    from backend.app.services.data import bhavcopy
    seed(bars, n=40, skip=(date(2026, 9, 15), date(2026, 9, 18)), end=date(2026, 10, 7))
    hole = [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)]
    header = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"
    files = {f"sec_bhavdata_full_{d:%d%m%Y}.csv": (header + f"\nMAXTEST, EQ, {d:%d-%b-%Y}, 500, 500, 505, 495, 502, 502, 502, 1000, 50, 10, 500, 40\n").encode() for d in hole}
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=files[r.url.path.rsplit("/", 1)[-1]]) if r.url.path.rsplit("/", 1)[-1] in files else httpx.Response(404)))
    r = bhavcopy.refresh(now=datetime(2026, 10, 7, 12, 0, tzinfo=IST), client=c, backfill_days=5, pause=0, with_master=False)
    assert sorted(x["date"] for x in r["loaded"]) == [d.isoformat() for d in hole] and r["status"] == "ok"
    assert bars.stored_dates() >= set(hole)


# ------------------------------------------------------- what the model sees
def test_model_view_is_compact_and_keeps_signals(bars):
    import json
    from backend.app.services.orchestration import tools
    seed(bars, n=700)
    full = tools.run_tool("technicals", {"symbol": "MAXTEST", "indicators": [{"type": "ema", "period": 20}, {"type": "ema", "period": 50}, {"type": "macd"}, {"type": "rsi"}, {"type": "bollinger"}], "period": "2y"})
    assert len(json.dumps(full)) > 24000                       # the UI payload is big (this is why a compact view is needed)
    slim = json.dumps(tools.llm_view("technicals", full))
    assert len(slim) < 9000 and '"signals"' in slim and '"levels"' in slim and '"summary"' in slim
    assert json.loads(slim)["chart"]["points"] == 500 and "overlays" not in slim


def test_history_view_summarises_and_flags_gaps(bars):
    from backend.app.services.orchestration import tools
    seed(bars, n=400, skip=(date(2026, 6, 9), date(2026, 8, 24)))
    out = tools.llm_view("history", {"chart": {"dates": [r["date"] for r in bars.symbol_bars("MAXTEST")], "close": [r["close"] for r in bars.symbol_bars("MAXTEST")]}, "source": "x"})
    cs = out["chart_summary"]
    assert "chart" not in out and cs["points"] > 300 and cs["data_gaps"][0]["calendar_days"] > 70
    assert tools.llm_view("quote", {"kv": {"a": 1}}) == {"kv": {"a": 1}}          # other tools untouched
