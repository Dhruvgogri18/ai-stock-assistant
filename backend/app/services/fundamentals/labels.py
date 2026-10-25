"""Statement line labels. We store the provider's labels exactly as given (nothing is lost); these tables only say which
stored line means what, for the computed ratios, and how to display each line."""
from __future__ import annotations

# canonical name -> provider labels, best first
ALIASES: dict[str, dict[str, list[str]]] = {
    "income": {
        "revenue": ["Total Revenue", "Operating Revenue", "Revenue"],
        "operating_income": ["Operating Income"],
        "ebit": ["EBIT", "Operating Income"],
        "ebitda": ["EBITDA", "Normalized EBITDA"],
        "net_income": ["Net Income", "Net Income Common Stockholders"],
        "eps": ["Diluted EPS", "Basic EPS"],
        "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
        "pretax_income": ["Pretax Income"],
    },
    "balance": {
        "total_assets": ["Total Assets"],
        "current_assets": ["Current Assets", "Total Current Assets"],
        "current_liabilities": ["Current Liabilities", "Total Current Liabilities"],
        "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
        "total_debt": ["Total Debt"],
        "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
        "shares": ["Ordinary Shares Number", "Share Issued"],
    },
    "cashflow": {
        "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
        "capex": ["Capital Expenditure"],
        "fcf": ["Free Cash Flow"],
    },
}

# lines shown first (in this order) when a statement is displayed; everything else follows alphabetically
DISPLAY_ORDER: dict[str, list[str]] = {
    "income": ["Total Revenue", "Operating Revenue", "Cost Of Revenue", "Gross Profit", "Operating Expense", "Operating Income", "EBITDA",
               "EBIT", "Interest Expense", "Pretax Income", "Tax Provision", "Net Income", "Net Income Common Stockholders",
               "Basic EPS", "Diluted EPS"],
    "balance": ["Total Assets", "Current Assets", "Total Non Current Assets", "Total Liabilities Net Minority Interest", "Current Liabilities",
                "Total Debt", "Stockholders Equity", "Common Stock Equity", "Cash And Cash Equivalents", "Working Capital"],
    "cashflow": ["Operating Cash Flow", "Investing Cash Flow", "Financing Cash Flow", "Capital Expenditure", "Free Cash Flow",
                 "End Cash Position"],
}


def line_kind(label: str) -> str:
    """How to display a stored value: money (rupees, shown in crore), per_share (rupees as is), count (shares) or rate (a fraction)."""
    low = label.lower()
    if "eps" in low or "per share" in low:
        return "per_share"
    if "shares" in low or "share issued" in low:
        return "count"
    if "tax rate" in low or low.endswith(" rate") or low.endswith("ratio"):
        return "rate"
    return "money"


def order_key(statement: str, label: str) -> tuple[int, int, str]:
    order = DISPLAY_ORDER.get(statement, [])
    return (0, order.index(label), label) if label in order else (1, 0, label)