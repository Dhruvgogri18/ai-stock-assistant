# Stock Research Assistant

Ask Indian-stock questions in plain English. The LLM only understands the question and writes the answer;
every figure comes from the data layer (tools) with its source and date.

## Run

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # fill LLM_API_KEY, LLM_MODEL, INDIANAPI_KEY
python -m scripts.check_llm                            # optional: proves the gateway works + writes a log
uvicorn backend.app.main:app --reload                  # http://127.0.0.1:8000
```

No keys yet? `python -m scripts.demo_server` runs the whole UI on a canned model and canned data (port 8001).
Tests (no network needed): `pip install pytest && pytest`.

## Market data: NSE end-of-day (bhavcopy)

Price-based answers (top gainers/losers, most active, quotes, price charts, price screens, 52-week highs) come from
NSE's free daily bhavcopy file, stored in the local database. They use **no API quota** and are **end of day, not live**.

```
python -m scripts.refresh_bhavcopy --days 400     # one-time: load ~1.5 years (needed for 1Y return / 52-week metrics)
uvicorn backend.app.main:app --reload             # the app then keeps itself current
```

- **Refresh:** a background job checks every 30 min. It does not ask for today's file before 18:30 IST, loads any
  missing weekdays, retries a missing day for 7 days and then treats it as an exchange holiday.
  Status: `GET /api/data/status` · force a run: `POST /api/data/refresh` · or cron / Task Scheduler running
  `python -m scripts.refresh_bhavcopy` after 19:00 IST.
- **If NSE blocks scripts:** download `sec_bhavdata_full_DDMMYYYY.csv` in a browser and run
  `python -m scripts.refresh_bhavcopy --file <that file>`.
- **Not in bhavcopy** (still from IndianAPI / yfinance): results, ratios, market cap, P/E, shareholding, news, events.
- Day change uses NSE's own adjusted previous close. 1M/3M/1Y returns and 52-week range come from stored closes/highs/lows
  and are **not** adjusted for splits or bonuses.
- Company names come from NSE's equity master; industries only exist for Nifty 500 + microcap-250 names (others show "—").
- These are public files, not a licensed feed. Fine for a prototype; use a licensed vendor before real users.

## Technical indicators

`technicals` tool + `GET /api/technicals/{symbol}?indicators=ema10,ema50,rsi,macd,bollinger,atr&period=3m`
(same calculation without the LLM, handy for checking numbers). Computed in code (`services/data/technicals.py`),
never by the model: EMA, SMA, RSI (Wilder), MACD (12,26,9), Bollinger (20, 2), ATR, plus crossover/trend signals and
support / resistance (swing levels + pivot points). The UI draws the chart with the overlays and RSI/MACD/ATR panels.

- **Candles:** Angel One daily candles if configured, else stored NSE bars, else any provider (close only; ATR is skipped).
- **Warm-up:** at least 3x the longest period (min 100 bars) is loaded before the window is trimmed, so lines start correctly.
- **Completed candles only:** today's still-forming candle is dropped, so values are "as of the last close".
- **Honesty checks:** holes in the price history and short history are reported in `data_quality.warnings`.
- The model sees a compact view of chart tool results (`tools.llm_view`); the UI gets the full series.

**Gap healing:** the refresh job now also fills missing weekdays *inside* the stored range (for example after an
interrupted `--days 400` backfill), not only after the newest stored date.

## Structure (mirrors the `api.zip` layering)

```
backend/app/
  main.py                      app + routers + static frontend (no logic)
  config/settings.py           all env settings (absolute .env path)
  routers/                     session_router (chat, status, LLM logs) · research_router (direct data, watchlist) · screener_router · alerts_router (alerts, notifications, calendar, watchlist view)
  schemas/                     pydantic request/response models
  repositories/                session_repository · llm_call_repository · watchlist_repository · screener_repository · alert_repository · warehouse (market data)
  services/
    llm_trace.py               session-scoped LLM logging (see below)
    llm/gateway_client.py      the ONLY place the model is called; every call is logged here
    orchestration/             LangGraph: state · nodes · graph · router · tools · prompts · service
    data/                      service (providers + fallback chain) · bhavcopy (download/schedule) · market_data (movers, quote, history)
                               · rag · providers/{db,indian_api,yfinance}
    screener/                  catalog (metrics + curated screeners) · engine (run a definition over all NSE stocks)
    alerts/                    rules (breakout presets, results classifier; pure) · engine (create, check_all, status, background loop)
    events/                    calendar (confirmed warehouse events + labelled provider estimates)
frontend/                      index.html · styles.css · app.js · screener.js/.css · workspace.js/.css (watchlist, calendar, alerts) (no build step)
tests/                         fake-gateway tests of the whole turn + logging
```

## The turn graph (`services/orchestration/graph.py`)

```
START → load_context → route_turn ─┬→ research ────────┐
                                   └→ out_of_scope ────┴→ finalize → persist → END
