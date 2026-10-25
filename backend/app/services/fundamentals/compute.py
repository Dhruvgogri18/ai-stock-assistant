"""Results and ratios computed from stored statements. Pure functions: no I/O, no network, easy to check by hand.

Every ratio carries its formula, its inputs and the period it is based on, so any figure can be traced. When an input is
missing or the ratio is not meaningful (a loss-making company's P/E, a bank's ROCE) the value is None and `reason` says why.
Money is stored in rupees; everything returned for display is in rupees crore (1 crore = 10,000,000).
"""
from __future__ import annotations

from datetime import date

from backend.app.services.fundamentals.labels import ALIASES

CRORE = 1e7
FINANCIAL_WORDS = ("bank", "financ", "insurance", "nbfc", "credit services", "asset management", "capital markets")
NOT_FOR_FINANCIALS = "Not meaningful for banks and financial companies."


def _d(period: str) -> date:
    return date.fromisoformat(period[:10])


def _days(a: str, b: str) -> int:
    return (_d(a) - _d(b)).days


def label_period(period: str) -> str:
    return _d(period).strftime("%b %Y")


def _div(a, b):
    return None if a is None or b in (None, 0) else a / b


def _cr(v):
    return None if v is None else v / CRORE


def _r(v, n: int = 2):
    return None if v is None else round(v, n)


