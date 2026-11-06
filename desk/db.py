"""SQLite in WAL mode. One connection, used from the single asyncio loop."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DESK_DATA", ROOT / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, pool TEXT, migrated_at REAL,
  creator TEXT, creator_balance_at_migration TEXT, holders INTEGER,
  signature TEXT, image TEXT, state TEXT DEFAULT 'waiting', first_seen REAL,
  liq_at_scan REAL, price_at_analyst REAL, h24_max REAL, decimals INTEGER, token_program TEXT
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY, mint TEXT, ts REAL, liquidity_usd REAL, top10_pct REAL,
  h1_change_pct REAL, age_min REAL, verdict TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY, mint TEXT, ts REAL, layer INTEGER, name TEXT, value_text TEXT,
  pass INTEGER, source TEXT, run_id TEXT, ms INTEGER
);
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY, mint TEXT, ts REAL, alive_6h REAL, organic REAL, flush REAL,
  chase REAL, score INTEGER, pass INTEGER, reason TEXT, input_tokens INTEGER, model TEXT
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, mint TEXT, ts REAL, price REAL, size_usd REAL, stack_pct REAL,
  link TEXT, mode TEXT, status TEXT, reason TEXT, score INTEGER
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY, mint TEXT, opened_at REAL, entry_price REAL, size_usd REAL,
  peak_price REAL, stop_price REAL, trail_active INTEGER DEFAULT 0, closed_at REAL,
  exit_price REAL, exit_reason TEXT, pnl_usd REAL, fees_usd REAL DEFAULT 0, mode TEXT,
  name TEXT, symbol TEXT, stop_pct REAL, status TEXT DEFAULT 'open', tokens_raw TEXT,
  decimals INTEGER, buy_sig TEXT, sell_sig TEXT, last_price REAL, score INTEGER,
  exit_started REAL, no_price_polls INTEGER DEFAULT 0, venue TEXT
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, mint TEXT, agent TEXT, title TEXT,
  subtitle TEXT, value TEXT, value2 TEXT, ref_id INTEGER
);
CREATE TABLE IF NOT EXISTS prices (mint TEXT, ts REAL, price REAL, volume_m5 REAL);
CREATE INDEX IF NOT EXISTS prices_mint_ts ON prices(mint, ts);
CREATE TABLE IF NOT EXISTS balance (ts REAL, value REAL, mode TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS change_log (ts REAL, key TEXT, old TEXT, new TEXT, who TEXT);
CREATE TABLE IF NOT EXISTS skips (mint TEXT PRIMARY KEY, until REAL);
CREATE TABLE IF NOT EXISTS heartbeats (agent TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT, auth TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY, ts REAL, title TEXT, body TEXT, url TEXT, mint TEXT, read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, usd REAL, note TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, ts REAL, mode TEXT, night_start REAL, path TEXT
);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS followups (mint TEXT, ts REAL, price REAL, liq REAL, mcap REAL, volume_m5 REAL);
CREATE INDEX IF NOT EXISTS followups_mint_ts ON followups(mint, ts);
"""

# Columns added after the first release; connect() adds any that an older database is missing.
MIGRATIONS = [
    ("scans", "snapshot", "TEXT"),      # DexScreener numbers Scanner saw when it decided
    ("verdicts", "snapshot", "TEXT"),   # DexScreener numbers Analyst saw when it scored
]

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    global _conn
    if path is None:
        DATA.mkdir(parents=True, exist_ok=True)
        path = DATA / "desk.db"
    _conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return _conn


def conn() -> sqlite3.Connection:
    if _conn is None:
        connect()
    return _conn  # type: ignore[return-value]


def execute(sql: str, params: tuple | list = ()) -> int:
    with _lock:
        cur = conn().execute(sql, params)
        return cur.lastrowid or 0


def executemany(sql: str, rows: list) -> None:
    with _lock:
        conn().executemany(sql, rows)


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    with _lock:
        r = conn().execute(sql, params).fetchone()
        return dict(r) if r else None


def scalar(sql: str, params: tuple | list = (), default=None):
    with _lock:
        r = conn().execute(sql, params).fetchone()
        return r[0] if r and r[0] is not None else default


def state_get(key: str, default: str | None = None) -> str | None:
    r = one("SELECT value FROM state WHERE key=?", (key,))
    return r["value"] if r else default


def state_set(key: str, value) -> None:
    execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))


def heartbeat(agent: str) -> None:
    execute("INSERT INTO heartbeats(agent, ts) VALUES(?,?) ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts",
            (agent, time.time()))


def event(kind: str, detail: str = "") -> None:
    execute("INSERT INTO events(ts, kind, detail) VALUES(?,?,?)", (time.time(), kind, detail))
