# SPDX-License-Identifier: Apache-2.0
"""``science-mcp`` — narrow structured façade over ScienceService.

The full profile exposes durable science state and managed job/storage
operations. The reviewer profile exposes only read-only context plus issue and
review recording; it cannot submit jobs or mutate research outputs.
Every handler is a thin shell: arguments → ScienceService → shared result
schema, serialized as JSON text. No business logic lives here.

Project directory resolution mirrors the ``science`` CLI: ``--project DIR``
argv flag, else the ``SCIENCE_PROJECT_DIR`` env var, else the cwd.

Errors surface as structured MCP tool-error results (``isError``) with a
clean message — never a traceback.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pydantic import BaseModel, ValidationError

from omnisci import __version__
from omnisci.compute.base import ExecutionSpec
from omnisci.errors import ApprovalRequiredError, NotFoundError, ScienceError
from omnisci.service import ScienceService

# Set by main(); tools resolve the service against this directory per call
# (same one-service-per-call pattern as the CLI).
_PROJECT_DIR: str = "."


def _service() -> ScienceService:
    return ScienceService(_PROJECT_DIR)


def _jsonable(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def _to_json(payload) -> str:
    return json.dumps(_jsonable(payload), indent=2)


def _fail(exc: Exception) -> None:
    """Convert a known failure into a clean MCP tool error (no traceback)."""
    kind = "not_found" if isinstance(exc, (NotFoundError, FileNotFoundError)) else "error"
    raise ValueError(f"{kind}: {exc}") from None


def _parse_spec(spec: dict) -> ExecutionSpec:
    try:
        return ExecutionSpec.model_validate(spec)
    except ValidationError as exc:
        raise ValueError(f"error: invalid execution spec: {exc}") from None


def build_server(profile: str = "full"):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "science",
        instructions=(
            "Structured façade over one OmniSci workspace folder. OmniGent owns "
            "ordinary tool execution; this server only records scientific state "
            "and manages explicit durable jobs."
        ),
    )

    def tool(*profiles: str):
        def decorator(fn):
            return mcp.tool()(fn) if profile in profiles else fn

        return decorator

    @tool("full", "reviewer")
    def science_project_status() -> str:
        """Workspace folder plus task, research-log, review, issue, run, and
        artifact counts as JSON."""
        try:
            return _to_json(_service().status())
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    @tool("full")
    def science_task_create(
        title: str,
        instructions: str = "",
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        assigned_agent: str | None = None,
        assigned_session: str | None = None,
    ) -> str:
        """Create one bounded research task."""
        try:
            return _to_json(
                _service().create_task(
                    title=title,
                    instructions=instructions,
                    parent_id=parent_id,
                    depends_on=depends_on,
                    expected_outputs=expected_outputs,
                    assigned_agent=assigned_agent,
                    assigned_session=assigned_session,
                )
            )
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_task_list(status: str | None = None) -> str:
        """List research tasks, optionally filtered by status."""
        try:
            return _to_json(_service().list_tasks(status=status))
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full")
    def science_task_update(
        task_id: str,
        title: str | None = None,
        instructions: str | None = None,
        status: str | None = None,
        assigned_agent: str | None = None,
        assigned_session: str | None = None,
        expected_outputs: list[str] | None = None,
    ) -> str:
        """Update a task. Moving it to done requires a linked research-log entry."""
        try:
            return _to_json(
                _service().update_task(
                    task_id,
                    title=title,
                    instructions=instructions,
                    status=status,
                    assigned_agent=assigned_agent,
                    assigned_session=assigned_session,
                    expected_outputs=expected_outputs,
                )
            )
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full")
    def science_research_log_add(
        summary: str,
        task_id: str | None = None,
        agent: str | None = None,
        session: str | None = None,
        files_changed: list[str] | None = None,
        run_ids: list[str] | None = None,
        artifact_ids: list[str] | None = None,
        sources: list[str] | None = None,
        assumptions: list[str] | None = None,
        limitations: list[str] | None = None,
        uncertainties: list[str] | None = None,
        requested_next_step: str | None = None,
    ) -> str:
        """Append a research-log entry. References to tasks, runs, and artifacts
        are validated."""
        try:
            return _to_json(
                _service().create_research_log(
                    summary=summary,
                    task_id=task_id,
                    agent=agent,
                    session=session,
                    files_changed=files_changed,
                    run_ids=run_ids,
                    artifact_ids=artifact_ids,
                    sources=sources,
                    assumptions=assumptions,
                    limitations=limitations,
                    uncertainties=uncertainties,
                    requested_next_step=requested_next_step,
                )
            )
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_research_log_list(
        task_id: str | None = None,
        session: str | None = None,
    ) -> str:
        """List research-log entries, optionally for one task or session."""
        try:
            return _to_json(_service().list_research_log(task_id=task_id, session=session))
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_issue_report(
        title: str,
        description: str,
        verification_question: str,
        session_id: str | None = None,
        task_id: str | None = None,
        research_log_id: str | None = None,
        category: str = "scientific",
        severity: str = "concern",
        evidence_refs: list[str] | None = None,
        confidence: float = 0.5,
        fingerprint: str | None = None,
        raised_by: str | None = None,
    ) -> str:
        """Raise or refresh one evidence-backed advisory issue."""
        try:
            return _to_json(
                _service().create_issue(
                    title=title,
                    description=description,
                    verification_question=verification_question,
                    session_id=session_id,
                    task_id=task_id,
                    research_log_id=research_log_id,
                    category=category,
                    severity=severity,
                    evidence_refs=evidence_refs,
                    confidence=confidence,
                    fingerprint=fingerprint,
                    raised_by=raised_by,
                )
            )
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_issue_list(
        status: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """List issues, optionally filtered by status or session."""
        try:
            return _to_json(_service().list_issues(status=status, session_id=session_id))
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_issue_update(
        issue_id: str,
        status: str,
        resolution: str | None = None,
        resolved_by: str | None = None,
    ) -> str:
        """Resolve, dismiss, or reopen one issue."""
        try:
            return _to_json(
                _service().update_issue(
                    issue_id,
                    status=status,
                    resolution=resolution,
                    resolved_by=resolved_by,
                )
            )
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_review_record(
        summary: str,
        session_id: str | None = None,
        reviewer_agent: str | None = None,
        reviewer_session: str | None = None,
        reviewer_model: str | None = None,
        reviewer_harness: str | None = None,
        reviewed_through: str | None = None,
        issue_ids: list[str] | None = None,
    ) -> str:
        """Record one completed advisory review and the issues it raised."""
        try:
            return _to_json(
                _service().record_review(
                    summary=summary,
                    session_id=session_id,
                    reviewer_agent=reviewer_agent,
                    reviewer_session=reviewer_session,
                    reviewer_model=reviewer_model,
                    reviewer_harness=reviewer_harness,
                    reviewed_through=reviewed_through,
                    issue_ids=issue_ids,
                )
            )
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full")
    def science_job_validate(spec: dict) -> str:
        """Validate an execution spec (spec §12.1, as a JSON object) against
        the local compute provider. Returns the ExecutionPlan as JSON."""
        try:
            return _to_json(_service().validate_job(_parse_spec(spec)))
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full")
    def science_job_submit(spec: dict) -> str:
        """Validate and submit an execution spec (spec §12.1, as a JSON
        object). Idempotent: resubmitting the same spec returns the existing
        run. Returns ``{"run": ..., "deduplicated": bool}`` as JSON."""
        try:
            run, deduplicated = _service().submit_job(_parse_spec(spec))
            return _to_json({"run": run, "deduplicated": deduplicated})
        except ApprovalRequiredError as exc:
            return _to_json(exc.payload())
        except (ScienceError, FileNotFoundError, ValueError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_job_status(run_id: str) -> str:
        """Status of a run, reconciled with the compute provider. Returns the
        Run record as JSON."""
        try:
            return _to_json(_service().get_run(run_id))
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    @tool("full")
    def science_job_cancel(run_id: str) -> str:
        """Cancel a run. Returns the updated Run record as JSON."""
        try:
            return _to_json(_service().cancel_run(run_id))
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    @tool("full")
    def science_storage_stage(uri: str, dest: str) -> str:
        """Stage a storage URI (file:// or project-relative path) into the
        project at ``dest``. Returns the destination ObjectMetadata as JSON."""
        try:
            return _to_json(_service().storage_get(uri, dest))
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    @tool("full")
    def science_artifact_register(
        path: str,
        type: str = "file",
        task_id: str | None = None,
        run_id: str | None = None,
        research_log_id: str | None = None,
    ) -> str:
        """Register a project file as an artifact (SHA-256 recorded).
        ``type``: data | figure | result | log | code | other. Returns the
        Artifact record as JSON."""
        try:
            return _to_json(
                _service().add_artifact(
                    path,
                    type=type,
                    task_id=task_id,
                    run_id=run_id,
                    research_log_id=research_log_id,
                )
            )
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    @tool("full", "reviewer")
    def science_artifact_list() -> str:
        """List registered artifact metadata without modifying research outputs."""
        try:
            return _to_json(_service().list_artifacts())
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    @tool("full")
    def science_skill_request(
        skill_id: str,
        reason: str = "",
        requesting_agent: str | None = None,
        requesting_session: str | None = None,
        requesting_task: str | None = None,
    ) -> str:
        """Request installation/enablement of a skill. Records the request as
        a pending Approval for an operator to resolve before installation
        (spec §9.4). Returns the approval_required response as JSON."""
        try:
            return _to_json(
                _service().request_skill(
                    skill_id,
                    reason=reason,
                    requesting_agent=requesting_agent,
                    requesting_session=requesting_session,
                    requesting_task=requesting_task,
                )
            )
        except (ScienceError, FileNotFoundError) as exc:
            _fail(exc)

    return mcp


def main(argv: list[str] | None = None) -> int:
    global _PROJECT_DIR
    parser = argparse.ArgumentParser(
        prog="science-mcp",
        description="OmniSci state and managed-job façade over stdio.",
    )
    parser.add_argument("--version", action="version", version=f"science-mcp {__version__}")
    parser.add_argument(
        "--project",
        default=os.environ.get("SCIENCE_PROJECT_DIR", "."),
        help="project directory (default: $SCIENCE_PROJECT_DIR or cwd)",
    )
    parser.add_argument(
        "--profile",
        choices=["full", "reviewer"],
        default="full",
        help="tool profile; reviewer cannot submit jobs or mutate research outputs",
    )
    args = parser.parse_args(argv)
    _PROJECT_DIR = args.project
    build_server(args.profile).run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
