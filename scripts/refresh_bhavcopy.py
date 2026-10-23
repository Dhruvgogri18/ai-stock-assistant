"""Load / refresh NSE end-of-day data.

    python -m scripts.refresh_bhavcopy                  catch up to the latest close
    python -m scripts.refresh_bhavcopy --days 400       scan the last 400 weekdays and fill any gaps (needed for
                                                        1-year returns and 52-week highs; takes a few minutes)
    python -m scripts.refresh_bhavcopy --master         force-refresh company names / industries
    python -m scripts.refresh_bhavcopy --file F.csv     ingest a bhavcopy you downloaded by hand (if NSE blocks scripts)
    python -m scripts.refresh_bhavcopy --status

The web app does the catch-up by itself in the background; use this for first-time backfill, cron / Task Scheduler,
or when something is stuck.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from backend.app.repositories import bars_repository as bars
from backend.app.services.data import bhavcopy, market_data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, help="weekdays to scan back for missing files")
    ap.add_argument("--master", action="store_true", help="refresh company names + industries")
    ap.add_argument("--file", help="ingest this bhavcopy CSV instead of downloading")
    ap.add_argument("--date", help="trading date of --file (YYYY-MM-DD); default: read from the file")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if a.status:
        print(json.dumps({**market_data.status(), "next_check": bhavcopy.next_check_hint(),
                          "recent": bars.recent_log(8)}, indent=2))
        return 0
    if a.file:
        d = date.fromisoformat(a.date) if a.date else None
        info = bhavcopy.ingest_day(d, open(a.file, "rb").read())
        bars.log_refresh("bhav", info["date"], "ok", info["rows"], "manual file")
        bhavcopy.refresh_master() if bhavcopy.master_due() else None
        market_data.invalidate()
        print(f"Loaded {info['rows']} rows for {info['date']}")
        return 0
    if a.master:
        print(json.dumps(bhavcopy.refresh_master(), indent=2))
        return 0

    res = bhavcopy.refresh(backfill_days=a.days, full_scan=bool(a.days))
    print(json.dumps(res, indent=2, default=str))
    print(json.dumps(market_data.status()))
    if res.get("status") == "error":
        print("\nSome files could not be fetched. If NSE is blocking scripts, download the file in a browser and run:\n"
              "  python -m scripts.refresh_bhavcopy --file sec_bhavdata_full_DDMMYYYY.csv", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
