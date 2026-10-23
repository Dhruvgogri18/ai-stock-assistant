"""What a screener can show and filter on, and the curated screeners shipped with the app.

A *definition* is plain JSON so it can be stored, shared and re-run:

    {"columns": ["Sector", "Day Change %"],                  # "Company Name" is always shown first
     "filters": [{"metric": "Day Change %", "op": ">", "value": 3}],
     "sort": {"metric": "Day Change %", "dir": "desc"},
     "universe": {"include_sme": true}}

Metric keys are the same strings the chat `screener` tool already uses (data.ALL_METRICS), so a chat
result converts to a definition without renaming anything.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

NUM, TEXT = "number", "text"
NUM_OPS = (">", "<", ">=", "<=", "=", "!=")
TEXT_OPS = ("contains", "=", "!=")
COMPANY = "Company Name"            # always the first column; cannot be removed


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str
    kind: str = NUM
    fmt: str = "num"          # text | price | pct | pct_signed | int | cr | ratio | num
    source: str = "price"     # price (NSE bhavcopy) | master (company master) | ratios | holding
    desc: str = ""
    render: str = ""          # "day_change" = LTP, change and % in one cell

    def public(self) -> dict:
        d = asdict(self)
        d["ops"] = list(TEXT_OPS if self.kind == TEXT else NUM_OPS)
        return d


# (group, one-line description shown in the Refine panel)
GROUPS = [
    ("Company Fundamentals", "Sector, market cap and valuation ratios."),
    ("Company Performance", "Price returns and 52-week range, from stored closes."),
    ("Market Data", "Last close, volume, turnover and delivery from NSE end-of-day files."),
    ("Profitability", "Return ratios and margins."),
    ("Shareholding", "Latest promoter, FII and DII holding."),
]
_F, _P, _M, _R, _S = (g for g, _ in GROUPS)

METRICS: list[Metric] = [
    Metric(COMPANY, "Company Name", _F, TEXT, "text", "master", "Company name and NSE symbol."),
    Metric("Sector", "Sector", _F, TEXT, "text", "master", "Sector from the company master (not every company has one)."),
    Metric("Market Cap (Cr)", "Market Cap (Cr)", _F, NUM, "cr", "ratios", "Market capitalisation in Rs crore."),
    Metric("P/E", "P/E", _F, NUM, "ratio", "ratios", "Price to earnings."),
    Metric("P/B", "P/B", _F, NUM, "ratio", "ratios", "Price to book value."),

    Metric("1M Return %", "1M Return", _P, NUM, "pct_signed", "price", "Close vs 21 trading days ago (not split-adjusted)."),
    Metric("3M Return %", "3M Return", _P, NUM, "pct_signed", "price", "Close vs 63 trading days ago (not split-adjusted)."),
    Metric("1Y Return %", "1Y Return", _P, NUM, "pct_signed", "price", "Close vs 250 trading days ago (not split-adjusted)."),
    Metric("52W High", "52W High", _P, NUM, "price", "price", "Highest traded price in the last 250 sessions."),
    Metric("52W Low", "52W Low", _P, NUM, "price", "price", "Lowest traded price in the last 250 sessions."),
    Metric("% From 52W High", "% From 52W High", _P, NUM, "pct_signed", "price", "Last close vs 52-week high."),
    Metric("% From 52W Low", "% From 52W Low", _P, NUM, "pct_signed", "price", "Last close vs 52-week low."),

    Metric("Day Change %", "Day Change", _M, NUM, "pct_signed", "price", "Last close with change vs the exchange's previous close.", "day_change"),
    Metric("LTP", "Last Close", _M, NUM, "price", "price", "Last close (end of day, not live)."),
    Metric("Prev Close", "Prev Close", _M, NUM, "price", "price", "Exchange previous close (adjusted for splits and bonus)."),
    Metric("Open", "Open", _M, NUM, "price", "price", "Opening price."),
    Metric("High", "High", _M, NUM, "price", "price", "Day high."),
    Metric("Low", "Low", _M, NUM, "price", "price", "Day low."),
    Metric("Volume", "Volume", _M, NUM, "int", "price", "Shares traded."),
    Metric("Turnover (Cr)", "Turnover (Cr)", _M, NUM, "cr", "price", "Value traded in Rs crore."),
    Metric("Delivery %", "Delivery %", _M, NUM, "pct", "price", "Share of traded quantity that was delivered."),

    Metric("ROCE %", "ROCE %", _R, NUM, "pct", "ratios", "Return on capital employed."),
    Metric("ROE %", "ROE %", _R, NUM, "pct", "ratios", "Return on equity."),
    Metric("Net margin %", "Net margin %", _R, NUM, "pct", "ratios", "Net profit margin."),
    Metric("Operating margin %", "Operating margin %", _R, NUM, "pct", "ratios", "Operating profit margin."),

    Metric("Promoter %", "Promoter %", _S, NUM, "pct", "holding", "Promoter holding, latest quarter loaded."),
    Metric("FII %", "FII %", _S, NUM, "pct", "holding", "Foreign institutional holding, latest quarter loaded."),
    Metric("DII %", "DII %", _S, NUM, "pct", "holding", "Domestic institutional holding, latest quarter loaded."),
    Metric("Public %", "Public %", _S, NUM, "pct", "holding", "Public holding, latest quarter loaded."),
]
BY_KEY = {m.key: m for m in METRICS}
HOLDER_COLUMN = {"promoter": "Promoter %", "fii": "FII %", "dii": "DII %", "public": "Public %"}
# metrics that need many sessions of stored bars before they can be computed
NEEDS_HISTORY = {"1M Return %", "3M Return %", "1Y Return %", "52W High", "52W Low", "% From 52W High", "% From 52W Low"}

DEFAULT_COLUMNS = ["Sector", "Day Change %", "Turnover (Cr)", "Volume", "Market Cap (Cr)"]
MOVER_COLUMNS = ["Sector", "Day Change %", "Turnover (Cr)", "Volume", "Delivery %"]


def _f(metric: str, op: str, value) -> dict:
    return {"metric": metric, "op": op, "value": value}


def _d(filters, sort, columns=None, include_sme=True) -> dict:
    return {"columns": list(columns or DEFAULT_COLUMNS), "filters": filters,
            "sort": {"metric": sort[0], "dir": sort[1]}, "universe": {"include_sme": include_sme}}


# Curated screeners. Only metrics we actually store are used. Fundamental-based ones say so in the description,
# because the warehouse currently holds ratios for a small universe (the page shows live coverage counts).
CURATED: list[dict] = [
    {"id": "c-top-gainers", "name": "Top NSE Gainers Today (Liquid)", "category": "Momentum",
     "description": "NSE stocks up over 3% at the last close with at least Rs 1 crore traded, sorted by percentage gain.",
     "definition": _d([_f("Day Change %", ">", 3), _f("Turnover (Cr)", ">=", 1)], ("Day Change %", "desc"), MOVER_COLUMNS)},
    {"id": "c-top-losers", "name": "Top NSE Losers Today (Liquid)", "category": "Momentum",
     "description": "NSE stocks down over 3% at the last close with at least Rs 1 crore traded, biggest falls first.",
     "definition": _d([_f("Day Change %", "<", -3), _f("Turnover (Cr)", ">=", 1)], ("Day Change %", "asc"), MOVER_COLUMNS)},
    {"id": "c-most-active", "name": "Most Active by Value Traded", "category": "Momentum",
     "description": "The stocks with the highest value traded at the last close, with day change and delivery %.",
     "definition": _d([_f("Turnover (Cr)", ">=", 1)], ("Turnover (Cr)", "desc"), MOVER_COLUMNS)},
    {"id": "c-high-delivery-up", "name": "High Delivery Rallies", "category": "Momentum",
     "description": "Stocks up more than 2% where over 60% of traded quantity was delivered, with at least Rs 1 crore traded.",
     "definition": _d([_f("Day Change %", ">", 2), _f("Delivery %", ">", 60), _f("Turnover (Cr)", ">=", 1)],
                      ("Delivery %", "desc"), MOVER_COLUMNS)},
    {"id": "c-near-52w-high", "name": "Near 52-Week High", "category": "Momentum",
     "description": "Stocks closing within 2% of their 52-week high, liquid names first. Needs about a year of stored history.",
     "definition": _d([_f("% From 52W High", ">=", -2), _f("Turnover (Cr)", ">=", 1)], ("Turnover (Cr)", "desc"),
                      ["Sector", "LTP", "52W High", "% From 52W High", "Turnover (Cr)"])},
    {"id": "c-near-52w-low", "name": "Near 52-Week Low", "category": "Momentum",
     "description": "Stocks closing within 2% of their 52-week low, liquid names first. Needs about a year of stored history.",
     "definition": _d([_f("% From 52W Low", "<=", 2), _f("Turnover (Cr)", ">=", 1)], ("Turnover (Cr)", "desc"),
                      ["Sector", "LTP", "52W Low", "% From 52W Low", "Turnover (Cr)"])},
    {"id": "c-1y-leaders", "name": "1-Year Momentum Leaders", "category": "Momentum",
     "description": "Stocks up more than 50% over a year with at least Rs 1 crore traded. Returns are not split-adjusted.",
     "definition": _d([_f("1Y Return %", ">", 50), _f("Turnover (Cr)", ">=", 1)], ("1Y Return %", "desc"),
                      ["Sector", "1M Return %", "3M Return %", "1Y Return %", "Turnover (Cr)"])},
    {"id": "c-large-cap-movers", "name": "Large-Cap Movers", "category": "Fundamental",
     "description": "Companies above Rs 10,000 crore market cap, ranked by day change. Market cap is loaded for part of the market only.",
     "definition": _d([_f("Market Cap (Cr)", ">", 10000)], ("Day Change %", "desc"),
                      ["Sector", "Day Change %", "Market Cap (Cr)", "P/E", "Turnover (Cr)"])},
    {"id": "c-quality", "name": "Quality: High Returns on Capital", "category": "Quality",
     "description": "ROCE above 20%, ROE above 15% and net margin above 10%. Ratios are loaded for part of the market only.",
     "definition": _d([_f("ROCE %", ">", 20), _f("ROE %", ">", 15), _f("Net margin %", ">", 10)], ("ROCE %", "desc"),
                      ["Sector", "ROCE %", "ROE %", "Net margin %", "Market Cap (Cr)"])},
    {"id": "c-value", "name": "Reasonable Valuation, Decent Returns", "category": "Valuation",
     "description": "P/E below 25 with ROCE above 15%. Ratios are loaded for part of the market only.",
     "definition": _d([_f("P/E", "<", 25), _f("ROCE %", ">", 15)], ("P/E", "asc"),
                      ["Sector", "P/E", "P/B", "ROCE %", "Market Cap (Cr)"])},
    {"id": "c-low-pb", "name": "Low Price-to-Book", "category": "Valuation",
     "description": "P/B below 2 with ROE above 12%. Ratios are loaded for part of the market only.",
     "definition": _d([_f("P/B", "<", 2), _f("ROE %", ">", 12)], ("P/B", "asc"),
                      ["Sector", "P/B", "P/E", "ROE %", "Market Cap (Cr)"])},
    {"id": "c-promoter", "name": "High Promoter Holding", "category": "Ownership",
     "description": "Promoter holding above 65% with market cap above Rs 500 crore. Shareholding data must be loaded for results.",
     "definition": _d([_f("Promoter %", ">", 65), _f("Market Cap (Cr)", ">", 500)], ("Promoter %", "desc"),
                      ["Sector", "Promoter %", "FII %", "DII %", "Market Cap (Cr)"])},
]
CURATED_BY_ID = {c["id"]: c for c in CURATED}
