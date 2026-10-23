"""Pure rule logic: given stored daily bars (oldest first) decide whether a breakout rule fired on a given bar.

No I/O here. All prices are end-of-day and NOT adjusted for splits or bonuses, so every check reports a caveat when
the lookback window contains a suspiciously large one-day move.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from backend.app.services.data import technicals

LOOKBACK = 250            # sessions in a "52-week" window
MIN_PRIOR = 120           # fewest prior sessions we accept before saying "52-week"
JUMP_CAVEAT = 0.35        # a one-day move beyond this in the window may be a split/bonus, not a real move

TRIGGER = {"key": "trigger", "label": "Trigger when", "type": "choice", "default": "close", "choices": ["close", "touch"],
           "labels": {"close": "It closes beyond it (end of day)", "touch": "Price touches it during the day (live)"}}

PRESETS = {
    "high_52w": {"label": "New 52-week high",
                 "description": "Fires when a stock goes above its highest price of the previous 250 sessions.", "params": [TRIGGER]},
    "low_52w": {"label": "New 52-week low",
                "description": "Fires when a stock goes below its lowest price of the previous 250 sessions.", "params": [TRIGGER]},
    "ema_cross": {"label": "Moving-average crossover",
                  "description": "Fires on the day a faster exponential moving average crosses a slower one.",
                  "params": [{"key": "fast", "label": "Fast EMA (days)", "type": "int", "default": 20, "min": 2, "max": 100},
                             {"key": "slow", "label": "Slow EMA (days)", "type": "int", "default": 50, "min": 5, "max": 250},
                             {"key": "direction", "label": "Direction", "type": "choice", "default": "up", "choices": ["up", "down"]}]},
    "level": {"label": "Price crosses a level",
              "description": "Fires when the price crosses a level you choose.",
              "params": [{"key": "level", "label": "Price level (Rs)", "type": "float", "default": None, "min": 0.01},
                         {"key": "direction", "label": "Direction", "type": "choice", "default": "above", "choices": ["above", "below"]},
                         TRIGGER]},
}


@dataclass
class Check:
    ready: bool                       # False = not enough stored history to evaluate this rule honestly
    triggered: bool = False
    title: str = ""
    body: str = ""
    facts: dict = field(default_factory=dict)
    note: str = ""                    # why it is not ready, or a caveat


def normalize_params(preset: str, params: dict | None) -> dict:
    """Validate and fill defaults. Raises ValueError with a message fit to show the user."""
    if preset not in PRESETS:
        raise ValueError(f"Unknown breakout rule '{preset}'.")
    params, out = params or {}, {}
    for p in PRESETS[preset]["params"]:
        v = params.get(p["key"], p["default"])
        if p["type"] == "choice":
            if v not in p["choices"]:
                raise ValueError(f"{p['label']} must be one of: {', '.join(p['choices'])}.")
        else:
            if v in (None, "") or isinstance(v, bool):
                raise ValueError(f"{p['label']} is required.")
            try:
                v = int(v) if p["type"] == "int" else float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{p['label']} must be a number.") from None
            if v < p.get("min", float("-inf")) or v > p.get("max", float("inf")):
                raise ValueError(f"{p['label']} is out of range.")
        out[p["key"]] = v
    if preset == "ema_cross" and out["fast"] >= out["slow"]:
        raise ValueError("The fast EMA must be shorter than the slow EMA.")
    return out


def describe(kind: str, preset: str, params: dict) -> str:
    if kind == "result":
        return "Results announced"
    touch = params.get("trigger") == "touch"
    if preset == "high_52w":
        return "Price trades above 52-week high (live)" if touch else "Close above 52-week high"
    if preset == "low_52w":
        return "Price trades below 52-week low (live)" if touch else "Close below 52-week low"
    if preset == "ema_cross":
        return f"{params['fast']}-day EMA crosses {'above' if params['direction'] == 'up' else 'below'} {params['slow']}-day EMA"
    if preset == "level":
        return (f"Price trades {params['direction']} Rs {params['level']:,.2f} (live)" if touch
                else f"Close crosses {params['direction']} Rs {params['level']:,.2f}")
    return preset


def _inr(v: float) -> str:
    return f"Rs {v:,.2f}"


def _caveat(bars: list[dict], i: int) -> str:
    window = bars[max(0, i - LOOKBACK): i + 1]
    for a, b in zip(window, window[1:]):
        if a["close"] and abs(b["close"] / a["close"] - 1) > JUMP_CAVEAT:
            return (f"A one-day move above {int(JUMP_CAVEAT * 100)}% appears in this window ({b['date']}). Prices are not "
                    "adjusted for splits or bonuses, so check whether it was a corporate action.")
    return ""


def evaluate(preset: str, params: dict, bars: list[dict], i: int, symbol: str) -> Check:
    """Did `preset` fire on bars[i]? bars are oldest first and may extend past i (they are ignored)."""
    if i < 1 or i >= len(bars):
        return Check(False, note="Not enough price history stored.")
    bar, date = bars[i], bars[i]["date"]
    close = bar["close"]
    cav = _caveat(bars, i)
    touch = params.get("trigger") == "touch"      # "traded beyond it at any point of the session", judged from the day's high/low

    if preset in ("high_52w", "low_52w"):
        prior = bars[max(0, i - LOOKBACK): i]
        if len(prior) < MIN_PRIOR:
            return Check(False, note=f"Only {len(prior)} earlier sessions are stored; a 52-week comparison needs at least {MIN_PRIOR}.")
        if preset == "high_52w":
            ref = max(prior, key=lambda b: b.get("high") or b["close"])
            level = ref.get("high") or ref["close"]
            extreme = bar.get("high") or close
            hit, word, rel = (extreme if touch else close) > level, "above", "high"
        else:
            ref = min(prior, key=lambda b: b.get("low") or b["close"])
            level = ref.get("low") or ref["close"]
            extreme = bar.get("low") or close
            hit, word, rel = (extreme if touch else close) < level, "below", "low"
        pct = (close / level - 1) * 100
        facts = {"date": date, "close": close, "level": level, "level_date": ref["date"], "pct_vs_level": round(pct, 2),
                 "mode": "touch" if touch else "close"}
        if touch:
            return Check(True, hit, f"{symbol} traded {word} its 52-week {rel}" if hit else "",
                         f"Day {rel} Rs {extreme:,.2f} on {date} went {word} the previous 52-week {rel} of {_inr(level)} set on "
                         f"{ref['date']}; it closed at {_inr(close)}. End-of-day NSE data." + (f" {cav}" if cav else ""), facts, cav)
        return Check(True, hit, f"{symbol} closed {word} its 52-week {rel}" if hit else "",
                     f"Close {_inr(close)} on {date} vs the previous 52-week {rel} of {_inr(level)} set on {ref['date']} "
                     f"({pct:+.2f}%). End-of-day NSE data." + (f" {cav}" if cav else ""), facts, cav)

    if preset == "ema_cross":
        fast, slow, direction = params["fast"], params["slow"], params["direction"]
        need = slow * 3
        if i + 1 < need:
            return Check(False, note=f"A {slow}-day EMA needs about {need} stored sessions; only {i + 1} are available.")
        s = pd.Series([b["close"] for b in bars[: i + 1]], dtype="float64")
        gap = technicals.ema(s, fast) - technicals.ema(s, slow)
        prev, now = float(gap.iloc[-2]), float(gap.iloc[-1])
        hit = (prev <= 0 < now) if direction == "up" else (prev >= 0 > now)
        facts = {"date": date, "fast_ema": round(float(technicals.ema(s, fast).iloc[-1]), 2),
                 "slow_ema": round(float(technicals.ema(s, slow).iloc[-1]), 2), "gap_now": round(now, 2), "gap_prev": round(prev, 2)}
        return Check(True, hit, f"{symbol}: {fast}-day EMA crossed {'above' if direction == 'up' else 'below'} {slow}-day EMA" if hit else "",
                     f"On {date} the {fast}-day EMA ({_inr(facts['fast_ema'])}) moved {'above' if direction == 'up' else 'below'} the "
                     f"{slow}-day EMA ({_inr(facts['slow_ema'])}). Moving averages lag price; treat this as context, not a call."
                     + (f" {cav}" if cav else ""), facts, cav)

    if preset == "level":
        level, direction, prev = params["level"], params["direction"], bars[i - 1]["close"]
        if touch:
            hi, lo = bar.get("high") or close, bar.get("low") or close
            hit = (prev <= level < hi) if direction == "above" else (prev >= level > lo)
            facts = {"date": date, "close": close, "prev_close": prev, "level": level, "mode": "touch"}
            return Check(True, hit, f"{symbol} traded {direction} {_inr(level)}" if hit else "",
                         f"Day {'high' if direction == 'above' else 'low'} {_inr(hi if direction == 'above' else lo)} on {date} crossed "
                         f"{_inr(level)} (previous close {_inr(prev)}); it closed at {_inr(close)}. End-of-day NSE data."
                         + (f" {cav}" if cav else ""), facts, cav)
        hit = (prev <= level < close) if direction == "above" else (prev >= level > close)
        facts = {"date": date, "close": close, "prev_close": prev, "level": level}
        return Check(True, hit, f"{symbol} closed {direction} {_inr(level)}" if hit else "",
                     f"Close {_inr(close)} on {date} (previous close {_inr(prev)}), crossing {_inr(level)}. End-of-day NSE data."
                     + (f" {cav}" if cav else ""), facts, cav)

    raise ValueError(f"Unknown breakout rule '{preset}'.")


def evaluate_live(preset: str, params: dict, bars: list[dict], ltp: float, baseline: float, symbol: str, stamp: str,
                  source: str = "Angel One SmartAPI") -> Check:
    """Did the live price cross the level? `bars` are the stored sessions BEFORE today; `baseline` is the price we last
    saw (or the previous close on the first look of the day), so a level crossed on an earlier day never fires again."""
    live_note = (f"Live price from {source} at {stamp}. A price can move back before the close; "
                 "the end-of-day check will show where it finished.")
    if preset in ("high_52w", "low_52w"):
        prior = bars[-LOOKBACK:]
        if len(prior) < MIN_PRIOR:
            return Check(False, note=f"Only {len(prior)} earlier sessions are stored; a 52-week comparison needs at least {MIN_PRIOR}.")
        if preset == "high_52w":
            ref = max(prior, key=lambda b: b.get("high") or b["close"])
            level = ref.get("high") or ref["close"]
            hit, word, rel = ltp > level and baseline <= level, "above", "high"
        else:
            ref = min(prior, key=lambda b: b.get("low") or b["close"])
            level = ref.get("low") or ref["close"]
            hit, word, rel = ltp < level and baseline >= level, "below", "low"
        cav = _caveat(prior, len(prior) - 1)
        facts = {"mode": "live", "ltp": ltp, "level": level, "level_date": ref["date"], "at": stamp, "source": source}
        return Check(True, hit, f"{symbol} traded {word} its 52-week {rel}" if hit else "",
                     f"{_inr(ltp)} vs the previous 52-week {rel} of {_inr(level)} set on {ref['date']}. {live_note}"
                     + (f" {cav}" if cav else ""), facts, cav)
    if preset == "level":
        level, direction = params["level"], params["direction"]
        hit = (baseline <= level < ltp) if direction == "above" else (baseline >= level > ltp)
        facts = {"mode": "live", "ltp": ltp, "level": level, "baseline": baseline, "at": stamp, "source": source}
        return Check(True, hit, f"{symbol} traded {direction} {_inr(level)}" if hit else "",
                     f"{_inr(ltp)} (previously {_inr(baseline)}) crossed {_inr(level)}. {live_note}", facts)
    return Check(False, note="This rule is only checked at the close.")


# --------------------------------------------------------------- results
# A title that announces *outcome*, not the intimation that a meeting will happen.
_RESULT = re.compile(r"\b(financial results?|unaudited|audited|quarterly results?|results? (?:for|of) the|"
                     r"outcome of board meeting|q[1-4] ?(?:fy)?\d{0,4} results?|results? highlights?)\b", re.I)
_INTIMATION = re.compile(r"\b(intimation|prior intimation|schedule|scheduled|will consider|to consider|to be held|"
                         r"date of board meeting|trading window|closure)\b|board meeting for", re.I)
SAMPLE_SOURCES = {"demo-seed"}


def is_results_announcement(filing: dict) -> bool:
    """Heuristic on the title. Sample (demo-seed) rows never count; they are not real announcements."""
    if (filing.get("source") or "") in SAMPLE_SOURCES:
        return False
    title = filing.get("title") or ""
    if filing.get("doc_type") == "result":
        return not _INTIMATION.search(title)
    return bool(_RESULT.search(title)) and not _INTIMATION.search(title)