def is_financial(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".lower()
    return any(w in text for w in FINANCIAL_WORDS)


class Fin:
    """The stored lines of one company, indexed as statement -> period_type -> period -> label -> value."""

    def __init__(self, rows: list[dict]) -> None:
        self.d: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
        for r in rows:
            if r.get("value") is not None:
                self.d.setdefault(r["statement"], {}).setdefault(r["period_type"], {}).setdefault(r["period"], {})[r["line_item"]] = r["value"]

    def periods(self, statement: str, ptype: str) -> list[str]:
        return sorted(self.d.get(statement, {}).get(ptype, {}))

    def raw(self, statement: str, ptype: str, period: str) -> dict[str, float]:
        return self.d.get(statement, {}).get(ptype, {}).get(period, {})

    def line(self, statement: str, ptype: str, period: str, key: str) -> float | None:
        items = self.raw(statement, ptype, period)
        for label in ALIASES[statement][key]:
            if label in items:
                return items[label]
        return None

    def series(self, statement: str, ptype: str, key: str) -> list[tuple[str, float]]:
        out = []
        for p in self.periods(statement, ptype):
            v = self.line(statement, ptype, p, key)
            if v is not None:
                out.append((p, v))
        return out

    def latest(self, statement: str, ptype: str, key: str) -> float | None:
        s = self.series(statement, ptype, key)
        return s[-1][1] if s else None

    def near(self, statement: str, ptype: str, key: str, period: str | None, tol_days: int = 70) -> float | None:
        """The value at `period`, or at the closest stored period within `tol_days`."""
        if period is None:
            return None
        best = None
        for p in self.periods(statement, ptype):
            gap = abs(_days(p, period))
            if gap <= tol_days and (best is None or gap < best[0]):
                v = self.line(statement, ptype, p, key)
                if v is not None:
                    best = (gap, v)
        return best[1] if best else None

    def has_data(self) -> bool:
        return bool(self.d)


# --------------------------------------------------------------------- results
def _year_ago(period: str, periods: list[str]) -> str | None:
    for q in periods:
        if 335 <= _days(period, q) <= 395:
            return q
    return None


def _quarter_ago(period: str, periods: list[str]) -> str | None:
    for q in periods:
        if 60 <= _days(period, q) <= 125:
            return q
    return None


def _growth(now, before):
    return None if now is None or before is None or before <= 0 else _r((now / before - 1) * 100)


def results_view(fin: Fin, ptype: str) -> dict:
    """Revenue, operating profit, net profit, EPS and margins per period (oldest first), with growth and, for quarters, TTM."""
    periods = fin.periods("income", ptype)

    def vals(p: str) -> dict:
        rev = fin.line("income", ptype, p, "revenue")
        oi = fin.line("income", ptype, p, "operating_income")
        oi = oi if oi is not None else fin.line("income", ptype, p, "ebit")
        return {"revenue": rev, "operating_income": oi, "net_income": fin.line("income", ptype, p, "net_income"),
                "eps": fin.line("income", ptype, p, "eps")}

    rows = []
    for p in periods:
        v = vals(p)
        ya, qa = _year_ago(p, periods), _quarter_ago(p, periods)
        vy, vq = (vals(ya) if ya else {}), (vals(qa) if qa and ptype == "quarterly" else {})
        rows.append({"period": p, "label": label_period(p),
                     "revenue": _r(_cr(v["revenue"])), "operating_income": _r(_cr(v["operating_income"])),
                     "net_income": _r(_cr(v["net_income"])), "eps": _r(v["eps"]),
                     "opm": _r(_div(v["operating_income"], v["revenue"]) * 100) if _div(v["operating_income"], v["revenue"]) is not None else None,
                     "npm": _r(_div(v["net_income"], v["revenue"]) * 100) if _div(v["net_income"], v["revenue"]) is not None else None,
                     "revenue_yoy": _growth(v["revenue"], vy.get("revenue")), "net_income_yoy": _growth(v["net_income"], vy.get("net_income")),
                     "revenue_qoq": _growth(v["revenue"], vq.get("revenue")), "net_income_qoq": _growth(v["net_income"], vq.get("net_income"))})

    ttm = None
    if ptype == "quarterly" and len(periods) >= 4:
        last4 = periods[-4:]
        if all(60 <= _days(b, a) <= 125 for a, b in zip(last4, last4[1:])):
            q = [vals(p) for p in last4]
            total = lambda k: None if any(x[k] is None for x in q) else sum(x[k] for x in q)
            rev, oi, ni, eps = total("revenue"), total("operating_income"), total("net_income"), total("eps")
            ttm = {"period": last4[-1], "label": f"TTM to {label_period(last4[-1])}", "revenue": _r(_cr(rev)), "operating_income": _r(_cr(oi)),
                   "net_income": _r(_cr(ni)), "eps": _r(eps),
                   "opm": _r(_div(oi, rev) * 100) if _div(oi, rev) is not None else None,
                   "npm": _r(_div(ni, rev) * 100) if _div(ni, rev) is not None else None}
    return {"period_type": ptype, "rows": rows, "ttm": ttm, "unit": "INR crore"}


# ---------------------------------------------------------------------- ratios
def _item(group, key, label, unit, value, formula, inputs=None, period=None, basis=None, reason=None) -> dict:
    return {"group": group, "key": key, "label": label, "unit": unit, "value": _r(value) if value is not None else None,
            "formula": formula, "inputs": inputs or [], "period": period, "basis": basis,
            "reason": None if value is not None else (reason or "Not enough stored data to compute this.")}


def _inp(name: str, value, unit: str = "INR crore") -> dict:
    return {"name": name, "value": _r(_cr(value) if unit == "INR crore" else value, 2), "unit": unit}


def _cagr(points: list[tuple[str, float]], max_years: int = 5):
    """(cagr %, years) over the longest span up to max_years between positive endpoints, else (None, 0)."""
    if len(points) < 3:
        return None, 0
    end = points[-1]
    best = None
    for start in points[:-1]:
        yrs = _days(end[0], start[0]) / 365.25
        if 1.5 <= yrs <= max_years + 0.5 and start[1] > 0 and end[1] > 0:
            if best is None or yrs > best[1]:
                best = (start, yrs)
    if not best:
        return None, 0
    (start, yrs) = best
    return _r(((end[1] / start[1]) ** (1 / yrs) - 1) * 100), round(yrs)


def ratios_view(fin: Fin, price: float | None, shares: float | None, sector: str | None = None, industry: str | None = None,
                provider_market_cap: float | None = None, provider_as_of: str | None = None) -> dict:
    fin_co = is_financial(sector, industry)
    annual = fin.periods("income", "annual")
    P = next((p for p in reversed(annual) if fin.line("income", "annual", p, "revenue") is not None
              or fin.line("income", "annual", p, "net_income") is not None), None)
    P1 = _year_ago(P, annual) if P else None
    g = lambda k, p=P: fin.line("income", "annual", p, k) if p else None
    rev, ni, eps = g("revenue"), g("net_income"), g("eps")
    oi = g("operating_income")
    ebit = g("ebit") if g("ebit") is not None else oi
    if ebit is None and g("pretax_income") is not None and g("interest_expense") is not None:
        ebit = g("pretax_income") + abs(g("interest_expense"))
    ebitda, interest = g("ebitda"), g("interest_expense")
    bal = lambda k, p=P: fin.near("balance", "annual", k, p)
    equity, equity_prev, assets, assets_prev = bal("equity"), bal("equity", P1), bal("total_assets"), bal("total_assets", P1)
    cl, ca, debt, cash = bal("current_liabilities"), bal("current_assets"), bal("total_debt"), bal("cash")
    shares = shares or bal("shares")
    plabel = label_period(P) if P else None

    q = results_view(fin, "quarterly")["ttm"]
    ttm_eps = q["eps"] if q else None
    mcap = price * shares if price and shares else None
    items: list[dict] = []
    add = items.append

    # ---- valuation
    if mcap is None and provider_market_cap:          # share count unknown (older load): show the provider's own figure, labelled as such
        mcap = provider_market_cap
        add(_item("Valuation", "market_cap", "Market cap", "INR crore", _cr(mcap), "Provider's reported market cap (share count not stored)",
                  [], provider_as_of, "as reported by the provider, not today's price"))
        shares = None
    else:
        add(_item("Valuation", "market_cap", "Market cap", "INR crore", _cr(mcap), "Price x shares outstanding",
                  [{"name": "Price", "value": _r(price), "unit": "INR"}, {"name": "Shares", "value": _r(shares, 0), "unit": "shares"}],
                  None, reason="Needs a price and the share count."))
    pe_eps, pe_basis = (ttm_eps, "TTM (last 4 quarters)") if ttm_eps is not None else (eps, f"latest annual ({plabel})" if plabel else None)
    if price and pe_eps is not None and pe_eps <= 0:
        pe, pe_reason = None, "Loss-making: P/E is not meaningful."
    else:
        pe, pe_reason = (_div(price, pe_eps) if price else None), "Needs a price and earnings per share."
    add(_item("Valuation", "pe", "P/E", "x", pe, "Price / earnings per share", [{"name": "Price", "value": _r(price), "unit": "INR"},
              {"name": "EPS", "value": _r(pe_eps), "unit": "INR"}], None, pe_basis, pe_reason))
    pb = _div(mcap, equity) if equity and equity > 0 and shares else None
    add(_item("Valuation", "pb", "P/B", "x", pb, "Market cap / shareholders' equity", [_inp("Market cap", mcap), _inp("Equity", equity)], plabel,
              reason="Needs a price, the share count and positive equity."))
    if fin_co:
        add(_item("Valuation", "ev_ebitda", "EV/EBITDA", "x", None, "(Market cap + debt - cash) / EBITDA", reason=NOT_FOR_FINANCIALS))
    else:
        ev = mcap + (debt or 0) - (cash or 0) if mcap is not None and debt is not None else None
        add(_item("Valuation", "ev_ebitda", "EV/EBITDA", "x", _div(ev, ebitda) if ebitda and ebitda > 0 else None,
                  "(Market cap + debt - cash) / EBITDA", [_inp("Market cap", mcap), _inp("Debt", debt), _inp("Cash", cash), _inp("EBITDA", ebitda)],
                  plabel, reason="Needs market cap, debt, cash and positive EBITDA."))

    # ---- profitability
    avg_eq = (equity + equity_prev) / 2 if equity is not None and equity_prev is not None else equity
    add(_item("Profitability", "roe", "ROE", "%", _div(ni, avg_eq) * 100 if _div(ni, avg_eq) is not None and avg_eq and avg_eq > 0 else None,
              "Net profit / average shareholders' equity", [_inp("Net profit", ni), _inp("Average equity", avg_eq)], plabel,
              "average of the last two year-ends" if equity_prev is not None else "year-end equity (only one year stored)"))
    if fin_co:
        add(_item("Profitability", "roce", "ROCE", "%", None, "EBIT / (total assets - current liabilities)", reason=NOT_FOR_FINANCIALS))
    else:
        ce = assets - cl if assets is not None and cl is not None else None
        add(_item("Profitability", "roce", "ROCE", "%", _div(ebit, ce) * 100 if _div(ebit, ce) is not None and ce and ce > 0 else None,
                  "EBIT / (total assets - current liabilities)", [_inp("EBIT", ebit), _inp("Capital employed", ce)], plabel, "year-end capital employed"))
    avg_assets = (assets + assets_prev) / 2 if assets is not None and assets_prev is not None else assets
    add(_item("Profitability", "roa", "ROA", "%", _div(ni, avg_assets) * 100 if _div(ni, avg_assets) is not None else None,
              "Net profit / average total assets", [_inp("Net profit", ni), _inp("Average assets", avg_assets)], plabel))
    om_num = oi if oi is not None else ebit
    add(_item("Profitability", "op_margin", "Operating margin", "%", _div(om_num, rev) * 100 if _div(om_num, rev) is not None else None,
              "Operating profit / revenue", [_inp("Operating profit", om_num), _inp("Revenue", rev)], plabel))
    add(_item("Profitability", "net_margin", "Net margin", "%", _div(ni, rev) * 100 if _div(ni, rev) is not None else None,
              "Net profit / revenue", [_inp("Net profit", ni), _inp("Revenue", rev)], plabel))

    # ---- growth
    prev = lambda k: g(k, P1) if P1 else None
    add(_item("Growth", "revenue_growth", "Revenue growth", "%", _growth(rev, prev("revenue")), "Revenue / revenue a year earlier - 1",
              [_inp("Revenue", rev), _inp("A year earlier", prev("revenue"))], plabel, "year on year"))
    add(_item("Growth", "ni_growth", "Net profit growth", "%", _growth(ni, prev("net_income")), "Net profit / net profit a year earlier - 1",
              [_inp("Net profit", ni), _inp("A year earlier", prev("net_income"))], plabel, "year on year",
              reason="Needs two years of positive net profit."))
    rc, ry = _cagr(fin.series("income", "annual", "revenue"))
    nc, ny = _cagr(fin.series("income", "annual", "net_income"))
    add(_item("Growth", "revenue_cagr", f"Revenue CAGR ({ry}-yr)" if ry else "Revenue CAGR", "%", rc, "(latest / earliest) ^ (1 / years) - 1", period=plabel,
              reason="Needs at least 3 years of positive revenue."))
    add(_item("Growth", "ni_cagr", f"Net profit CAGR ({ny}-yr)" if ny else "Net profit CAGR", "%", nc, "(latest / earliest) ^ (1 / years) - 1", period=plabel,
              reason="Needs at least 3 years of positive net profit."))

    # ---- leverage and liquidity
    for key, label, unit, formula, val, inputs in (
            ("debt_equity", "Debt / equity", "x", "Total debt / shareholders' equity", _div(debt, equity) if equity and equity > 0 else None,
             [_inp("Total debt", debt), _inp("Equity", equity)]),
            ("interest_cover", "Interest coverage", "x", "EBIT / interest expense", _div(ebit, abs(interest)) if interest else None,
             [_inp("EBIT", ebit), _inp("Interest", interest)]),
            ("current_ratio", "Current ratio", "x", "Current assets / current liabilities", _div(ca, cl), [_inp("Current assets", ca), _inp("Current liabilities", cl)])):
        add(_item("Leverage and liquidity", key, label, unit, None if fin_co else val, formula, inputs, plabel, reason=NOT_FOR_FINANCIALS if fin_co else None))

    # ---- cash flow
    cfo, capex, fcf_row = (fin.line("cashflow", "annual", fin.periods("cashflow", "annual")[-1], k) if fin.periods("cashflow", "annual") else None
                           for k in ("cfo", "capex", "fcf"))
    cp = fin.periods("cashflow", "annual")[-1] if fin.periods("cashflow", "annual") else None
    fcf = fcf_row if fcf_row is not None else (cfo + capex if cfo is not None and capex is not None else None)
    cf_rev = fin.near("income", "annual", "revenue", cp)
    cf_ni = fin.near("income", "annual", "net_income", cp)
    cl_label = label_period(cp) if cp else None
    add(_item("Cash flow", "cfo", "Operating cash flow", "INR crore", _cr(cfo), "Cash generated by operations (as reported)", period=cl_label))
    add(_item("Cash flow", "fcf", "Free cash flow", "INR crore", _cr(fcf), "Operating cash flow + capital expenditure (capex is negative)",
              [_inp("Operating cash flow", cfo), _inp("Capex", capex)], cl_label))
    add(_item("Cash flow", "fcf_margin", "Free cash flow margin", "%", _div(fcf, cf_rev) * 100 if _div(fcf, cf_rev) is not None else None,
              "Free cash flow / revenue", [_inp("Free cash flow", fcf), _inp("Revenue", cf_rev)], cl_label))
    add(_item("Cash flow", "cash_conversion", "Cash conversion", "x", _div(cfo, cf_ni) if cf_ni and cf_ni > 0 else None,
              "Operating cash flow / net profit", [_inp("Operating cash flow", cfo), _inp("Net profit", cf_ni)], cl_label,
              reason="Needs operating cash flow and positive net profit."))

    groups: list[dict] = []
    for it in items:
        if not groups or groups[-1]["name"] != it["group"]:
            groups.append({"name": it["group"], "items": []})
        groups[-1]["items"].append(it)
    return {"groups": groups, "by_key": {i["key"]: i for i in items}, "price": price, "market_cap_raw": mcap, "annual_period": P,
            "financial_company": fin_co}


def ratios_row(view: dict) -> dict:
    """The fields of the warehouse `ratios` row (read by the screener and the chat's ratios tool)."""
    v = lambda k: view["by_key"].get(k, {}).get("value")
    return {"pe": v("pe"), "pb": v("pb"), "roce": v("roce"), "roe": v("roe"), "net_margin": v("net_margin"),
            "op_margin": v("op_margin"), "market_cap": view.get("market_cap_raw")}