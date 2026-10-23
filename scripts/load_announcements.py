"""Load BSE corporate announcements into the filings warehouse (RAG + news).

Usage (repo root, venv on):
  python -m scripts.load_announcements
  python -m scripts.load_announcements --symbols RELIANCE,TCS,INFY

Uses BSE public announcement API (no key). Best-effort; failures per symbol are printed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import db
from backend.app.config.settings import settings

# BSE announcement search (public; shape may change)
BSE_ANN = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"


def _headers():
    return {
        "User-Agent": "Mozilla/5.0 (compatible; StockResearch/1.0)",
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.bseindia.com",
        "Referer": "https://www.bseindia.com/",
    }


def fetch_bse_announcements(symbol: str, days: int = 90) -> list[dict]:
    """Return list of {title, body, url, filed_at}."""
    # BSE API often wants scrip code; try symbol as strsearch
    to = datetime.now()
    fr = to - timedelta(days=days)
    params = {
        "pageno": 1,
        "strCat": "-1",
        "strPrevDate": fr.strftime("%Y%m%d"),
        "strScrip": "",
        "strSearch": "P",
        "strToDate": to.strftime("%Y%m%d"),
        "strType": "C",
        "pageSize": 30,
    }
    # Alternate: subcategory with text search
    out = []
    try:
        with httpx.Client(timeout=30.0, headers=_headers(), follow_redirects=True) as client:
            # Corporate announcements by security — fallback scrape-style endpoint
            r = client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w",
                params={
                    "strCat": "-1",
                    "strPrevDate": fr.strftime("%Y%m%d"),
                    "strScrip": "",
                    "strSearch": symbol[:6],
                    "strToDate": to.strftime("%Y%m%d"),
                    "strType": "C",
                },
            )
            if r.status_code != 200:
                return out
            data = r.json()
        table = data.get("Table") or data.get("data") or []
        if isinstance(table, dict):
            table = [table]
        for row in table[:25]:
            title = row.get("NEWSSUB") or row.get("HEADLINE") or row.get("title") or "Announcement"
            body = row.get("MORE") or row.get("NEWS_DT") or title
            # filter rough match on symbol/name
            blob = f"{title} {body} {row.get('SLONGNAME', '')}".upper()
            if symbol.upper() not in blob and symbol[:4].upper() not in blob:
                continue
            dt = row.get("NEWS_DT") or row.get("DTOFNEWS") or ""
            url = row.get("ATTACHMENTNAME") or row.get("NSURL") or ""
            if url and not str(url).startswith("http"):
                url = f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{url}"
            out.append({
                "title": str(title)[:300],
                "body": str(body)[:4000],
                "url": str(url)[:500],
                "filed_at": str(dt)[:32],
            })
    except Exception as e:
        print(f"  BSE fetch error {symbol}: {e}")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="", help="Comma-separated NSE symbols")
    p.add_argument("--days", type=int, default=90)
    args = p.parse_args()

    db.init_db()
    symbols = [s.strip().upper() for s in (args.symbols or settings.announcements_symbols).split(",") if s.strip()]
    print(f"Loading announcements for {len(symbols)} symbols (last {args.days}d)…")
    total = 0
    for sym in symbols:
        rows = fetch_bse_announcements(sym, days=args.days)
        print(f"  {sym}: {len(rows)} hits")
        for r in rows:
            db.insert_filing(
                sym,
                "announcement",
                r["title"],
                r["body"] or r["title"],
                url=r.get("url") or "",
                filed_at=r.get("filed_at") or datetime.now(timezone.utc).isoformat(),
                source="bse-announcements",
            )
            total += 1
    print(f"Done. Inserted {total} filings.")
    print("Tip: news/search_filings tools will read from the local filings index.")


if __name__ == "__main__":
    main()
