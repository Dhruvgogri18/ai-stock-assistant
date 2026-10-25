"""Load full financial statements (income statement, balance sheet, cash flow; annual and quarterly) for many stocks.

    python -m scripts.load_financials --dry-run 2             # fetch 2 stocks and show what would be stored (nothing is written)
    python -m scripts.load_financials --symbols TCS,INFY      # specific stocks
    python -m scripts.load_financials --watchlist             # everything on your watchlist
    python -m scripts.load_financials --top 500               # the 500 most liquid stocks (the default when nothing else is given)
    python -m scripts.load_financials --refresh-loaded        # re-fetch stocks that already have statements

Runs from yfinance (unofficial, rate limited), so it is slow and gentle on purpose: --pause seconds between stocks. Stocks fetched
successfully within --max-age-days are skipped unless --force, so an interrupted run can simply be started again. This makes real
network calls: run it yourself, it is not part of the tests.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.repositories import bars_repository as bars, warehouse, watchlist_repository as watchlist  # noqa: E402
from backend.app.services.fundamentals import loader  # noqa: E402


def select_symbols(symbols: list[str] | None = None, use_watchlist: bool = False, top: int | None = None, refresh_loaded: bool = False,
                   max_age_days: int = 7, force: bool = False, limit: int | None = None, now: datetime | None = None) -> tuple[list[str], dict]:
    """Which stocks to fetch, and why others were left out. Pure apart from reading the databases."""
    now = now or datetime.now(timezone.utc)
    statuses = {s["symbol"]: s for s in warehouse.list_fundamentals_status()}
    if symbols:
        base, source = [s.upper() for s in symbols], "given symbols"
    elif use_watchlist:
        base, source = watchlist.items(), "watchlist"
    elif refresh_loaded:
        base, source = sorted(s for s, st in statuses.items() if (st.get("annual_periods") or 0) > 0), "already loaded"
    else:
        known = set(bars.company_map())
        base = [s for s in bars.top_by_turnover(top or 500) if s in known]
        source = f"top {top or 500} by traded value"
    skipped = {"fresh": [], "unknown": []}
    keep = []
    known_syms = set(bars.company_map())
    for s in dict.fromkeys(base):
        if known_syms and s not in known_syms and not symbols:
            skipped["unknown"].append(s)
            continue
        st = statuses.get(s)
        if st and not force and not st.get("error") and st.get("fetched_at"):
            try:
                at = datetime.strptime(st["fetched_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
                if now - at < timedelta(days=max_age_days):
                    skipped["fresh"].append(s)
                    continue
            except ValueError:
                pass
        keep.append(s)
    return (keep[:limit] if limit else keep), {"source": source, **skipped}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="", help="comma-separated NSE symbols")
    p.add_argument("--watchlist", action="store_true")
    p.add_argument("--top", type=int, default=None, help="the N most liquid stocks (default 500)")
    p.add_argument("--refresh-loaded", action="store_true")
    p.add_argument("--max-age-days", type=int, default=7)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--pause", type=float, default=1.5, help="seconds between stocks (be gentle with the provider)")
    p.add_argument("--dry-run", type=int, default=0, metavar="N", help="fetch N stocks, print what was parsed, store nothing")
    a = p.parse_args()

    warehouse.init_db()
    syms, info = select_symbols([s for s in a.symbols.split(",") if s.strip()] or None, a.watchlist, a.top, a.refresh_loaded,
                                a.max_age_days, a.force, a.limit)
    print(f"Source: {info['source']}. To fetch: {len(syms)}. Skipped: {len(info['fresh'])} fetched recently, {len(info['unknown'])} not in the NSE master.")
    if a.dry_run:
        for s in syms[:a.dry_run]:
            got = loader.fetch(s, pause=0.3)
            print(f"\n{s}: info {'ok' if got.info else 'MISSING'}, currency {got.info.get('financialCurrency') or got.info.get('currency')}")
            for (st, pt), periods in sorted(got.frames.items()):
                print(f"  {st:9s}{pt:10s}{len(periods)} periods, {len(next(iter(periods.values())))} lines, newest {max(periods)}")
            for e in got.errors:
                print(f"  problem: {e}")
        print("\nDry run: nothing was stored.")
        return
    ok, failed = 0, []
    for i, s in enumerate(syms, 1):
        try:
            r = loader.refresh_symbol(s, pause=0.3)
            ok += 1
            print(f"[{i}/{len(syms)}] {s}: {r['annual_periods']} annual, {r['quarterly_periods']} quarterly periods" + (f" (partial: {len(r['errors'])} problem(s))" if r["errors"] else ""), flush=True)
        except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the run
            failed.append((s, str(e)[:120]))
            print(f"[{i}/{len(syms)}] {s}: FAILED {str(e)[:120]}", flush=True)
        time.sleep(a.pause)
    print(f"\nDone. Stored {ok}, failed {len(failed)}.")
    for s, why in failed[:30]:
        print(f"  {s}: {why}")


if __name__ == "__main__":
    main()