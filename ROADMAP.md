# ROADMAP.md: Phase A + Phase B build plan

Read `CLAUDE.md` first. This file is the work order for Phases A (trust and quality) and B (data depth).
Phases C and D are out of scope for this run.

## How to run this plan

1. Create `PROGRESS.md` at the repo root. It is the resume point if the session ends. Keep it current: milestone status,
   files touched, decisions made, commands the human must run, blockers.
2. Work through the milestones **in the order below**, not in the order of the section headings.
3. A milestone is done only when ALL of these hold:
   - `python -m pytest` passes (run it before starting and after finishing; the starting result is the baseline),
   - new behavior has tests that use the fake gateway/data (no network, no real DBs),
   - the milestone's acceptance criteria are met,
   - `README.md` is updated if API, env vars or data freshness changed,
   - one git commit with the message `<id>: <summary>`.
4. Do not stop between milestones to ask for confirmation. Stop and ask only for a **blocker**: a credential, a network
   call, a paid data source, or a design choice that changes the public API.
5. **Never run network or data-changing scripts** (`load_all`, `refresh_bhavcopy`, `load_*`, `check_llm`). Write the loader,
   test it against a small fixture file in `tests/fixtures/`, and list the exact command for the human in `PROGRESS.md`.
6. Keep every new capability behind a graceful fallback. Missing keys or missing data must produce a clear "not
   available" result, never a crash.
7. If a milestone turns out to be larger than expected, deliver a working, tested slice and note the remainder in
   `PROGRESS.md` instead of leaving it half-wired.

## Order of work

`A1 → A2 → A3 → B2 → B1 → A4 → B3 → B4 → B5`

---

## A1: Clean-up and hygiene (do first)

- Confirm with grep that nothing active imports the legacy modules, then delete:
  `backend/app/{api,chat,data,db,llm,rag,watchlist,session_log}.py`, the top-level `backend/app/providers/` directory
  (keep `angel_one.py` by moving it under `services/data/providers/`), and `backend/app/config.py`.
- Fix `services/data/service.py` (~line 116), which imports `backend.app.db`. Replace it with the warehouse/repository path.
- Update every import and any README paths that point at moved files.
- Add `requirements-dev.txt` (`-r requirements.txt` + `pytest`), and minimal `ruff` config in `pyproject.toml`.
- Acceptance: app starts (`uvicorn backend.app.main:app`), demo server still works, test count does not drop.

## A2: Grounding verifier

Goal: every number in an answer is traceable to tool data, or the user is told it is not.

- New `services/orchestration/grounding.py`:
  - extract numeric claims from the answer (amounts, percentages, ratios, Cr/lakh values), ignoring years, dates, list
    numbering, rank numbers and numbers inside follow-ups,
  - flatten numeric values from all tool `blocks` data, and match with tolerance for rounding and unit scaling
    (₹ Cr, lakh, %, x),
  - return `{status: "ok" | "partial" | "failed", checked: int, unmatched: [..]}`.
- New graph node `verify` between `research` and `finalize` (wire in `graph.py`, add state keys in `state.py`, wrap with
  `@traced("verify")`).
- Behavior: if unmatched numbers exist, do one regeneration pass with an instruction to use only tool data. If still
  unmatched, keep the answer and attach the unmatched list.
- Persist the result in the message payload (`persist` node) and add a `steps` entry. Show a small badge in
  `frontend/app.js` ("All figures verified" / "N figures could not be verified").
- Tests: matched answer, rounded match, unit-scaled match, hallucinated figure triggers regeneration, still-bad figure
  shows warning, years/dates are not flagged.

## A3: Evaluation harness

- `tests/evals/cases.json`: at least 40 Indian-market questions across results, ratios, peers, ownership, movers,
  screener, history, filings, jargon, out-of-scope. Each case has the expected tool names/args and required facts.
- `tests/test_evals.py`: runs each case through the graph with the scripted fake gateway and asserts tool calls, blocks,
  sources, no buy/sell language, and follow-ups present.
