# SPDX-License-Identifier: Apache-2.0
"""Repository + migration unit tests."""

from __future__ import annotations

import pytest
from omnisci.domain import db as project_db
from omnisci.domain.repository import TABLE_MODELS, Repository
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


@pytest.fixture
def repo(tmp_path):
    conn = project_db.connect(tmp_path / ".omnisci" / "state.db")
    r = Repository(conn)
    r.migrate()
    return r


def test_migrate_is_idempotent(tmp_path):
    conn = project_db.connect(tmp_path / "project.db")
    assert project_db.migrate(conn) == 2
    assert project_db.migrate(conn) == 2
    n = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert n == 2
    # WAL mode enabled
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_roundtrip_all_tables(repo):
    objects = {
        "tasks": Task(title="t1"),
        "research_log": ResearchLogEntry(task_id="task_x", summary="s"),
        "runs": Run(spec_hash="abc", idempotency_key="k1"),
        "artifacts": Artifact(path="data/x.csv"),
        "reviews": Review(summary="reviewed"),
        "issues": Issue(
            title="suspect assumption",
            description="The assumption is not supported by the recorded evidence.",
            verification_question="Which result directly supports the assumption?",
        ),
        "approvals": Approval(action="compute.submit:local"),
    }
    assert set(objects) == set(TABLE_MODELS)
    for table, obj in objects.items():
        repo.add(table, obj)
        loaded = repo.get(table, obj.id)
        assert loaded == obj
    for table, obj in objects.items():
        assert [o.id for o in repo.list(table)] == [obj.id]


def test_update(repo):
    task = repo.add("tasks", Task(title="before"))
    task.title = "after"
    repo.update("tasks", task)
    assert repo.get("tasks", task.id).title == "after"


def test_get_missing_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.get("tasks", "task_nope")
    assert repo.get_or_none("tasks", "task_nope") is None


def test_find_run_by_idempotency_key(repo):
    run = repo.add("runs", Run(spec_hash="h", idempotency_key="key-1"))
    assert repo.find_run_by_idempotency_key("key-1").id == run.id
    assert repo.find_run_by_idempotency_key("key-2") is None


def test_find_issue_by_fingerprint(repo):
    issue = repo.add(
        "issues",
        Issue(
            session_id="session-1",
            title="stale figure",
            description="The figure predates the latest analysis.",
            verification_question="Does its checksum match the latest run output?",
            fingerprint="figure-check",
        ),
    )
    assert repo.find_issue_by_fingerprint("figure-check", "session-1").id == issue.id
    assert repo.find_issue_by_fingerprint("figure-check", "session-2") is None
