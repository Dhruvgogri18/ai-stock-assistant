"""Fake yfinance statements for tests and local demos. SYNTHETIC numbers: never load these into a real database."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

C = 1e7   # rupees per crore


@dataclass
class Spec:
    symbol: str
    revenue: float = 1000.0          # latest annual revenue, Rs crore
    growth: float = 0.12             # annual revenue growth
    opm: float = 0.22                # operating margin
    npm: float = 0.15                # net margin
    sector: str = "Technology"
    industry: str = "Information Technology Services"
    price: float = 300.0
    shares_cr: float = 10.0          # shares outstanding, crore
    equity_ratio: float = 0.55       # equity / total assets
    debt_ratio: float = 0.10         # debt / total assets
    currency: str = "INR"
    name: str = ""


ANNUAL = ["2025-03-31", "2024-03-31", "2023-03-31", "2022-03-31"]                         # newest first, as yfinance returns them
QUARTERLY = ["2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"]


def _frame(rows: dict[str, list], cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=[pd.Timestamp(c) for c in cols]).T


def income(s: Spec, annual: bool) -> pd.DataFrame:
    cols = ANNUAL if annual else QUARTERLY
    rev, ni, oi, eps, ebit, ebitda, interest, pretax, tax = ([] for _ in range(9))
    for i, _ in enumerate(cols):
        r = s.revenue * C * ((1 + s.growth) ** (-i if annual else -i / 4)) / (1 if annual else 4)
        rev.append(r); oi.append(r * s.opm); ebit.append(r * s.opm * 1.04); ebitda.append(r * (s.opm + 0.04))
        ni.append(r * s.npm); eps.append(r * s.npm / (s.shares_cr * C)); interest.append(r * 0.01)
        pretax.append(r * s.npm * 1.3); tax.append(r * s.npm * 0.3)
    return _frame({"Total Revenue": rev, "Operating Revenue": rev, "Cost Of Revenue": [x * 0.6 for x in rev], "Gross Profit": [x * 0.4 for x in rev],
                   "Operating Income": oi, "EBIT": ebit, "EBITDA": ebitda, "Interest Expense": interest, "Pretax Income": pretax,
                   "Tax Provision": tax, "Net Income": ni, "Diluted EPS": eps, "Basic EPS": eps,
                   "Tax Rate For Calcs": [0.25] * len(cols), "Special Income": [np.nan] * len(cols)}, cols)


def balance(s: Spec, annual: bool) -> pd.DataFrame:
    cols = ANNUAL if annual else QUARTERLY
    assets = [s.revenue * C * 1.3 * ((1 + s.growth) ** (-i if annual else -i / 4)) for i, _ in enumerate(cols)]
    return _frame({"Total Assets": assets, "Current Assets": [a * 0.5 for a in assets], "Current Liabilities": [a * 0.2 for a in assets],
                   "Total Debt": [a * s.debt_ratio for a in assets], "Stockholders Equity": [a * s.equity_ratio for a in assets],
                   "Cash And Cash Equivalents": [a * 0.1 for a in assets], "Ordinary Shares Number": [s.shares_cr * C] * len(cols)}, cols)


def cashflow(s: Spec, annual: bool) -> pd.DataFrame:
    cols = ANNUAL if annual else QUARTERLY
    cfo = [s.revenue * C * s.npm * 1.2 * ((1 + s.growth) ** (-i if annual else -i / 4)) / (1 if annual else 4) for i, _ in enumerate(cols)]
    return _frame({"Operating Cash Flow": cfo, "Capital Expenditure": [-c * 0.3 for c in cfo], "Free Cash Flow": [c * 0.7 for c in cfo],
                   "Investing Cash Flow": [-c * 0.5 for c in cfo]}, cols)


class FakeTicker:
    def __init__(self, s: Spec, broken: tuple[str, ...] = (), no_info: bool = False):
        self._s, self._broken = s, broken
        self.info = {} if no_info else {"longName": s.name or f"{s.symbol} Limited", "sector": s.sector, "industry": s.industry,
                                         "financialCurrency": s.currency, "currency": s.currency, "sharesOutstanding": s.shares_cr * C,
                                         "currentPrice": s.price, "marketCap": s.price * s.shares_cr * C}

    def _get(self, name, build):
        if name in self._broken:
            raise ConnectionError(f"{name} unavailable")
        return build()

    income_stmt = property(lambda self: self._get("income_stmt", lambda: income(self._s, True)))
    quarterly_income_stmt = property(lambda self: self._get("quarterly_income_stmt", lambda: income(self._s, False)))
    balance_sheet = property(lambda self: self._get("balance_sheet", lambda: balance(self._s, True)))
    quarterly_balance_sheet = property(lambda self: self._get("quarterly_balance_sheet", lambda: balance(self._s, False)))
    cashflow = property(lambda self: self._get("cashflow", lambda: cashflow(self._s, True)))
    quarterly_cashflow = property(lambda self: self._get("quarterly_cashflow", lambda: cashflow(self._s, False)))


def factory(specs: list[Spec], **kw):
    by = {s.symbol: s for s in specs}

    def make(symbol: str):
        if symbol.upper() not in by:
            return FakeTicker(Spec(symbol), no_info=True, broken=("income_stmt", "quarterly_income_stmt", "balance_sheet",
                                                                  "quarterly_balance_sheet", "cashflow", "quarterly_cashflow"))
        return FakeTicker(by[symbol.upper()], **kw)
    return make


# a realistic-looking IT peer group (synthetic)
IT_PEERS = [Spec("TCS", 2400, 0.09, 0.26, 0.19, price=3900, shares_cr=36.2, name="Tata Consultancy Services Limited"),
            Spec("INFY", 1650, 0.07, 0.24, 0.17, price=1500, shares_cr=41.4, name="Infosys Limited"),
            Spec("WIPRO", 900, 0.03, 0.18, 0.14, price=260, shares_cr=104.5, name="Wipro Limited"),
            Spec("HCLTECH", 1100, 0.10, 0.21, 0.15, price=1500, shares_cr=27.1, name="HCL Technologies Limited"),
            Spec("TECHM", 520, 0.02, 0.12, 0.08, price=1500, shares_cr=9.7, name="Tech Mahindra Limited"),
            Spec("LTIM", 380, 0.14, 0.17, 0.14, price=5400, shares_cr=2.96, name="LTIMindtree Limited"),
            Spec("PERSISTENT", 125, 0.22, 0.17, 0.12, price=5600, shares_cr=1.5, name="Persistent Systems Limited")]