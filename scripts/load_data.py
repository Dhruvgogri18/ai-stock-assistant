"""Nightly / on-demand loader: yfinance (bootstrap) → warehouse.

Usage (from repo root, venv active):
  python -m scripts.load_data
  python -m scripts.load_data --symbols RELIANCE,TCS,INFY
  python -m scripts.load_data --filings-only

Later: point this at IndianAPI / Upstox / exchange files instead of yfinance.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as python -m scripts.load_data from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.repositories import warehouse as db
from backend.app.config.settings import settings
from backend.app.services.data.service import UNIVERSE


def load_from_yfinance(symbols: list[str]) -> None:
    import yfinance as yf

    for sym in symbols:
        print(f"  loading {sym}…", flush=True)
        try:
            t = yf.Ticker(sym + ".NS")
            info = t.info or {}
            name = info.get("longName") or sym
            sector = info.get("sector") or ""
            db.upsert_company(sym, name=name, sector=sector)

            # Ratios
            roce = None
            try:
                b = t.balance_sheet
                roce = round(float(t.income_stmt.loc["EBIT"].iloc[0] /
                                   (b.loc["Total Assets"].iloc[0] - b.loc["Current Liabilities"].iloc[0])) * 100, 2)
            except Exception:
                pass
            pct = lambda x: round(x * 100, 2) if x is not None else None
            db.upsert_ratios({
                "symbol": sym,
                "name": name,
                "sector": sector,
                "pe": info.get("trailingPE"),
                "pb": info.get("priceToBook"),
                "roce": roce,
                "roe": pct(info.get("returnOnEquity")),
                "net_margin": pct(info.get("profitMargins")),
                "op_margin": pct(info.get("operatingMargins")),
                "market_cap": info.get("marketCap"),
                "source": "yfinance-bootstrap",
            })

            # Annual income lines
            try:
                inc = t.income_stmt
                if inc is not None and not inc.empty:
                    for col in inc.columns[:5]:
                        period = str(col.date()) if hasattr(col, "date") else str(col)
                        items = {}
                        for label, keys in (
                            ("Total Revenue", ["Total Revenue", "Operating Revenue"]),
                            ("Operating Income", ["Operating Income", "EBIT"]),
                            ("Net Income", ["Net Income"]),
                        ):
                            for k in keys:
                                if k in inc.index:
                                    val = inc.loc[k, col]
                                    try:
                                        items[label] = float(val)
                                    except Exception:
                                        items[label] = None
                                    break
                        if items:
                            db.upsert_financial_rows(sym, period, "annual", "income", items)
            except Exception as e:
                print(f"    financials skip: {e}")

            # Prices (weekly-ish via history)
            try:
                h = t.history(period="5y", interval="1wk")
                if h is not None and not h.empty:
                    with db.connect() as conn:
                        for d, row in h.iterrows():
                            conn.execute(
                                """INSERT INTO prices(symbol, date, close, volume)
                                   VALUES(?,?,?,?)
                                   ON CONFLICT(symbol, date) DO UPDATE SET close=excluded.close, volume=excluded.volume""",
                                (sym, str(d.date()), float(row["Close"]), float(row.get("Volume") or 0)),
                            )
            except Exception as e:
                print(f"    prices skip: {e}")

            # Synthetic ownership placeholder until IndianAPI/SHP wired
            # (real promoter/FII/DII comes from IndianAPI or exchange SHP files)
            db.upsert_shareholding(sym, "latest", {
                "promoter": None, "fii": None, "dii": None, "public": None,
            })

            # News → filings index (lightweight RAG corpus)
            try:
                for n in (t.news or [])[:5]:
                    c = n.get("content", n)
                    title = c.get("title") or "News"
                    body = title
                    url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
                    db.insert_filing(sym, "announcement", title, body, url=url, source="yfinance-news")
            except Exception:
                pass

        except Exception as e:
            print(f"  FAIL {sym}: {e}")


def main():
    p = argparse.ArgumentParser(description="Load NSE symbols into local warehouse")
    p.add_argument("--symbols", default="", help="Comma-separated NSE symbols (default: built-in universe)")
    p.add_argument("--filings-only", action="store_true", help="Only seed demo filings")
    args = p.parse_args()

    print(f"DB: {settings.database_url or settings.db_path}")
    db.init_db()
    db.seed_demo_filings()
    print("Schema ready + demo filings seeded.")

    if args.filings_only:
        return

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(UNIVERSE)
    print(f"Loading {len(symbols)} symbols via yfinance bootstrap…")
    load_from_yfinance(symbols)
    print(f"Done. ratios_count={db.ratios_count()}")
    print("Tip: set INDIANAPI_KEY and extend this loader to pull promoter/FII/DII + full statements.")


if __name__ == "__main__":
    main()