```

- **load_context** loads prior turns from the session DB (the DB is the source of truth; no checkpointer).
- **route_turn** is one small LLM call (`LLM_FAST_MODEL`) returning `research | out_of_scope`, a chat title and a
  topic for the live status line. If it fails, the turn falls back to `research`.
- **research** is the tool-calling loop (up to `MAX_TOOL_ROUNDS`, parallel tool execution). If the budget is spent
  it forces a final answer rather than failing.
- **finalize** strips the `<follow_ups>` trailer from the answer and builds sources + usage.
- **persist** writes user + assistant messages (with blocks, steps, sources, usage) and the title.

Add Phase 2/3 by adding routes and nodes (e.g. `event_radar`, `fno`) and one edge in `graph.py`.

## API

| | |
|---|---|
| `POST /api/session` · `GET /api/sessions` · `GET/PATCH/DELETE /api/session/{id}` | create, list, open, rename, delete |
| `POST /api/session/{id}/message` | run a turn → assistant message + follow-ups + steps + sources |
| `GET /api/session/{id}/status` | live label while a turn runs ("Pulling ratios · TCS…"); poll every ~1 s |
| `POST /api/session/{id}/feedback` | 👍 / 👎 on a message |
| `GET /api/session/{id}/llm-calls` | every model call: stage, exact messages sent, raw reply, tool calls, tokens, ms, error |
| `GET /api/session/{id}/usage` | token / latency / error totals, per stage |
| `GET /api/session/{id}/log` | human-readable transcript |
| `/api/results/{s}` `/ratios/{s}` `/peers/{s}` `/screener` `/watchlist` … | direct data endpoints |
| `GET /api/screeners/metrics` | every column/filter the Refine panel offers, with how many stocks have a value |
| `GET /api/screeners/curated` · `GET /api/screeners/mine` | cards for the two tabs (with match count + first symbols) |
| `GET /api/screeners/{id}?limit&offset` | a curated (`c-…`) or saved screener: definition + first page of results |
| `POST /api/screeners/run` | run an unsaved definition `{definition, limit, offset}` (server-side sort and paging) |
| `POST /api/screeners` · `PATCH/DELETE /api/screeners/{id}` · `POST /api/screeners/{id}/duplicate` | save, edit, delete, copy (curated ones are read-only) |
| `POST /api/screeners/from-tool` | save a chat `market_movers` / `screener` result as a screener |
| `GET /api/alerts/presets` · `GET/POST /api/alerts` · `PATCH/DELETE /api/alerts/{id}` | breakout and results alerts (rule + live status) |
| `POST /api/alerts/check` | run every active alert against stored data now, plus one live reading if the market is open |
| `GET /api/alerts/live-status` | live-checking state: on / closed / unconfigured / off, and the last poll |
| `GET /api/notifications` · `/unread-count` · `POST /read` · `DELETE /{id}` | the alert inbox |
| `GET /api/calendar?days=&symbols=` | upcoming results / dividend dates (defaults to the watchlist) |
| `GET /api/watchlist/overview` · `GET /api/symbols/search?q=` | watchlist table (live or end-of-day price per row); symbol typeahead |
| `GET /api/prices/status` | where prices come from now: live / eod / failing / unconfigured, coverage, backoff |

## Live prices (Angel One first, bhavcopy as the fallback)

`services/data/live_prices.py` lays Angel One quotes over the stored end-of-day snapshot, so the screener, movers, the chat's
screener/movers tools and the watchlist all show the same prices, and every result says which source it used (`price_mode`:
`live` / `eod`, with the time).

- **When live applies:** weekdays from 09:15 IST until that day's bhavcopy is stored (after that bhavcopy is final and is used,
  labelled end of day). Before 09:15, weekends, Angel not configured, `PRICES_LIVE=false`, or Angel failing: bhavcopy, with a note saying why.
- **Watchlist:** fetches its own symbols (50 per request), cached `PRICES_TTL_SEC` (10 s), page refreshes itself every 15 s while open.
  Each row says live or EOD.
- **Screener / movers (whole market):** a background refresher walks all ~2,600 stocks (50 per request, stalest first) but only while
  someone has used a screen in the last `PRICES_DEMAND_WINDOW_SEC` (10 min). Screens switch to live only when at least
  `PRICES_MIN_COVERAGE` (95%) of stocks have a quote younger than `PRICES_MAX_AGE_SEC` (180 s); until then they show end-of-day
  prices with "live prices are loading (n of N)" and the page re-checks by itself. A stock with no live quote drops out of price filters
  rather than showing yesterday's value next to today's.
- **Different while live:** day change is vs Angel's own previous close; Turnover is value traded so far (volume x average price);
  Delivery % is empty (published only after the close); returns use today's price against stored closes.
- **Rate limits:** one Angel request at a time, at least `PRICES_BATCH_PAUSE_SEC` (1.1 s) apart, shared by everything. After a failure
  with no data it backs off 1 to 15 minutes. A full pass is about 52 requests, roughly a minute.
- **Unverified assumption:** the batch quote endpoint (50 tokens per request, `close` = previous close) comes from the SmartAPI docs and was
  not tested against a live account. Run `python -m scripts.check_angel_quotes` once with your credentials to check it.
- The chat's single-stock `quote`, `history` and `technicals` tools already tried Angel first; touch alerts use Angel's per-symbol LTP.

## Alerts, calendar and watchlist

Pages: `#/watchlist`, `#/calendar`, `#/alerts` (inbox + rules).

