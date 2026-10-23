"""Check Angel One live quotes against YOUR account. Run it yourself: it makes real API calls (a handful of requests).

    python -m scripts.check_angel_quotes

The live-price layer assumes SmartAPI's batch quote endpoint takes up to 50 tokens per request and returns ltp/open/high/low/
close (close = PREVIOUS close), tradeVolume, avgPrice and exchFeedTime. That was written from the API docs and could not be
tested against a live account when it was built, so this script prints what your account actually returns. It never prints
credentials.
"""
from __future__ import annotations

import sys
import time

from backend.app.providers import angel_one
from backend.app.services.data import live_prices, market_data


def main() -> None:
    if not angel_one.configured():
        sys.exit("Angel One is not configured. Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD and ANGEL_TOTP_SECRET in .env.")

    t = time.time()
    idx = angel_one.token_index()
    print(f"1. Instrument dump: {len(idx):,} NSE cash symbols indexed in {time.time() - t:.1f}s")

    stored, universe = {}, []
    try:
        snap = market_data.eod_snapshot()
        stored = snap.df["LTP"].to_dict()
        universe = live_prices.universe_symbols(snap)          # the stocks screens cover (listed equities + SME, no ETFs/bonds)
        covered = sum(1 for s in universe if s in idx)
        print(f"2. Your screening universe: {covered:,} of {len(universe):,} stocks have an Angel token "
              f"({len(universe) - covered:,} will never get a live price). Last stored session: {snap.as_of}")
    except market_data.NoMarketData:
        print("2. No stored prices yet, skipping the coverage check.")

    sample = [s for s in ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC") if s in idx] or list(idx)[:5]
    gaps = []
    for attempt in (1, 2):
        t = time.time()
        found, missing = angel_one.quote_chunk(sample)
        gaps.append(time.time() - t)
        print(f"3.{attempt} Batch of {len(sample)}: {len(found)} quotes, {len(missing)} without one, {gaps[-1]:.2f}s")
    print(f"\n   {'symbol':<12}{'ltp':>10}{'prev close':>12}{'chg %':>8}{'high':>10}{'low':>10}{'volume':>14}  feed time")
    mismatch = 0
    for s, q in found.items():
        pc = q.get("prev_close")
        chg = f"{(q['ltp'] / pc - 1) * 100:+.2f}" if pc else "n/a"
        print(f"   {s:<12}{q['ltp']:>10.2f}{pc if pc is not None else float('nan'):>12.2f}{chg:>8}{(q['high'] or float('nan')):>10.2f}"
              f"{(q['low'] or float('nan')):>10.2f}{(q['volume'] or 0):>14,.0f}  {q.get('feed_time')}")
        if pc and s in stored and abs(pc / stored[s] - 1) > 0.001:
            mismatch += 1
    if not all(q.get("prev_close") for q in found.values()):
        print("   WARNING: some quotes have no previous close, so their day change cannot be shown.")
    if not all(q.get("volume") for q in found.values()):
        print("   WARNING: no traded volume in the response, so live Turnover will be empty.")
    if stored:
        print(f"   Previous close vs your stored last close: {len(found) - mismatch} of {len(found)} match. "
              "During market hours nearly all should match (a mismatch is normally a corporate action). If none match when the "
              "market is open, Angel's 'close' is not the previous close and live day change would be wrong: tell the developer.")

    if len(idx) >= 50:
        big = list(idx)[:50]
        t = time.time()
        try:
            found50, miss50 = angel_one.quote_chunk(big)
            print(f"4. Batch of 50 (the largest we send): {len(found50)} quotes in {time.time() - t:.2f}s")
        except Exception as e:  # noqa: BLE001
            print(f"4. Batch of 50 FAILED: {type(e).__name__}: {e}. Lower the batch size in providers/angel_one.py (QUOTE_MAX).")
    n = len(universe) or 2600
    print(f"\nRequests are spaced {angel_one.settings.prices_batch_pause_sec}s apart (PRICES_BATCH_PAUSE_SEC). A full pass over "
          f"{n:,} stocks is about {n // 50 + 1} requests, roughly "
          f"{(n // 50 + 1) * (angel_one.settings.prices_batch_pause_sec + sum(gaps) / len(gaps)):.0f}s. "
          "Check Angel's documented rate limit for the quote endpoint and raise the pause if you hit errors.")


if __name__ == "__main__":
    main()