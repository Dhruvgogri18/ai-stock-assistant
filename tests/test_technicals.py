from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.app.services.data import technicals as t

IST = timezone(timedelta(hours=5, minutes=30))


def candles(closes, start="2025-01-01", spread=1.0):
    d0 = datetime.fromisoformat(start)
    rows, day = [], d0
    for c in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": day.date().isoformat(), "open": c, "high": c + spread, "low": c - spread, "close": c, "volume": 1000})
        day += timedelta(days=1)
    return pd.DataFrame(rows)


NOW = datetime(2030, 1, 1, 20, 0, tzinfo=IST)      # far future: no "forming candle" to drop


# ------------------------------------------------------------------ math
def test_ema_matches_hand_calculation():
    got = t.ema(pd.Series([1, 2, 3, 4, 5], dtype=float), 3).tolist()
    assert got == pytest.approx([1.0, 1.5, 2.25, 3.125, 4.0625])


def test_sma_and_bollinger_constant_series():
    s = pd.Series([10.0] * 30)
    assert t.sma(s, 5).iloc[-1] == 10 and np.isnan(t.sma(s, 5).iloc[3])
    u, m, l = t.bollinger(s, 20)
    assert u.iloc[-1] == m.iloc[-1] == l.iloc[-1] == 10


def test_rsi_direction_and_bounds():
    up, down, flat = pd.Series(range(1, 80), dtype=float), pd.Series(range(80, 1, -1), dtype=float), pd.Series([5.0] * 60)
    assert t.rsi(up).iloc[-1] == 100 and t.rsi(down).iloc[-1] == 0 and t.rsi(flat).iloc[-1] == 50
    r = t.rsi(pd.Series(np.sin(np.arange(200) / 5) * 10 + 100)).dropna()
    assert r.between(0, 100).all()
    assert t.rsi(up).iloc[:14].isna().all()                        # warm-up values are not reported


def test_macd_identity_and_flat():
    s = pd.Series(np.cumsum(np.random.default_rng(1).normal(size=200)) + 100)
    m, sg, h = t.macd(s)
    assert (h - (m - sg)).abs().max() < 1e-9
    m, sg, h = t.macd(pd.Series([50.0] * 100))
    assert m.abs().max() == 0 and h.abs().max() == 0


def test_atr_constant_range():
    n = 60
    h, l, c = pd.Series([102.0] * n), pd.Series([100.0] * n), pd.Series([101.0] * n)
    assert t.atr(h, l, c, 14).iloc[-1] == pytest.approx(2.0)


# ------------------------------------------------------------- build / trimming
def test_overlays_start_at_a_real_value_not_zero():
    """The Fuzz screenshot shows the EMA line starting at 0 on the left; ours is warmed up before trimming."""
    closes = list(np.linspace(400, 600, 300))
    out = t.build(candles(closes), [t.Spec("ema", 10)], 63, "3 Months", "X", now=NOW)
    vals = out["chart"]["overlays"][0]["values"]
    assert len(vals) == 63 == len(out["chart"]["dates"]) == len(out["chart"]["close"])
    assert all(v is not None and v > 400 for v in vals)
    assert abs(vals[0] - out["chart"]["close"][0]) < 10            # EMA10 hugs a steady trend from the first point


def test_indicator_values_use_history_before_the_window():
    closes = [100.0] * 250 + [200.0] * 5                           # jump in the last 5 bars
    full = t.ema(pd.Series(closes), 50).iloc[-1]
    out = t.build(candles(closes), [t.Spec("ema", 50)], 20, "1 Month", "X", now=NOW)
    assert out["indicators"]["EMA 50"] == pytest.approx(full, abs=0.01)


