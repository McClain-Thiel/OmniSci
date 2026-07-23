# SPDX-License-Identifier: Apache-2.0
"""CRUD over the project database.

Rows are stored as ``(id, created_at, data)`` where ``data`` is the canonical
JSON dump of the pydantic model. Hot lookup fields for runs and issues are also
indexed explicitly.
"""

from __future__ import annotations

import json
import sqlite3
import threading

from pydantic import BaseModel

from omnisci.domain import db as _db
from omnisci.domain.schemas import (
    Approval,
    Artifact,
    Issue,
    ResearchLogEntry,
    Review,
    Run,
    Task,
)
from omnisci.errors import NotFoundError

TABLE_MODELS: dict[str, type[BaseModel]] = {
    "tasks": Task,
    "research_log": ResearchLogEntry,
    "runs": Run,
    "artifacts": Artifact,
    "reviews": Review,
    "issues": Issue,
    "approvals": Approval,
}


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()

    # -- generic helpers -----------------------------------------------------

    def _model(self, table: str) -> type[BaseModel]:
        if table not in TABLE_MODELS:
            raise ValueError(f"unknown table: {table}")
        return TABLE_MODELS[table]

    def _decode(self, table: str, row: sqlite3.Row) -> BaseModel:
        return self._model(table).model_validate(json.loads(row["data"]))

    # -- CRUD ----------------------------------------------------------------

    def add(self, table: str, obj: BaseModel) -> BaseModel:
        data = json.dumps(obj.model_dump(mode="json"), sort_keys=True)
        with self._lock:
            if table == "runs":
                self._conn.execute(
                    "INSERT INTO runs (id, idempotency_key, created_at, data) VALUES (?, ?, ?, ?)",
                    (obj.id, obj.idempotency_key, obj.created_at, data),
                )
            elif table == "issues":
                self._conn.execute(
                    "INSERT INTO issues "
                    "(id, status, session_id, fingerprint, created_at, data) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        obj.id,
                        obj.status.value,
                        obj.session_id,
                        obj.fingerprint,
                        obj.created_at,
                        data,
                    ),
                )
            else:
                self._conn.execute(
                    f"INSERT INTO {table} (id, created_at, data) VALUES (?, ?, ?)",
                    (obj.id, obj.created_at, data),
                )
            self._conn.commit()
        return obj

    def get(self, table: str, object_id: str) -> BaseModel:
        row = self._conn.execute(f"SELECT * FROM {table} WHERE id = ?", (object_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"{table[:-1]} not found: {object_id}")
        return self._decode(table, row)

    def get_or_none(self, table: str, object_id: str) -> BaseModel | None:
        try:
            return self.get(table, object_id)
        except NotFoundError:
            return None

    def list(self, table: str) -> list[BaseModel]:
        rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY created_at, id").fetchall()
        return [self._decode(table, row) for row in rows]

    def update(self, table: str, obj: BaseModel) -> BaseModel:
        data = json.dumps(obj.model_dump(mode="json"), sort_keys=True)
        with self._lock:
            if table == "issues":
                cur = self._conn.execute(
                    "UPDATE issues SET status = ?, session_id = ?, fingerprint = ?, data = ? "
                    "WHERE id = ?",
                    (obj.status.value, obj.session_id, obj.fingerprint, data, obj.id),
                )
            else:
                cur = self._conn.execute(
                    f"UPDATE {table} SET data = ? WHERE id = ?", (data, obj.id)
                )
            self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"{table[:-1]} not found: {obj.id}")
        return obj

    # -- run-specific ---------------------------------------------------------

    def find_run_by_idempotency_key(self, key: str) -> Run | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE idempotency_key = ? ORDER BY created_at LIMIT 1",
            (key,),
        ).fetchone()
        return self._decode("runs", row) if row else None

    def find_issue_by_fingerprint(
        self, fingerprint: str, session_id: str | None = None
    ) -> Issue | None:
        if session_id is None:
            row = self._conn.execute(
                "SELECT * FROM issues WHERE fingerprint = ? ORDER BY created_at LIMIT 1",
                (fingerprint,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM issues WHERE fingerprint = ? AND session_id = ? "
                "ORDER BY created_at LIMIT 1",
                (fingerprint, session_id),
            ).fetchone()
        return self._decode("issues", row) if row else None

    def get_setting(self, key: str, default=None):
        row = self._conn.execute("SELECT data FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["data"]) if row else default

    def set_setting(self, key: str, value) -> None:
        data = json.dumps(value, sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, data) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (key, data),
            )
            self._conn.commit()

    def migrate(self) -> int:
        return _db.migrate(self._conn)
