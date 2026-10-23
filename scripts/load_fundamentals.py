"""Load fundamentals (ratios + income lines) via yfinance into the warehouse.

Usage:
  python -m scripts.load_fundamentals
  python -m scripts.load_fundamentals --symbols RELIANCE,TCS,INFY
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import db
from backend.app.data import UNIVERSE


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="")
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(UNIVERSE)

    import yfinance as yf

    db.init_db()
    print(f"Loading fundamentals for {len(symbols)} symbols via yfinance…")
    for sym in symbols:
        print(f"  {sym}…", flush=True)
        try:
            t = yf.Ticker(sym + ".NS")
            info = t.info or {}
            name = info.get("longName") or sym
            sector = info.get("sector") or ""
            db.upsert_company(sym, name=name, sector=sector)
            pct = lambda x: round(x * 100, 2) if x is not None else None
            roce = None
            try:
                b = t.balance_sheet
                roce = round(float(t.income_stmt.loc["EBIT"].iloc[0] /
                                   (b.loc["Total Assets"].iloc[0] - b.loc["Current Liabilities"].iloc[0])) * 100, 2)
            except Exception:
                pass
            db.upsert_ratios({
                "symbol": sym, "name": name, "sector": sector,
                "pe": info.get("trailingPE"), "pb": info.get("priceToBook"),
                "roce": roce, "roe": pct(info.get("returnOnEquity")),
                "net_margin": pct(info.get("profitMargins")),
                "op_margin": pct(info.get("operatingMargins")),
                "market_cap": info.get("marketCap"),
                "source": "yfinance-fundamentals",
            })
            try:
                inc = t.income_stmt
                if inc is not None and not inc.empty:
                    for col in list(inc.columns)[:5]:
                        period = str(col.date()) if hasattr(col, "date") else str(col)
                        items = {}
                        for label, keys in (
                            ("Total Revenue", ["Total Revenue", "Operating Revenue"]),
                            ("Operating Income", ["Operating Income", "EBIT"]),
                            ("Net Income", ["Net Income"]),
                        ):
                            for k in keys:
                                if k in inc.index:
                                    try:
                                        items[label] = float(inc.loc[k, col])
                                    except Exception:
                                        items[label] = None
                                    break
                        if items:
                            db.upsert_financial_rows(sym, period, "annual", "income", items)
            except Exception as e:
                print(f"    statements: {e}")
        except Exception as e:
            print(f"  FAIL {sym}: {e}")
    print(f"Done. ratios_count={db.ratios_count()}")


if __name__ == "__main__":
    main()