def test_crossover_date_and_sessions_ago():
    closes = list(np.linspace(200, 100, 120)) + list(np.linspace(100, 220, 120))
    df = candles(closes)
    out = t.build(df, [t.Spec("ema", 10), t.Spec("ema", 30)], 100, "5 Months", "X", now=NOW)
    sig = next(s for s in out["signals"] if s["name"] == "EMA 10 vs EMA 30")
    assert sig["last_cross"] == "bullish crossover" and sig["state"] == "EMA 10 above EMA 30"
    c = pd.Series(closes)
    diff = (t.ema(c, 10) - t.ema(c, 30)).to_numpy()
    flip = max(i for i in range(1, len(diff)) if np.sign(diff[i]) != np.sign(diff[i - 1]))
    assert sig["since"] == df["date"].iloc[flip] and sig["sessions"] == len(df) - 1 - flip


def test_no_crossover_is_reported_honestly():
    out = t.build(candles(list(np.linspace(100, 300, 300))), [t.Spec("ema", 10), t.Spec("ema", 30)], 60, "3 Months", "X", now=NOW)
    sig = next(s for s in out["signals"] if "vs EMA 30" in s["name"] and s["name"].startswith("EMA 10"))
    assert sig["last_cross"] == "no crossover in the data" and sig["since"] is None


def test_price_vs_ma_signal_text():
    closes = [100.0] * 100 + [90.0] * 5
    out = t.build(candles(closes), [t.Spec("ema", 20)], 30, "1 Month", "X", now=NOW)
    s = next(s for s in out["signals"] if s["name"] == "Price vs EMA 20")
    assert s["state"] == "below" and s["distance_pct"] < 0 and "since" in s["detail"]
    assert s["sessions"] == 4                                    # first sub-EMA close 4 sessions before the last


def test_rsi_macd_bollinger_atr_panels_and_signals():
    rng = np.random.default_rng(3)
    closes = list(np.cumsum(rng.normal(0.3, 2, 260)) + 300)
    specs = t.parse_specs(["rsi", "macd", "bollinger", "atr", "ema 20"])
    out = t.build(candles(closes), specs, 126, "6 Months", "X", now=NOW)
    names = [p["name"] for p in out["chart"]["panels"]]
    assert names == ["RSI 14", "MACD (12,26,9)", "ATR 14"]
    assert [o["name"] for o in out["chart"]["overlays"]] == ["BB Upper", "BB Middle", "BB Lower", "EMA 20"] or \
           {o["name"] for o in out["chart"]["overlays"]} == {"BB Upper", "BB Middle", "BB Lower", "EMA 20"}
    assert 0 <= out["indicators"]["RSI 14"] <= 100
    assert {"RSI 14", "MACD (12,26,9)", "Bollinger (20, 2)", "ATR 14"} <= {s["name"] for s in out["signals"]}
    rsi_sig = next(s for s in out["signals"] if s["name"] == "RSI 14")
    assert rsi_sig["state"].split(" ")[0] in ("overbought", "oversold", "neutral")


def test_atr_skipped_without_high_low():
    df = candles(list(np.linspace(100, 120, 200)))
    df["high"], df["low"] = np.nan, np.nan
    out = t.build(df, [t.Spec("atr", 14), t.Spec("ema", 20)], 60, "3 Months", "X", now=NOW)
    assert out["data_quality"]["has_ohlc"] is False and any("ATR skipped" in w for w in out["data_quality"]["warnings"])
    assert [p["name"] for p in out["chart"]["panels"]] == []


# ----------------------------------------------------------- data hygiene
def test_forming_candle_is_dropped_during_market_hours_only():
    df = candles([100, 101, 102, 103, 104, 105], start="2026-10-01")
    today = df["date"].iloc[-1]
    midday = datetime.fromisoformat(today).replace(hour=11, minute=0, tzinfo=IST)
    evening = midday.replace(hour=16, minute=30)
    assert len(t.clean_candles(df, midday)) == 5 and t.clean_candles(df, midday)["date"].iloc[-1] != today
    assert len(t.clean_candles(df, evening)) == 6


