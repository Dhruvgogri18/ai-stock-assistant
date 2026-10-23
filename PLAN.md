# Remaining ROADMAP milestones

## Baseline
- No PROGRESS.md exists.
- `git log --oneline` cannot be verified: working tree has no `.git` metadata.
- Existing implementation proves core chat/audit/provenance, bhavcopy/EOD market data, technicals, sessions/API, and basic FTS filing search are present.
- ROADMAP order is A1 → A2 → A3 → B2 → B1 → A4 → B3 → B4 → B5.
- A1, A2, A3, B2, B1, A4, B4, and B5 remain; B3 is partial. No milestone can be called complete under ROADMAP acceptance rules without pytest/commit evidence.

## A1 — Clean-up and hygiene
- Files: delete legacy `backend/app/{api,chat,data,db,llm,rag,watchlist,session_log}.py`, top-level `backend/app/providers/`, and `backend/app/config.py`; move/adapt Angel One provider under `backend/app/services/data/providers/`; edit `services/data/service.py`, imports, README; create `requirements-dev.txt`, `pyproject.toml`, `PROGRESS.md`.
- Approach: grep active imports first; migrate remaining `backend.app.db`/legacy provider call sites to warehouse/repository paths; verify only active layered modules are imported; keep behavior unchanged.
- Tests: run baseline and full pytest; preserve/increase test count; add import/startup smoke coverage if needed; verify demo server offline.
- Risks: hidden legacy imports; provider divergence; deleting modules may break tests or import-time behavior.
- Need: no credentials/network; pytest installation may be needed. Git commit is required by ROADMAP but repository metadata is absent here.

## A2 — Grounding verifier
- Files: create `services/orchestration/grounding.py`; edit `state.py`, `nodes.py`, `graph.py`, `prompts.py`, `frontend/app.js`, persistence/message payload code; add tests.
- Approach: extract non-date/non-rank numeric claims; flatten tool blocks; tolerance/unit-scale matching; add traced verify node and one constrained regeneration pass; persist status/unmatched list and render badge.
- Tests: exact, rounded, unit-scaled matches; regeneration on hallucination; warning after second failure; ignore years/dates/follow-ups.
- Risks: numeric parsing false positives; regeneration must preserve format/follow-ups; trace and persistence compatibility.
- Need: no credentials/network.

## A3 — Evaluation harness
- Files: create `tests/evals/cases.json`, `tests/test_evals.py`, `scripts/run_evals.py`; create/update `PROGRESS.md`.
- Approach: define ≥40 Indian-market cases with expected tools/args/facts; script FakeGateway graph runs and asserts sources, blocks, follow-ups, no advice language; real-gateway runner only writes `logs/evals/` and is not run.
- Tests: deterministic no-network suite under 30 seconds; include all ROADMAP domains and out-of-scope behavior.
- Risks: scripted responses becoming brittle; test duration and fixture coverage; avoid secrets/logging sensitive data.
- Need: no credentials for tests; human must later configure/run real gateway command.

## B2 — Corporate-action-adjusted prices
- Files: `repositories/warehouse.py`; create `scripts/load_corporate_actions.py` and fixture; edit `services/data/market_data.py`, `prompts.py`, README; add tests.
- Approach: store actions and cumulative factors; adjust history, returns, and 52-week range; return `adjusted`/`adjustment_note`; preserve explicitly labeled raw fallback.
- Tests: split and bonus continuity; no-action fallback labeled unadjusted; loader fixture parsing.
- Risks: factor direction/date boundaries; existing raw bars and current API compatibility; do not claim adjustment without loaded data.
- Need: no network; human may later run the loader with an NSE CSV.