- **Breakout alerts** (`services/alerts/rules.py`): close above/below the 52-week high/low, EMA crossover (default 20/50), price level.
  They evaluate stored end-of-day bars, only for sessions *after* the alert was created, and never back-fill history. Prices are not
  split-adjusted, so a result that sits next to a one-day move above 35% carries a caveat.
- **Trigger: close or touch.** Level and 52-week alerts can be judged on the *close* (default) or when the price *touches* the level.
  Touch alerts use live Angel One prices during market hours (09:15-15:30 IST, `services/alerts/live.py`), polled every
  `ALERTS_LIVE_INTERVAL_SEC` seconds (default 60, `0` = no automatic polling; "Check now" still takes one reading). A crossing is judged
  against the last price seen today, or the previous stored close on the first look, so a level crossed on an earlier day never fires and
  a restart neither misses nor repeats a crossing. Notifications say "Live price from Angel One at HH:MM IST" and that it can reverse
  before the close. If Angel One is not configured (or fails) a touch alert is still judged after the close from the day's high/low,
  using the same dedupe key, so it is never lost and never doubled. Repeated total failures back off (1 to 15 minutes) so wrong
  credentials cannot hammer the broker login. Moving-average crossovers are close-only. Per-poll price calls are one symbol at a time
  (`ALERTS_LIVE_PAUSE_SEC`, `ALERTS_LIVE_MAX_SYMBOLS`); check Angel's rate limits before raising them.
- **Result alerts**: fire when a filing for a watched stock looks like an *outcome* of results (title heuristic; board-meeting
  intimations and `demo-seed` sample rows are ignored). Duplicate rows from re-running the loader notify once.
  Filings only exist after `python -m scripts.load_announcements` (run it on a schedule).
- **Checking**: a background loop runs every `ALERTS_CHECK_INTERVAL_MIN` minutes (default 5, `0` = off) and the Alerts page has "Check now".
  Every notification has a dedupe key, so repeated checks never notify twice.
- **Calendar**: confirmed dates come from the warehouse `events` table (`warehouse.insert_event`); anything else is a provider
  (yfinance) projection and is labelled *Estimated*. Stocks with no date are listed, not hidden.

## Screener page

`#/screener` is the home (Curated / Created by you tabs); `#/screener/<id>` is one screener with sortable, paged results,
filter chips and a **Refine Screener** panel to add/remove columns. Saved screeners live in `data/sessions.db`
(`screeners` table) and autosave on every change; curated ones are defined in `services/screener/catalog.py`.
A screener is a JSON definition (`columns`, `filters`, `sort`, `universe`). Missing values are shown as a dash and
never counted as zero; a stock without a value for a filtered metric is left out, and the page says how many stocks
have that metric. Prices are end of day. Fundamentals and shareholding only cover what the warehouse has loaded.

## Where the logs are

Everything the model sees and says is recorded per chat session, three ways:

1. **`logs/sessions/<session_id>.log`** transcript: REQUEST START → STAGE → **LLM CALL** (full prompt) → **LLM RESPONSE**
   (completion or tool calls, tokens, ms) → **TOOL CALL** (args, ms, source, provider, errors) → REQUEST END.
2. **`logs/index.log`** one line per event across all sessions (`grep session=ab12cd34`).
3. **SQLite table `llm_calls`** (`data/sessions.db`), one row per call, served by `/llm-calls` and `/usage`.

Tokens come from the gateway's `usage` (`usage_source=provider`); if it returns none they are estimated and marked
`estimated`. Failed calls are logged too. Deleting a chat keeps its logs (audit trail).
Set `LLM_LOG_PROMPTS=false` to log metadata only. The UI shows the totals under "Thinking completed" with a
"View LLM log" link.

## Not built yet

- **Index levels** (Nifty/Sensex/Bank Nifty "market snapshot") and live intraday prices: bhavcopy has stocks only, end of day.
- **Market cap / P/E columns in the movers grid**: not in bhavcopy.
- Markets / Concalls pages (sidebar items say so).
- Set Alert (Phase 2), file attachments, token streaming (answers appear when complete; progress is shown via status).
- Auth: there is one implicit user. `USER_NAME` only changes the greeting.
- The brand name/logo are placeholders (`APP_NAME`), not Fuzz's.