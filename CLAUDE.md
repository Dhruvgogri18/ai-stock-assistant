# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Mission

Build an India-first AI equity research assistant (NSE/BSE) that is more trustworthy, deeper and more proactive than askfuzz.ai.

Public reference points for the competitor (from their own posts; verify before using in product copy): finance-specific
"decimal-level accuracy", India-hosted models, a panel of "experts", pre/post-market voice briefings, and a Dhan
broker integration.

Where we win:

1. **Provenance on every number.** Each figure shown to the user traces to a tool result with `source` and `as_of`.
2. **Grounding check.** An automated pass flags numbers in an answer that do not appear in tool data (not built yet).
3. **Data depth.** Live prices and indices, split-adjusted history, filings and concall search, F&O (partly built).
4. **Proactive, not just reactive.** Alerts, daily briefings, watchlist and portfolio awareness (not built yet).
5. **Auditability.** Every model call, tool call, token count and latency is already logged per session. Keep it that way.

## Run and verify

Run commands from the repository root (imports use `backend.app` and `scripts` as packages).

```bash
python -m venv .venv
# Activate .venv, then:
pip install -r requirements.txt
pip install pytest                         # pytest is not in requirements.txt
cp .env.example .env                       # configure the gateway and optional data providers
uvicorn backend.app.main:app --reload      # UI/API at http://127.0.0.1:8000
python -m scripts.demo_server              # offline canned model/data UI on port 8001
python -m pytest                           # tests use fake gateway/data; no network needed
python -m pytest tests/test_chat_flow.py   # one test module
python -m pytest tests/test_chat_flow.py::test_full_turn_persists_logs_and_follow_ups  # one test
```

`python -m scripts.check_llm` checks a configured gateway. For market data, `python -m scripts.refresh_bhavcopy --days 400`
backfills enough EOD history for 1Y/52-week figures; `--file <csv>` imports a manually downloaded bhavcopy.
`python -m scripts.load_all` runs bhavcopy, fundamentals, and announcements loaders in sequence (network/data-changing
operation, so ask before running it). There is no frontend build pipeline or repository-configured lint command.

## Architecture

- `backend/app/main.py` creates the FastAPI app, initializes the warehouse, starts optional bhavcopy refresh, registers the
  active `routers/`, and serves the static `frontend/` (`index.html`, `app.js`, `styles.css`). The browser keeps chat state
  on the server, polls session status during turns, and renders returned tool blocks; answers are not token-streamed.
- Chat requests enter `routers/session_router.py` → `services/orchestration/service.py` → the LangGraph in `graph.py`:
  `load_context → route_turn → (research | out_of_scope) → finalize → persist`. The research node runs model-selected tools
  (bounded by `MAX_TOOL_ROUNDS`, run in parallel per round). The session database, not a LangGraph checkpointer, owns
  conversation history. `nodes.py`, `tools.py`, and `prompts.py` implement the stages, dispatch, and model instructions;
  finalization removes the `<follow_ups>` trailer and assembles sources/usage.
- `services/llm/gateway_client.py` is the only place the model is called (OpenAI-compatible gateway).
  `services/llm_trace.py` logs prompts, replies, tool activity, usage, and errors to `logs/sessions/`, `logs/index.log`,
  and the `llm_calls` table in the session DB. Threaded graph execution rebinds trace context explicitly
  (`contextvars.copy_context().run`), so keep doing that for any new thread pool.
- Both chat tools and direct endpoints in `routers/research_router.py` use `services/data/service.py`. Fundamentals prefer
  local warehouse data, then IndianAPI, then yfinance in auto mode; filing search uses SQLite FTS5 via `services/data/rag.py`.
  Prices, movers, charts, and price-based screens primarily use locally stored NSE bhavcopy bars through
  `services/data/market_data.py`; optional Angel One integration supplies live LTP/history.
- `services/data/bhavcopy.py` downloads/parses NSE daily files and schedules catch-up refresh;
  `repositories/bars_repository.py` stores EOD bars and refresh state; `routers/market_router.py` exposes market
  endpoints/status. Bhavcopy prices are end-of-day, not live; longer returns and 52-week ranges are not split/bonus-adjusted.
- Storage is split: `stock.db` (or configured warehouse) holds market/fundamental/filing data through
  `repositories/warehouse.py`; `data/sessions.db` holds chats, LLM calls, and watchlist via session repositories.
  `config/settings.py` resolves `.env` and relative data/log paths from the repository root. Tests redirect both databases
  to temporary locations and replace the gateway in `tests/conftest.py`.

