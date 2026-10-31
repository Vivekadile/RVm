"""SQLite connection handling and schema."""
import sqlite3
import time

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    machine_id       TEXT NOT NULL,
    upi_id           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',   -- active | ended
    item_count       INTEGER NOT NULL DEFAULT 0,
    total_amount     INTEGER NOT NULL DEFAULT 0,       -- rupees
    end_reason       TEXT,
    payout_status    TEXT,                             -- success | failed | skipped
    payout_ref       TEXT,
    created_at       REAL NOT NULL,
    last_activity_at REAL NOT NULL,
    ended_at         REAL
);

-- At most one active session per machine
CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_machine
    ON sessions(machine_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS deposits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    machine_id TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('bottle', 'can')),
    weight_g   REAL,
    amount     INTEGER NOT NULL,
    created_at REAL NOT NULL
);
"""


def get_db():
    """Return the request-scoped SQLite connection."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    """Close the connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    get_db().executescript(SCHEMA)


def now():
    """Current unix time in seconds."""
    return time.time()


def init_app(app):
    """Register DB teardown and create the schema."""
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
