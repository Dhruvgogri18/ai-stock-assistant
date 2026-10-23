"""Connection + schema for data/sessions.db (sessions, messages, llm_calls, watchlist).

Kept separate from the warehouse (stock.db) so reloading market data can never
touch user conversations.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from backend.app.config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT 'New chat',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,            -- user | assistant
    content     TEXT NOT NULL,
    payload     TEXT,                     -- assistant extras as JSON: blocks, follow_ups, steps, sources, usage...
    feedback    TEXT,                     -- up | down | NULL
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

-- One row per model call. Also mirrored to logs/sessions/<id>.log
CREATE TABLE IF NOT EXISTS llm_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT,
    request_id        TEXT,
    stage             TEXT,
    call_no           INTEGER,
    model             TEXT,
    temperature       REAL,
    started_at        TEXT,
    duration_ms       REAL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    usage_source      TEXT,               -- provider | estimated
    cost_usd          REAL,
    tools_offered     TEXT,               -- JSON list of tool names
    messages          TEXT,               -- JSON: exact messages sent
    response          TEXT,               -- raw completion text
    tool_calls        TEXT,               -- JSON: tool calls the model asked for
    finish_reason     TEXT,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id, id);

-- Saved screeners ("Created by you"). definition is the JSON described in services/screener/catalog.py
CREATE TABLE IF NOT EXISTS screeners (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'custom',   -- custom | chat | curated (a copy of a curated one)
    definition  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Alert rules and the notifications they produce. params/payload are JSON.
CREATE TABLE IF NOT EXISTS alerts (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,                 -- breakout | result
    preset         TEXT NOT NULL DEFAULT '',      -- high_52w | low_52w | ema_cross | level (breakout only)
    symbol         TEXT,                          -- NULL = every stock on the watchlist
    params         TEXT NOT NULL DEFAULT '{}',
    active         INTEGER NOT NULL DEFAULT 1,
    last_eval_date TEXT,                          -- breakout: newest bar date already evaluated
    baseline_id    INTEGER NOT NULL DEFAULT 0,    -- result: only filings with a higher id can fire
    created_at     TEXT NOT NULL,
    last_checked   TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   TEXT,
    kind       TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL UNIQUE,              -- the same event can never notify twice
    created_at TEXT NOT NULL,
    read_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(read_at, id);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY
);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = settings.sessions_path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if path not in _initialised:
        with _init_lock:
            if path not in _initialised:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                _initialised.add(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
