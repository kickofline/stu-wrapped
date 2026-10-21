"""SQLite store for short-lived request state that must survive restarts and deploys:
Wrapped jobs (jobs.py) and one-time "Open in Safari" handoff links (app.py)."""
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_FILE = os.path.join(DATA_DIR, "state.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    tag TEXT NOT NULL,
    status TEXT NOT NULL,          -- waiting, running, done, error
    step INTEGER NOT NULL,
    total_steps INTEGER NOT NULL,
    message TEXT NOT NULL,
    error TEXT,
    share_id TEXT,
    owner_hash TEXT,               -- sha256 of the starting browser's owner cookie
    created_at TEXT NOT NULL       -- local time, ISO seconds
);
CREATE INDEX IF NOT EXISTS jobs_tag_status ON jobs (tag, status);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs (created_at);
CREATE TABLE IF NOT EXISTS handoffs (
    token TEXT PRIMARY KEY,
    tag TEXT NOT NULL,
    app TEXT NOT NULL,
    expires REAL NOT NULL          -- unix time
);
CREATE INDEX IF NOT EXISTS handoffs_expires ON handoffs (expires);
"""


def connect() -> sqlite3.Connection:
    """A connection in autocommit mode; callers open BEGIN IMMEDIATE for read-then-write."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con
