# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for all domain objects (spec §7.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


def new_id(prefix: str) -> str:
    """Short prefixed id, e.g. ``task_9f2b1c4d7e01``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class RunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class IssueStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class IssueSeverity(str, Enum):
    INFO = "info"
    CONCERN = "concern"
    MAJOR = "major"
    CRITICAL = "critical"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"


class Workspace(BaseModel):
    name: str
    directory: str


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str
    instructions: str = ""
    status: TaskStatus = TaskStatus.PENDING
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    assigned_agent: str | None = None
    assigned_session: str | None = None
    expected_outputs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class ResearchLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("log"))
    task_id: str | None = None
    agent: str | None = None
    session: str | None = None
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    requested_next_step: str | None = None
    created_at: str = Field(default_factory=utcnow)


class Run(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    provider: str = "local"
    provider_run_id: str | None = None
    spec_hash: str = ""
    idempotency_key: str | None = None
    command: list[str] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    resources: dict[str, Any] | None = None
    status: RunState = RunState.QUEUED
    queued_at: str = Field(default_factory=utcnow)
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    logs_path: str | None = None
    cost_usd: float | None = None
    output_artifact_ids: list[str] = Field(default_factory=list)
    execution_spec: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("art"))
    path: str | None = None  # project-relative path
    uri: str | None = None  # storage URI (e.g. file://, s3://)
    type: str = "file"  # data | figure | result | log | code | other
    mime: str | None = None
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    research_log_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("research_log_id", "checkpoint_id"),
    )
    viewer: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)


class Issue(BaseModel):
    id: str = Field(default_factory=lambda: new_id("issue"))
    session_id: str | None = None
    task_id: str | None = None
    research_log_id: str | None = None
    category: str = "scientific"
    severity: IssueSeverity = IssueSeverity.CONCERN
    status: IssueStatus = IssueStatus.OPEN
    title: str
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    verification_question: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    fingerprint: str | None = None
    raised_by: str | None = None
    resolution: str | None = None
    resolved_by: str | None = None
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class Review(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rev"))
    session_id: str | None = None
    reviewer_agent: str | None = None
    reviewer_session: str | None = None
    reviewer_model: str | None = None
    reviewer_harness: str | None = None
    reviewed_through: str | None = None
    summary: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    action: str  # semantic action, e.g. compute.submit:local (spec §11.1)
    scope: str | None = None
    requesting_session: str | None = None
    requesting_agent: str | None = None
    requesting_task: str | None = None
    decision: ApprovalDecision = ApprovalDecision.PENDING
    actor: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    expires_at: str | None = None
    consumed_at: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None
    scope_kind: Literal["one_time", "prefix", "project"] = "one_time"
    created_at: str = Field(default_factory=utcnow)
