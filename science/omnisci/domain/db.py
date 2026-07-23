# SPDX-License-Identifier: Apache-2.0
"""SQLite connection (WAL mode) and schema migrations.

The workspace database lives at ``.omnisci/state.db`` and is only ever touched
by :class:`omnisci.service.ScienceService`.
Migrations are integer-versioned, recorded in ``schema_migrations``, and
``migrate()`` is idempotent — it runs at every service construction.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from omnisci.domain.schemas import utcnow

SCHEMA_VERSION = 2

# version -> list of DDL statements, applied in ascending version order.
MIGRATIONS: dict[int, list[str]] = {
    1: [
        "CREATE TABLE IF NOT EXISTS tasks ("
        "  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS checkpoints ("
        "  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS runs ("
        "  id TEXT PRIMARY KEY, idempotency_key TEXT,"
        "  created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_runs_idempotency ON runs(idempotency_key)",
        "CREATE TABLE IF NOT EXISTS artifacts ("
        "  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS reviews ("
        "  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS approvals ("
        "  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
    ],
    2: [
        "ALTER TABLE checkpoints RENAME TO research_log",
        "ALTER TABLE reviews RENAME TO legacy_reviews",
        "CREATE TABLE reviews ("
        "  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE TABLE issues ("
        "  id TEXT PRIMARY KEY, status TEXT NOT NULL, session_id TEXT,"
        "  fingerprint TEXT, created_at TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE INDEX idx_issues_status ON issues(status)",
        "CREATE INDEX idx_issues_session ON issues(session_id)",
        "CREATE INDEX idx_issues_fingerprint ON issues(fingerprint)",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, data TEXT NOT NULL)",
    ],
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Idempotent. Returns the current version."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    current = row["v"] or 0
    for version in sorted(MIGRATIONS):
        if version > current:
            for stmt in MIGRATIONS[version]:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, utcnow()),
            )
    conn.commit()
    return SCHEMA_VERSION
