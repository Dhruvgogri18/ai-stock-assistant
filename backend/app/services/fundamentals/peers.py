"""Peer comparison among the stocks whose statements we hold, and the plain-English summary lines built from it.

A peer is a stock in the same sector (the NSE master's sector; the provider's sector only when the subject has none),
with statements loaded. Peers are ordered by market-cap closeness. We say exactly how many peers there are and how many
stocks in the sector have no data yet, rather than implying the group is complete.
"""
from __future__ import annotations

import math
import statistics

from backend.app.repositories import bars_repository as bars, warehouse
from backend.app.services.data import market_data
from backend.app.services.fundamentals import compute

METRICS = [  # key, label, unit, better ("high" / "low" / None = no ranking)
    ("market_cap", "Market cap", "INR crore", None), ("pe", "P/E", "x", None), ("pb", "P/B", "x", None),
    ("roe", "ROE", "%", "high"), ("roce", "ROCE", "%", "high"), ("op_margin", "Operating margin", "%", "high"),
    ("net_margin", "Net margin", "%", "high"), ("revenue_growth", "Revenue growth", "%", "high"),
    ("ni_growth", "Net profit growth", "%", "high"), ("debt_equity", "Debt / equity", "x", "low"), ("ret_1y", "1Y return", "%", None)]


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def sector_text(master_sector: str | None, status: dict | None) -> tuple[str | None, str | None]:
    """(sector, industry) text for the 'is this a financial company' check."""
    st = status or {}
    return master_sector or st.get("provider_sector"), " ".join(x for x in (st.get("industry"), st.get("provider_sector")) if x) or None


def _group(sym: str, cmap: dict, status: dict | None) -> tuple[str | None, str | None]:
    sec = (cmap.get(sym) or {}).get("sector")
    if sec:
        return "NSE sector", sec
    ps = (status or {}).get("provider_sector")
    return ("provider sector", ps) if ps else (None, None)


def company_row(sym: str, status: dict | None, cmap: dict, prices) -> dict:
    fin = compute.Fin(warehouse.get_financials_all(sym))
    price = float(prices.loc[sym, "LTP"]) if prices is not None and sym in prices.index and prices.loc[sym, "LTP"] == prices.loc[sym, "LTP"] else None
    sector, industry = sector_text((cmap.get(sym) or {}).get("sector"), status)
    prov = warehouse.get_ratios(sym) or {}
    view = compute.ratios_view(fin, price, (status or {}).get("shares"), sector, industry, prov.get("market_cap"), prov.get("as_of"))
    by = view["by_key"]
    row = {"symbol": sym, "name": (cmap.get(sym) or {}).get("name") or sym, "price": price}
    for key, _, _, _ in METRICS:
        row[key] = by[key]["value"] if key in by else None
    if prices is not None and sym in prices.index and "1Y Return %" in prices.columns:
        v = prices.loc[sym, "1Y Return %"]
        row["ret_1y"] = round(float(v), 2) if v == v else None
    row["_view"] = view
    return row


def _prices():
    try:
        snap = market_data.snapshot()
        return snap.df, market_data._meta(snap)
    except market_data.NoMarketData:
        return None, {"source": market_data.SOURCE, "as_of": None, "price_mode": "eod"}


