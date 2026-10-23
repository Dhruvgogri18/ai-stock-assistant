"""Technical indicators, computed in code. The LLM never calculates them; it only explains what this returns.

    parse_specs()      "EMA 20", {"type": "rsi"} ... -> list[Spec]
    parse_period()     "3 months" -> (label, trading days to show)
    build()            candles + specs -> chart payload, latest values, signals, support/resistance

Definitions (stated here so the answer can quote them exactly):
    SMA(n)    simple mean of the last n closes
    EMA(n)    exponential average, alpha = 2/(n+1), seeded with the first close (pandas ewm adjust=False)
    RSI(n)    Wilder's RSI, default 14
    MACD      EMA12 - EMA26, signal = EMA9 of MACD, histogram = MACD - signal
    Bollinger SMA20 +/- 2 standard deviations (population std)
    ATR(n)    Wilder's average true range, default 14 (needs high/low/close)

Warm-up: indicators are computed on MORE history than is displayed (at least 3x the longest period, minimum 100
bars) and only then trimmed to the requested window, so the lines start at a sensible value instead of at zero.
Only COMPLETED daily candles are used: today's still-forming candle is dropped while the market is open.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
KINDS = ["ema", "sma", "rsi", "macd", "bollinger", "atr"]
DEFAULT_PERIOD = {"ema": 20, "sma": 50, "rsi": 14, "atr": 14, "bollinger": 20}
MAX_INDICATORS = 6
MIN_WARMUP = 100
MARKET_DONE = (15, 45)          # a daily candle dated today is complete after 15:45 IST
GAP_DAYS = 7                    # more than this many calendar days between candles = a hole, not a holiday


@dataclass(frozen=True)
class Spec:
    kind: str
    period: int | None = None

    @property
    def label(self) -> str:
        if self.kind == "macd":
            return "MACD (12,26,9)"
        if self.kind == "bollinger":
            return f"Bollinger ({self.period}, 2)"
        return f"{self.kind.upper()} {self.period}"

    @property
    def is_ma(self) -> bool:
        return self.kind in ("ema", "sma")


# --------------------------------------------------------------------- parsing
_ALIASES = {"bb": "bollinger", "bbands": "bollinger", "bollinger bands": "bollinger", "boll": "bollinger",
            "moving average": "sma", "ma": "sma", "exponential moving average": "ema"}


def _one_spec(raw) -> Spec | None:
    kind, period = None, None
    if isinstance(raw, dict):
        kind = raw.get("type") or raw.get("indicator") or raw.get("name") or raw.get("kind")
        period = raw.get("period") or raw.get("length") or raw.get("window")
    elif isinstance(raw, str):
        s = raw.strip().lower()
        m = re.search(r"(\d+)", s)
        period = int(m.group(1)) if m else None
        kind = re.sub(r"[\d\-_]+|\bday\b|\bdays\b|\bperiod\b|\bd\b", " ", s).strip()
    if not kind:
        return None
    kind = _ALIASES.get(str(kind).strip().lower(), str(kind).strip().lower())
    kind = {"exponential": "ema", "simple": "sma"}.get(kind, kind)
    if kind.startswith("ema"):
        kind = "ema"
    elif kind.startswith("sma"):
        kind = "sma"
    elif kind.startswith("bollinger"):
        kind = "bollinger"
    if kind not in KINDS:
        return None
    if kind == "macd":
        return Spec("macd", None)
    try:
        period = int(period) if period is not None else DEFAULT_PERIOD[kind]
    except (TypeError, ValueError):
        period = DEFAULT_PERIOD[kind]
    return Spec(kind, max(2, min(period, 400)))


def parse_specs(raw) -> list[Spec]:
    """Tolerant: accepts [{"type":"ema","period":20}], ["EMA 20","rsi"], or a single string. Empty -> EMA 20 + EMA 50."""
    if raw is None or raw == []:
        raw = []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    specs: list[Spec] = []
    for r in raw:
        s = _one_spec(r)
        if s and s not in specs:
            specs.append(s)
    if not specs:
        specs = [Spec("ema", 20), Spec("ema", 50)]
    return specs[:MAX_INDICATORS]


def parse_period(raw) -> tuple[str, int]:
    """'3m', '3 months', '1y', '6mo', '90d' -> (label, trading bars to display)."""
    s = str(raw or "6m").strip().lower()
    m = re.match(r"^(\d+)\s*([a-z]*)", s)
    if not m:
        return "6 Months", 126
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith("y"):
        return (f"{n} Year" + ("s" if n > 1 else ""), min(n * 250, 750))
    if unit.startswith("w"):
        return (f"{n} Weeks", max(5, n * 5))
    if unit.startswith("d"):
        return (f"{n} Days", max(10, min(n, 750)))
    return (f"{n} Month" + ("s" if n > 1 else ""), min(max(n, 1) * 21, 750))


def warmup_bars(specs: list[Spec]) -> int:
    longest = max([s.period or 26 for s in specs] + [26])
    return max(MIN_WARMUP, 3 * longest)


# ------------------------------------------------------------------ primitives
def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain, loss = delta.clip(lower=0), (-delta).clip(lower=0)
    ag, al = gain.ewm(alpha=1 / n, adjust=False).mean(), loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(al == 0) & (ag > 0)] = 100.0
    out[(al == 0) & (ag == 0)] = 50.0
    out.iloc[:n] = np.nan                      # first n values are not meaningful
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(close, fast) - ema(close, slow)
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    out = tr.ewm(alpha=1 / n, adjust=False).mean()
    out.iloc[:n] = np.nan
    return out


# ---------------------------------------------------------------------- hygiene
def clean_candles(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Sort, de-duplicate, coerce numbers and drop today's candle while it is still forming."""
    df = df.copy()
    df["date"] = df["date"].astype(str).str[:10]
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    df = df.dropna(subset=["close"]).sort_values("date").drop_duplicates("date", keep="last")
    now = now or datetime.now(IST)
    if len(df) and df["date"].iloc[-1] == now.date().isoformat() and (now.hour, now.minute) < MARKET_DONE:
        df = df.iloc[:-1]
    return df.reset_index(drop=True)