def test_dedupes_sorts_and_handles_text_numbers():
    df = pd.DataFrame({"date": ["2026-01-03", "2026-01-02", "2026-01-02T00:00:00+0530"], "close": ["10", "9", "9.5"]})
    out = t.clean_candles(df, NOW)
    assert out["date"].tolist() == ["2026-01-02", "2026-01-03"] and out["close"].tolist() == [9.5, 10.0]


def test_gap_detection_warns():
    a = candles(list(np.linspace(100, 110, 120)), start="2026-01-01")
    b = candles(list(np.linspace(110, 130, 60)), start="2026-08-25")
    out = t.build(pd.concat([a, b]), [t.Spec("ema", 20)], 150, "6 Months", "X", now=NOW)
    gaps = out["data_quality"]["gaps"]
    assert len(gaps) == 1 and gaps[0]["calendar_days"] > 60
    assert any("hole" in w for w in out["data_quality"]["warnings"])


def test_short_history_warns_but_still_returns():
    out = t.build(candles(list(np.linspace(100, 120, 40))), [t.Spec("ema", 50)], 30, "1 Month", "X", now=NOW)
    assert any("approximate" in w for w in out["data_quality"]["warnings"])
    with pytest.raises(ValueError, match="more history"):
        t.build(candles([1, 2, 3]), [t.Spec("ema", 5)], 30, "1 Month", "X", now=NOW)


# ------------------------------------------------------------------ levels
def test_support_resistance_and_pivots():
    wave = []
    for i in range(10):                       # oscillate between ~100 and ~120
        wave += list(np.linspace(100, 120, 10)) + list(np.linspace(120, 100, 10))
    closes = wave + list(np.linspace(100, 110, 10))
    out = t.build(candles(closes, spread=0.5), [t.Spec("ema", 20)], 60, "3 Months", "X", now=NOW)
    lv = out["levels"]
    assert lv["resistance"] and lv["support"]
    assert all(r["level"] > 110 for r in lv["resistance"]) and all(s["level"] < 110 for s in lv["support"])
    assert abs(lv["resistance"][0]["level"] - 120.5) < 2 and abs(lv["support"][0]["level"] - 99.5) < 2
    p = lv["pivot_points"]
    assert p["s1"] < p["pivot"] < p["r1"] and lv["20_day_high"] >= lv["20_day_low"]


# ----------------------------------------------------------------- parsing
def test_parse_specs_variants_and_defaults():
    assert t.parse_specs(None) == [t.Spec("ema", 20), t.Spec("ema", 50)]
    assert t.parse_specs([{"type": "EMA", "period": 10}]) == [t.Spec("ema", 10)]
    assert t.parse_specs(["10 EMA", "50-day SMA", "RSI", "MACD", "bollinger bands", "ema10"]) == [
        t.Spec("ema", 10), t.Spec("sma", 50), t.Spec("rsi", 14), t.Spec("macd"), t.Spec("bollinger", 20)]
    assert t.parse_specs("20 ema") == [t.Spec("ema", 20)]
    assert t.parse_specs([{"type": "nonsense"}]) == [t.Spec("ema", 20), t.Spec("ema", 50)]
    assert len(t.parse_specs([f"ema {n}" for n in range(5, 60, 5)])) == t.MAX_INDICATORS
    assert t.parse_specs([{"type": "ema", "period": 9999}])[0].period == 400


def test_parse_period_variants():
    assert t.parse_period("3m") == ("3 Months", 63) and t.parse_period("3 months") == ("3 Months", 63)
    assert t.parse_period("1y") == ("1 Year", 250) and t.parse_period("2yr")[1] == 500
    assert t.parse_period("90d") == ("90 Days", 90) and t.parse_period(None) == ("6 Months", 126)
    assert t.warmup_bars([t.Spec("ema", 10)]) == 100 and t.warmup_bars([t.Spec("sma", 200)]) == 600