def compare(symbol: str, limit: int = 6) -> dict:
    sym = symbol.upper().strip()
    cmap = bars.company_map()
    loaded = set(warehouse.symbols_with_financials())          # includes stocks from older loads that have no status record
    statuses = {s["symbol"]: s for s in warehouse.list_fundamentals_status()}
    prices, meta = _prices()
    subject_status = statuses.get(sym)
    taxonomy, group = _group(sym, cmap, subject_status)
    notes: list[str] = []
    out = {"symbol": sym, "group": {"taxonomy": taxonomy, "name": group}, "metrics": [{"key": k, "label": l, "unit": u, "better": b} for k, l, u, b in METRICS],
           "price_source": {k: meta.get(k) for k in ("source", "as_of", "price_mode")}, "peers": [], "median": None, "rank": {}, "subject": None, "notes": notes}
    if sym not in loaded:
        notes.append(f"No statements are stored for {sym} yet, so it cannot be compared.")
        return out
    if not group:
        notes.append(f"{sym} has no sector on record, so no peer group can be formed.")
        return out

    subject = company_row(sym, subject_status, cmap, prices)
    out["subject"] = {k: v for k, v in subject.items() if k != "_view"}

    pool = [s for s in sorted(loaded) if s != sym and _group(s, cmap, statuses.get(s)) == (taxonomy, group)]
    if taxonomy == "NSE sector":
        in_sector = sum(1 for c in cmap.values() if c.get("sector") == group)
    else:
        in_sector = sum(1 for st in statuses.values() if st.get("provider_sector") == group)
    rows = [company_row(s, statuses.get(s), cmap, prices) for s in pool]
    sm = subject.get("market_cap")
    rows.sort(key=lambda r: (abs(math.log(r["market_cap"] / sm)) if sm and r.get("market_cap") else math.inf, r["symbol"]))
    rows = rows[:max(1, limit)]
    out["peers"] = [{k: v for k, v in r.items() if k != "_view"} for r in rows]
    out["group"].update(companies_in_group=in_sector, with_data=len(pool) + 1)

    if rows:
        out["median"] = {k: (round(statistics.median(vs), 2) if (vs := [r[k] for r in rows if r.get(k) is not None]) else None) for k, *_ in METRICS}
        for key, _, _, better in METRICS:
            if not better or subject.get(key) is None:
                continue
            vals = [subject[key]] + [r[key] for r in rows if r.get(key) is not None]
            if len(vals) >= 3:
                ahead = sum(1 for v in vals if (v > subject[key] if better == "high" else v < subject[key]))
                out["rank"][key] = {"rank": ahead + 1, "of": len(vals)}
    else:
        notes.append(f"No other {group} stock has statements stored yet, so there is nothing to compare with.")
    missing = in_sector - out["group"]["with_data"]
    if missing > 0:
        notes.append(f"{missing} other stocks in {group} have no statements stored yet and are not in this comparison.")
    if len(rows) and len(rows) < 3:
        notes.append(f"Only {len(rows)} peer{'s' if len(rows) != 1 else ''} with data, so ranks and medians are not very meaningful.")
    if taxonomy == "provider sector":
        notes.append("This company has no NSE sector on record, so peers are grouped by the data provider's sector label.")
    return out


def summary_lines(view: dict | None, cmp: dict | None) -> list[str]:
    """The short plain-English facts shown under the company name. Each is computed; a line with missing inputs is left out."""
    lines: list[str] = []
    if not view:
        return lines
    by = view["by_key"]
    ni = by.get("ni_cagr")
    if ni and ni["value"] is not None:
        years = ni["label"].split("(")[-1].split("-")[0] if "(" in ni["label"] else ""
        lines.append(f"Net profit {'up' if ni['value'] >= 0 else 'down'} {abs(ni['value']):g}% a year over {years} years")
    if cmp and cmp.get("subject"):
        r = cmp["rank"].get("roce") or cmp["rank"].get("roe")
        key = "roce" if cmp["rank"].get("roce") else "roe"
        if r and by.get(key, {}).get("value") is not None:
            lines.append(f"{by[key]['label']} {by[key]['value']:g}%, {ordinal(r['rank'])} of {r['of']} compared {cmp['group']['name']} companies")
        med, pe = (cmp.get("median") or {}).get("pe"), by.get("pe", {}).get("value")
        if med and pe is not None:
            gap = (pe / med - 1) * 100
            lines.append(f"P/E {pe:g}x is a {abs(gap):.0f}% {'premium' if gap >= 0 else 'discount'} to the peer median of {med:g}x")
    return lines