def find_gaps(dates: list[str], max_days: int = GAP_DAYS) -> list[dict]:
    out = []
    for a, b in zip(dates, dates[1:]):
        d = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
        if d > max_days:
            out.append({"from": a, "to": b, "calendar_days": d})
    return out


# ------------------------------------------------------------------------ signals
def _r(v, nd: int = 2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _side_run(diff: pd.Series, skip: int = 0) -> tuple[int, int | None]:
    """(current sign, index of the bar where that sign began).

    `skip` ignores the first bars (indicator warm-up: two EMAs share the same seed, so they look 'crossed'
    there). The index is None when the sign never changed after the first valid bar (no crossover in the data).
    Exact zeros carry the previous sign forward."""
    arr = np.sign(diff.to_numpy(dtype=float))
    arr[:skip] = np.nan
    prev, flip, seen = 0, None, 0
    for i in np.where(~np.isnan(arr))[0]:
        cur = int(arr[i]) or prev
        if seen and cur != prev:
            flip = int(i)
        prev, seen = cur, seen + 1
    return prev, flip


def _ago(df: pd.DataFrame, i: int | None) -> tuple[str | None, int | None]:
    if i is None:
        return None, None
    return df["date"].iloc[i], len(df) - 1 - i


def _signals(df: pd.DataFrame, series: dict[Spec, dict[str, pd.Series]], specs: list[Spec]) -> list[dict]:
    close, last = df["close"], float(df["close"].iloc[-1])
    out: list[dict] = []

    for sp in specs:
        if sp.is_ma:
            ma = series[sp]["line"]
            if pd.isna(ma.iloc[-1]):
                continue
            v = float(ma.iloc[-1])
            sign, start = _side_run(close - ma, sp.period)
            when, ago = _ago(df, start)
            pct = (last / v - 1) * 100
            side = "above" if sign > 0 else "below"
            detail = f"Close {last:,.2f} is {abs(pct):.1f}% {side} {sp.label} ({v:,.2f})"
            if start is not None:
                detail += f"; it has closed {side} it since {when} ({ago} sessions)" if ago else f"; it first closed {side} it on {when}"
            out.append({"name": f"Price vs {sp.label}", "state": side, "value": _r(v), "distance_pct": _r(pct),
                        "since": when, "sessions": ago, "detail": detail})

    mas = sorted([s for s in specs if s.is_ma], key=lambda s: (s.kind, s.period))
    for i, fast in enumerate(mas):
        for slow in mas[i + 1:]:
            if fast.kind != slow.kind or fast.period == slow.period:
                continue
            diff = series[fast]["line"] - series[slow]["line"]
            if pd.isna(diff.iloc[-1]):
                continue
            sign, start = _side_run(diff, slow.period)
            when, ago = _ago(df, start)
            now_state = f"{fast.label} above {slow.label}" if sign > 0 else f"{fast.label} below {slow.label}"
            if start is not None:
                kind = "bullish crossover" if sign > 0 else "bearish crossover"
                detail = f"{now_state}. Last crossover ({kind}) on {when}, {ago} sessions ago."
            else:
                kind = "no crossover in the data"
                detail = f"{now_state}. No crossover occurs within the loaded history."
            out.append({"name": f"{fast.label} vs {slow.label}", "state": now_state, "last_cross": kind,
                        "since": when, "sessions": ago, "detail": detail})

    for sp in specs:
        if sp.kind == "rsi":
            v = float(series[sp]["line"].iloc[-1])
            if math.isnan(v):
                continue
            zone = "overbought (above 70)" if v >= 70 else "oversold (below 30)" if v <= 30 else "neutral (30 to 70)"
            out.append({"name": sp.label, "state": zone, "value": _r(v), "detail": f"{sp.label} is {v:.1f}: {zone}."})
        elif sp.kind == "macd":
            m, sg, h = (series[sp][k] for k in ("macd", "signal", "hist"))
            if pd.isna(h.iloc[-1]):
                continue
            sign, start = _side_run(h, 35)
            when, ago = _ago(df, start)
            rel = "above" if sign > 0 else "below"
            zero = "above" if m.iloc[-1] > 0 else "below"
            detail = f"MACD {m.iloc[-1]:,.2f} is {rel} its signal line {sg.iloc[-1]:,.2f} (histogram {h.iloc[-1]:+.2f}) and {zero} zero."
            if start is not None:
                detail += f" Last signal-line crossover on {when} ({ago} sessions ago)."
            out.append({"name": sp.label, "state": f"MACD {rel} signal", "macd": _r(m.iloc[-1]), "signal": _r(sg.iloc[-1]),
                        "histogram": _r(h.iloc[-1]), "since": when, "sessions": ago, "detail": detail})
        elif sp.kind == "bollinger":
            up, mid, lo = (series[sp][k] for k in ("upper", "mid", "lower"))
            if pd.isna(up.iloc[-1]):
                continue
            width = up.iloc[-1] - lo.iloc[-1]
            pb = (last - lo.iloc[-1]) / width if width else float("nan")
            state = ("above the upper band" if last > up.iloc[-1] else "below the lower band" if last < lo.iloc[-1]
                     else "in the upper part of the bands" if pb > 0.8 else "in the lower part of the bands" if pb < 0.2
                     else "in the middle of the bands")
            out.append({"name": sp.label, "state": state, "upper": _r(up.iloc[-1]), "middle": _r(mid.iloc[-1]),
                        "lower": _r(lo.iloc[-1]), "percent_b": _r(pb, 2), "bandwidth_pct": _r(width / mid.iloc[-1] * 100),
                        "detail": f"Close {last:,.2f} is {state} (upper {up.iloc[-1]:,.2f}, middle {mid.iloc[-1]:,.2f}, lower {lo.iloc[-1]:,.2f})."})
        elif sp.kind == "atr":
            v = float(series[sp]["line"].iloc[-1])
            if math.isnan(v):
                continue
            out.append({"name": sp.label, "state": "volatility", "value": _r(v), "percent_of_price": _r(v / last * 100),
                        "detail": f"{sp.label} is {v:,.2f}, about {v / last * 100:.1f}% of the close: a typical daily range."})
    return out


# -------------------------------------------------------------------- levels
def _swings(df: pd.DataFrame, window: int = 5, lookback: int = 150) -> tuple[list[float], list[float]]:
    d = df.tail(lookback).reset_index(drop=True)
    hi = d["high"].where(d["high"].notna(), d["close"])
    lo = d["low"].where(d["low"].notna(), d["close"])
    highs, lows = [], []
    for i in range(window, len(d) - window):
        if hi.iloc[i] == hi.iloc[i - window:i + window + 1].max():
            highs.append(float(hi.iloc[i]))
        if lo.iloc[i] == lo.iloc[i - window:i + window + 1].min():
            lows.append(float(lo.iloc[i]))
    return highs, lows


def _cluster(levels: list[float], tol: float = 0.01) -> list[dict]:
    out: list[list[float]] = []
    for v in sorted(levels):
        if out and abs(v / (sum(out[-1]) / len(out[-1])) - 1) <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [{"level": _r(sum(g) / len(g)), "touches": len(g)} for g in out]


def _levels(df: pd.DataFrame, view: pd.DataFrame) -> dict:
    last = float(df["close"].iloc[-1])
    highs, lows = _swings(df)
    res = [c for c in _cluster(highs) if c["level"] > last][:3]
    sup = [c for c in reversed(_cluster(lows)) if c["level"] < last][:3]
    row = df.iloc[-1]
    h = row["high"] if pd.notna(row["high"]) else row["close"]
    l = row["low"] if pd.notna(row["low"]) else row["close"]
    p = (h + l + row["close"]) / 3
    piv = {"pivot": _r(p), "r1": _r(2 * p - l), "s1": _r(2 * p - h), "r2": _r(p + (h - l)), "s2": _r(p - (h - l)),
           "based_on": row["date"]}
    out = {"resistance": res, "support": sup, "pivot_points": piv,
           "note": "Resistance/support are swing highs/lows from the last ~150 sessions (merged within 1%); "
                   "pivot points use the last completed session. These are reference levels, not predictions."}
    for n in (20, 50):
        w = df.tail(n)
        if len(w) == n:
            hh = w["high"].where(w["high"].notna(), w["close"]).max()
            ll = w["low"].where(w["low"].notna(), w["close"]).min()
            out[f"{n}_day_high"], out[f"{n}_day_low"] = _r(hh), _r(ll)
    return out


# ------------------------------------------------------------------------ build
def build(df: pd.DataFrame, specs: list[Spec], display_bars: int, period_label: str, name: str,
          now: datetime | None = None) -> dict:
    """df: candles with date/open/high/low/close/volume (any order). Returns the tool payload (without source/as_of)."""
    df = clean_candles(df, now)
    if len(df) < 5:
        raise ValueError(f"Only {len(df)} daily candles available; need more history to calculate indicators.")
    close = df["close"]
    has_ohlc = df[["high", "low"]].notna().all(axis=1).mean() > 0.9
    warnings: list[str] = []

    series: dict[Spec, dict[str, pd.Series]] = {}
    for sp in specs:
        if sp.kind == "ema":
            series[sp] = {"line": ema(close, sp.period)}
        elif sp.kind == "sma":
            series[sp] = {"line": sma(close, sp.period)}
        elif sp.kind == "rsi":
            series[sp] = {"line": rsi(close, sp.period)}
        elif sp.kind == "macd":
            m, s, h = macd(close)
            series[sp] = {"macd": m, "signal": s, "hist": h}
        elif sp.kind == "bollinger":
            u, mi, lo = bollinger(close, sp.period)
            series[sp] = {"upper": u, "mid": mi, "lower": lo}
        elif sp.kind == "atr":
            if not has_ohlc:
                warnings.append("ATR skipped: this data source has no high/low prices.")
                continue
            series[sp] = {"line": atr(df["high"], df["low"], close, sp.period)}
    specs = [s for s in specs if s in series]

    n = len(df)
    start = max(0, n - display_bars)
    v = df.iloc[start:].reset_index(drop=True)
    idx = slice(start, n)

    def tr(s: pd.Series) -> list:
        return [_r(x) for x in s.iloc[idx]]

    overlays, panels, latest = [], [], {}
    for sp in specs:
        s = series[sp]
        if sp.is_ma:
            overlays.append({"name": sp.label, "kind": sp.kind, "period": sp.period, "values": tr(s["line"])})
            latest[sp.label] = _r(s["line"].iloc[-1])
        elif sp.kind == "bollinger":
            for key, nm, dash in (("upper", "Upper", True), ("mid", "Middle", False), ("lower", "Lower", True)):
                overlays.append({"name": f"BB {nm}", "kind": "band", "dash": dash, "values": tr(s[key])})
            latest.update({"Bollinger upper": _r(s["upper"].iloc[-1]), "Bollinger middle": _r(s["mid"].iloc[-1]),
                           "Bollinger lower": _r(s["lower"].iloc[-1])})
        elif sp.kind == "rsi":
            panels.append({"name": sp.label, "type": "line", "values": tr(s["line"]), "bands": [30, 70], "domain": [0, 100]})
            latest[sp.label] = _r(s["line"].iloc[-1])
        elif sp.kind == "atr":
            panels.append({"name": sp.label, "type": "line", "values": tr(s["line"])})
            latest[sp.label] = _r(s["line"].iloc[-1])
        elif sp.kind == "macd":
            panels.append({"name": sp.label, "type": "macd", "macd": tr(s["macd"]), "signal": tr(s["signal"]),
                           "hist": tr(s["hist"])})
            latest.update({"MACD": _r(s["macd"].iloc[-1]), "MACD signal": _r(s["signal"].iloc[-1]),
                           "MACD histogram": _r(s["hist"].iloc[-1])})

    # adequacy of history for each indicator
    for sp in specs:
        need = 3 * (sp.period or 26)
        if n < need:
            warnings.append(f"{sp.label}: only {n} candles loaded (3x the period = {need} recommended), so early "
                            f"values are approximate.")
    gaps = [g for g in find_gaps(df["date"].tolist()) if g["to"] >= v["date"].iloc[0]]
    if gaps:
        warnings.append(f"The price history has {len(gaps)} hole(s) inside the data (e.g. {gaps[0]['from']} to "
                        f"{gaps[0]['to']}). Moving averages across a hole are unreliable.")
    if n - start < display_bars:
        warnings.append(f"Only {n - start} candles available for the requested {period_label.lower()}.")

    hi_i, lo_i = int(v["close"].idxmax()), int(v["close"].idxmin())
    summary = {
        "latest_close": _r(v["close"].iloc[-1]), "latest_date": v["date"].iloc[-1],
        "window_start": v["date"].iloc[0], "window_high_close": _r(v["close"].iloc[hi_i]), "window_high_date": v["date"].iloc[hi_i],
        "window_low_close": _r(v["close"].iloc[lo_i]), "window_low_date": v["date"].iloc[lo_i],
        "window_return_pct": _r((v["close"].iloc[-1] / v["close"].iloc[0] - 1) * 100),
        "candles_used_for_calculation": n, "candles_displayed": len(v),
    }
    vol = df["volume"]
    if vol.notna().sum() >= 21 and vol.iloc[-21:-1].mean() > 0:
        summary["volume_vs_20d_avg"] = _r(vol.iloc[-1] / vol.iloc[-21:-1].mean())

    title = f"{name} – Close" + (" vs " + ", ".join(s.label for s in specs if s.is_ma) if any(s.is_ma for s in specs) else "")
    extra = [s.label for s in specs if not s.is_ma]
    if extra:
        title += (" + " if any(s.is_ma for s in specs) else " with ") + ", ".join(extra)
    title += f" ({period_label})"

    return {
        "chart": {"dates": v["date"].tolist(), "close": [_r(x) for x in v["close"]], "overlays": overlays, "panels": panels},
        "title": title, "indicators": latest, "signals": _signals(df, series, specs), "levels": _levels(df, v),
        "summary": summary, "definitions": {
            "EMA": "alpha = 2/(n+1), seeded with the first close", "RSI": "Wilder smoothing (alpha = 1/n)",
            "MACD": "EMA12 - EMA26; signal = EMA9 of MACD", "Bollinger": "SMA20 +/- 2 population std dev",
            "ATR": "Wilder average of true range"},
        "data_quality": {"candles": n, "has_ohlc": bool(has_ohlc), "gaps": gaps[:5], "warnings": warnings},
    }