Prefer the active layered router → service → repository paths. Keep routers thin and keep logic out of `main.py`.

## Non-negotiable rules

1. **Numbers come from tools, never from the model.** If a metric is missing, the answer says "Not available". Do not add
   code or prompt text that lets the model fill figures from memory.
2. **Keep provenance with every tool result.** Each tool returns `source`, `as_of` and `provider`. `finalize` builds the
   UI sources list from them. A new tool without these fields is incomplete.
3. **No buy/sell recommendations or price targets** from the assistant. This is a research tool, so keep the disclaimer
   posture (SEBI-regulated territory; get legal review before adding anything that looks like advice).
4. **State data freshness honestly.** Bhavcopy is end-of-day. Do not describe EOD data as live, and do not call Angel One
   data end-of-day. The prompt already reads `source` to decide this.
5. **Never read, print, log or commit `.env`.** It holds real credentials (LLM key, IndianAPI key, Angel One client ID,
   password and TOTP secret). Use `.env.example` for names and defaults. Never paste secrets into tests, fixtures or logs.
6. **Tests never touch the network or real databases.** Use the fixtures in `tests/conftest.py` (`FakeGateway`, temp DBs,
   patched `tools.run_tool`).
7. **Do not run data-changing or network scripts** (`load_all`, `refresh_bhavcopy`, `load_*`) unless asked.

## How to extend

**Add a tool** (all steps required):

1. Implement the function in `services/data/service.py` (or `market_data.py` for price data). Return a dict with
   `source`, `as_of`, `provider`.
2. Register its schema in `TOOLS` in `services/orchestration/tools.py`. `ALLOWED` is derived from `TOOLS`, and dispatch is
   `getattr(data, name)`, so the function name must match the tool name.
3. Add a verb in `_VERB` (live status line text).
4. If the UI should render a table or chart for it, add the block renderer in `frontend/app.js` and a line under
   "HOW THE UI WORKS" in `prompts.py`.
5. Add a test in `tests/` using the fake gateway scripting (`env.fake.say(...)`).

**Add a graph node or route:** add the node in `nodes.py`, wire it in `graph.py` (`add_node` + edges), extend `RouteName`
and the router prompt if it is a new route, and add the state keys to `state.py`. Wrap the node with `@traced("name")`.

**Change prompts:** edit `prompts.py` only. Keep the format contract (title, blockquote takeaway, tables, `<follow_ups>`
trailer) in sync with `split_follow_ups` in `nodes.py` and with the frontend renderer.

**Change the model call boundary:** only in `services/llm/gateway_client.py`, and keep tracing intact.

## Known debt (check before touching)

- Legacy parallel implementation still in the tree: `backend/app/{api,chat,data,db,llm,rag,watchlist,session_log}.py`,
  top-level `backend/app/providers/`, and `backend/app/config.py` (shadowed by the `config/` package). `main.py` does not
  register them.
- `services/data/service.py` still imports `backend.app.db` (around line 116). Inspect call sites before removing `db.py`.
- `backend/app/providers/indian_api.py` and `services/data/providers/indian_api.py` have diverged. The active copy is
  under `services/data/providers/`.
- Auth: one implicit user. `CORS_ALLOW_ORIGINS=*` is a development default.
- No lint/format config and no CI. `pytest` is missing from `requirements.txt`.
- Repo hygiene: `.venv/`, `logs/`, `data/` and `demo_data/` exist in the working tree. Keep them out of commits and archives.

## Roadmap

The detailed work order lives in `ROADMAP.md` (milestones, order, acceptance criteria). Read it only when asked to work on
the roadmap. Progress is tracked in `PROGRESS.md`. Summary of phases:

- **A: trust and quality** (cleanup, grounding verifier, eval harness, streaming)
- **B: data depth** (index levels, split-adjusted prices, filings/concall retrieval, richer movers grid, F&O)
- **C: product** (alerts, watchlist/portfolio, briefings, expert routes), not started
- **D: platform** (auth, Postgres, deployment, CI, compliance copy), not started

## Working agreement

- Make the smallest change that satisfies the request; do not refactor unrelated modules.
- Before editing, read the call sites. Before finishing, run `python -m pytest` and report the result.
- When a change affects behavior described in `README.md` (API, env vars, data freshness), update the README in the same change.
- If a task needs a network call, a data load or a credential, say so and ask first.
- When running a multi-milestone plan from `ROADMAP.md`, commit once per milestone and keep `PROGRESS.md` current so work can resume in a fresh session.