- `scripts/run_evals.py`: same cases against the real gateway, writes a JSON report to `logs/evals/`. Do not run it;
  document the command in `PROGRESS.md`.
- Acceptance: eval tests run in under 30 seconds with no network.

## B2: Corporate-action-adjusted prices

- Warehouse table `corporate_actions` (symbol, ex_date, type, ratio, adjustment_factor) in `repositories/warehouse.py`
  and a loader `scripts/load_corporate_actions.py` (NSE corporate actions CSV; parse from a fixture in tests).
- Apply cumulative adjustment factors in `services/data/market_data.py` for history, 1M/3M/1Y returns and 52-week
  range. Add `adjusted: true/false` and `adjustment_note` to tool results.
- If no corporate-action data is loaded, behavior is unchanged and the result states that figures are unadjusted.
- Update the prompt note in `prompts.py` and the README caveat accordingly.
- Tests: 1:2 split and a bonus produce continuous series; unadjusted fallback is labeled.

## B1: Index levels (market snapshot)

- New tool `index_snapshot` (Nifty 50, Sensex, Bank Nifty, plus Nifty Next 50/Midcap if the source provides them):
  level, change, % change, `as_of`, `source`.
- Source order: Angel One live (only if `angel_one.configured()`), else NSE end-of-day index closing file stored in the
  warehouse via a new loader (verify the file format from a fixture; do not assume URLs work).
- Register in `tools.py`, add `_VERB`, add a `market_snapshot` block renderer in `frontend/app.js`, add a line in the
  prompt's "HOW THE UI WORKS".
- The result must say clearly whether it is live or end-of-day.
- Tests: live path (mocked), EOD fallback, not-available path.

## A4: Token streaming

- Stream only the final answer. Tool rounds keep using the existing status line.
- Add `services/llm/gateway_client.py` streaming support and `POST /api/session/{id}/message/stream` (SSE events:
  `status`, `token`, `done`, `error`). Keep the existing non-streaming endpoint working unchanged.
- The `<follow_ups>` trailer must never be shown to the user while streaming; strip it before display.
- Frontend renders tokens incrementally, then swaps in final blocks/sources/verification badge on `done`.
- Tests: stream ordering, trailer suppression, error mid-stream, existing endpoint unchanged.

## B3: Filings and concall retrieval

- Extend `services/data/rag.py`: chunk documents, store chunks with metadata (symbol, doc title, date, type).
- Hybrid retrieval: FTS5 always; vector similarity only when `EMBEDDING_API_KEY` is set. Without a key, FTS5 only.
- `search_filings` returns snippets with doc title, date, symbol and a stable chunk id; the answer cites title and date.
- Add a loader path for concall transcripts from local PDF/text files in `data/` (no scraping). Parse from a small fixture
  in tests.
- Tests: chunking, FTS5-only retrieval, hybrid ranking with a fake embedder, citation fields present.

## B4: Movers grid columns

- Add market cap, P/E and sector to the `market_movers` and `screener` result rows by joining warehouse fundamentals
  where available. Missing values render as "—", never as zero.
- Update the grid renderer in `frontend/app.js`. Keep results sortable.
- Tests: join present, join missing, ordering unchanged.

## B5: F&O snapshot (stretch; skip and log if no usable source)

- New tool `fno_snapshot` (open interest, change in OI, put-call ratio) from the NSE F&O end-of-day file, with its
  own loader and fixture-based tests. Add a router branch and `fno` node only if the single research loop cannot handle it.
- If the source cannot be verified without network access, implement the loader and tool against the fixture and record
  the verification step for the human in `PROGRESS.md`.

---

## Out of scope for this run

- Licensed data vendor integration (needs a commercial decision). If useful, define a small provider interface only.
- Alerts, portfolio, briefings, auth, Postgres migration, deployment (Phases C and D).
- Any change to `.env`, credentials or the demo databases.