## B1 — Index levels / market snapshot
- Files: create index repository/loader as needed; edit `services/data/service.py`, `services/orchestration/tools.py`, `prompts.py`, `frontend/app.js`, `routers/market_router.py` only if direct route needed; add fixture/tests.
- Approach: `index_snapshot` with Angel One live when configured, otherwise warehouse NSE EOD fixture; include level/change/% change, source/as_of/provider and live/EOD label; graceful unavailable result.
- Tests: mocked live path, EOD fallback, unavailable path, renderer/tool contract.
- Risks: unverified NSE file format; provider API shape and freshness wording; avoid network during development.
- Need: no credentials/network now; Angel One credentials only for later human live verification.

## A4 — Token streaming
- Files: `services/llm/gateway_client.py`, `routers/session_router.py`, orchestration service/graph as needed, `frontend/app.js`; add SSE tests.
- Approach: stream final answer only; retain existing tool/status flow and endpoint; emit ordered status/token/done/error events; suppress `<follow_ups>` until final done payload, then render blocks/sources/badge.
- Tests: ordering, trailer suppression, mid-stream error, unchanged non-stream endpoint.
- Risks: gateway compatibility; disconnect/cancellation; trace usage/latency and persistence must remain complete.
- Need: no credentials/network; real streaming gateway verification later.

## B3 — Filings and concall retrieval (partial)
- Files: `services/data/rag.py`, warehouse schema/repository, create local concall loader/fixture; update tool/prompt/UI citation fields; add tests.
- Approach: chunk indexed documents with metadata and stable chunk IDs; always FTS5, optional embedding branch only with `EMBEDDING_API_KEY`; local PDF/text ingestion only; return title/date/symbol/chunk ID citations.
- Tests: chunking, FTS-only without key, fake-embedder hybrid ranking, concall fixture parsing, citation contract.
- Risks: schema migration and deterministic chunk IDs; optional dependency/key fallback; PDF parser behavior.
- Need: no network; embedding key only for later optional integration verification.

## B4 — Movers grid columns
- Files: `services/data/service.py`/`market_data.py`, warehouse fundamentals join path, `frontend/app.js`; tests.
- Approach: join market cap, P/E, sector when available for movers/screener; use `—` for missing, never zero; preserve sortable arbitrary-column grid and ordering.
- Tests: present join, missing join, ordering unchanged, frontend payload/renderer contract.
- Risks: symbol normalization and stale fundamentals; join must not alter price filtering or ordering.
- Need: no credentials/network.

## B5 — F&O snapshot (stretch)
- Files: create F&O warehouse/loader/tool fixture and tests; edit `services/orchestration/tools.py`, data service, prompts/UI/router only if loop cannot handle it; update PROGRESS.
- Approach: fixture-backed NSE EOD loader; return open interest, change in OI, PCR with provenance; implement only if source format is verifiable from fixture, otherwise log blocked verification per roadmap.
- Tests: loader parsing, tool provenance, unavailable fallback, model/tool routing if a route is added.
- Risks: source format and instrument semantics; roadmap explicitly permits skip/log when unusable.
- Need: no network now; human must verify NSE F&O source/file before production loader use.

## ROADMAP conflicts with actual code
- ROADMAP assumes a Git repository and per-milestone commits, but current directory has no `.git`; commit status/history cannot be established.
- ROADMAP requires PROGRESS.md as resume point, but it is absent.
- A1 says confirm nothing active imports legacy modules before deletion; current active `services/data/service.py` still imports `backend.app.db` per CLAUDE/delegated inspection, so cleanup is not complete.
- B3 labels filing/concall retrieval as a future extension, but basic FTS5 filing search already exists; scope should be migration/enrichment, not greenfield search.
- B4 says add sector, but sector is already rendered/partly present; missing work is fundamentals joins for market cap/P/E and complete result rows.
- Core provenance/auditability, bhavcopy/EOD data, technicals, session APIs, and basic filing search are already implemented although the roadmap does not mark them as separate completed milestones.
- ROADMAP’s “all milestones in order” rule conflicts with the repository’s already-partial B3/B4 state only in sequencing, not implementation; finish remaining work in the prescribed order.
- pytest is required by ROADMAP but is not installed in the current environment, so acceptance cannot currently be verified.
