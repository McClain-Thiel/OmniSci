# SPDX-License-Identifier: Apache-2.0
"""Drive the ``science-mcp`` server in-process over stdio (spec §9.2).

Each test spawns ``python -m omnisci.mcp_server --project <tmp project>``
as a subprocess and talks to it with the official MCP client SDK, so the
wire protocol, tool schemas and error mapping are all exercised for real.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from omnisci.domain.schemas import ApprovalDecision, IssueStatus, RunState
from omnisci.service import ScienceService

from tests.conftest import allow_unapproved_local_compute, make_spec


@pytest.fixture
def mcp_project(tmp_path):
    project_dir = tmp_path / "proj"
    svc = ScienceService.init_project(project_dir, research_goal="mcp test goal")
    allow_unapproved_local_compute(svc)
    (project_dir / "data" / "experiment.csv").write_text("group,value\ncontrol,1.1\n")
    return svc


def _server_params(project_dir, profile: str = "full") -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "omnisci.mcp_server",
            "--project",
            str(project_dir),
            "--profile",
            profile,
        ],
    )


def _payload(result):
    assert result.content, "tool returned no content"
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


async def _call(project_dir, calls):
    """Run ``[(tool, args), ...]`` against one server session; return results."""
    async with (
        stdio_client(_server_params(project_dir)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        results = []
        for tool_name, arguments in calls:
            results.append(await session.call_tool(tool_name, arguments))
        return {t.name for t in listed.tools}, results


def run_session(project_dir, *calls):
    return asyncio.run(_call(project_dir, calls))


EXPECTED_TOOLS = {
    "science_project_status",
    "science_task_create",
    "science_task_list",
    "science_task_update",
    "science_research_log_add",
    "science_research_log_list",
    "science_issue_report",
    "science_issue_list",
    "science_issue_update",
    "science_review_record",
    "science_job_validate",
    "science_job_submit",
    "science_job_status",
    "science_job_cancel",
    "science_storage_stage",
    "science_artifact_register",
    "science_artifact_list",
    "science_skill_request",
}


def test_tool_listing(mcp_project):
    tools, _ = run_session(mcp_project.project_dir)
    assert tools == EXPECTED_TOOLS


def test_project_status(mcp_project):
    _, (result,) = run_session(mcp_project.project_dir, ("science_project_status", {}))
    payload = _payload(result)
    assert payload["project"]["name"] == "proj"
    assert payload["project"]["directory"] == str(mcp_project.project_dir)
    assert payload["counts"] == {
        "tasks": 0,
        "tasks_by_status": {},
        "research_log": 0,
        "reviews": 0,
        "issues": 0,
        "open_issues": 0,
        "runs": 0,
        "artifacts": 0,
        "approvals": 0,
    }


def test_research_log_issue_and_review(mcp_project):
    task = mcp_project.create_task(title="Analyze")

    # Sequential: the review needs the issue id from the first call.
    async def seq():
        async with (
            stdio_client(_server_params(mcp_project.project_dir)) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            log_result = await session.call_tool(
                "science_research_log_add",
                {
                    "task_id": task.id,
                    "summary": "analysis complete",
                    "agent": "worker",
                    "files_changed": ["analyses/analyze.py"],
                    "limitations": ["tiny sample"],
                },
            )
            entry = _payload(log_result)
            issue_result = await session.call_tool(
                "science_issue_report",
                {
                    "title": "Missing uncertainty",
                    "description": "The result does not quantify uncertainty.",
                    "verification_question": "What interval or sensitivity analysis supports it?",
                    "session_id": "session-1",
                    "research_log_id": entry["id"],
                    "evidence_refs": [entry["id"]],
                    "severity": "major",
                },
            )
            issue = _payload(issue_result)
            review_result = await session.call_tool(
                "science_review_record",
                {
                    "summary": "One uncertainty issue raised.",
                    "session_id": "session-1",
                    "reviewer_agent": "reviewer",
                    "issue_ids": [issue["id"]],
                },
            )
            return entry, issue, _payload(review_result)

    entry, issue, review = asyncio.run(seq())
    assert entry["task_id"] == task.id
    assert entry["files_changed"] == ["analyses/analyze.py"]
    assert issue["research_log_id"] == entry["id"]
    assert issue["status"] == IssueStatus.OPEN.value
    assert review["issue_ids"] == [issue["id"]]

    stored = mcp_project.get_review(review["id"])
    assert stored.summary == "One uncertainty issue raised."


def test_task_lifecycle_uses_research_log(mcp_project):
    async def seq():
        async with (
            stdio_client(_server_params(mcp_project.project_dir)) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            created = _payload(
                await session.call_tool(
                    "science_task_create",
                    {
                        "title": "Analyze",
                        "expected_outputs": ["results/summary.json"],
                    },
                )
            )
            await session.call_tool(
                "science_research_log_add",
                {
                    "task_id": created["id"],
                    "summary": "Analysis and checks completed.",
                },
            )
            updated = _payload(
                await session.call_tool(
                    "science_task_update",
                    {"task_id": created["id"], "status": "done"},
                )
            )
            listed = _payload(await session.call_tool("science_task_list", {"status": "done"}))
            return created, updated, listed

    created, updated, listed = asyncio.run(seq())
    assert created["expected_outputs"] == ["results/summary.json"]
    assert updated["status"] == "done"
    assert [task["id"] for task in listed] == [created["id"]]


def test_reviewer_profile_is_restricted(mcp_project):
    async def listed():
        async with (
            stdio_client(_server_params(mcp_project.project_dir, "reviewer")) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}

    assert asyncio.run(listed()) == {
        "science_project_status",
        "science_task_list",
        "science_research_log_list",
        "science_issue_report",
        "science_issue_list",
        "science_issue_update",
        "science_review_record",
        "science_job_status",
        "science_artifact_list",
    }


def _execution_spec_dict() -> dict:
    spec = make_spec(
        [
            sys.executable,
            "-c",
            "import json, pathlib;"
            "pathlib.Path('results').mkdir(exist_ok=True);"
            "pathlib.Path('results/out.json').write_text(json.dumps({'ok': True}))",
        ],
        output_files=["results/out.json"],
        name="mcp-run",
    )
    return spec.model_dump(by_alias=True, mode="json")


def test_job_validate_submit_status(mcp_project):
    spec = _execution_spec_dict()

    async def seq():
        async with (
            stdio_client(_server_params(mcp_project.project_dir)) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            plan = _payload(await session.call_tool("science_job_validate", {"spec": spec}))
            submitted = _payload(await session.call_tool("science_job_submit", {"spec": spec}))
            status = _payload(
                await session.call_tool("science_job_status", {"run_id": submitted["run"]["id"]})
            )
            resubmit = _payload(await session.call_tool("science_job_submit", {"spec": spec}))
            return plan, submitted, status, resubmit

    plan, submitted, status, resubmit = asyncio.run(seq())
    assert plan["spec_hash"] == submitted["run"]["spec_hash"]
    assert submitted["deduplicated"] is False
    assert submitted["run"]["status"] == RunState.SUCCEEDED.value
    assert status["id"] == submitted["run"]["id"]
    assert status["exit_code"] == 0
    assert status["output_artifact_ids"], "outputs should be registered"
    # idempotent resubmission returns the same run
    assert resubmit["deduplicated"] is True
    assert resubmit["run"]["id"] == submitted["run"]["id"]


def test_job_cancel_terminal_run_is_structured_error(mcp_project):
    run, _ = mcp_project.submit_job(make_spec([sys.executable, "-c", "pass"], name="x"))
    assert run.status == RunState.SUCCEEDED

    _, (result,) = run_session(mcp_project.project_dir, ("science_job_cancel", {"run_id": run.id}))
    assert result.isError
    text = result.content[0].text
    assert "already terminal" in text
    assert "Traceback" not in text


def test_storage_stage(mcp_project):
    _, (result,) = run_session(
        mcp_project.project_dir,
        ("science_storage_stage", {"uri": "data/experiment.csv", "dest": "data/staged.csv"}),
    )
    payload = _payload(result)
    staged = mcp_project.project_dir / "data" / "staged.csv"
    assert staged.read_text() == "group,value\ncontrol,1.1\n"
    assert payload["size_bytes"] == staged.stat().st_size
    assert len(payload["checksum_sha256"]) == 64


def test_artifact_register(mcp_project):
    task = mcp_project.create_task(title="Produce figure")
    _, (result, listed) = run_session(
        mcp_project.project_dir,
        (
            "science_artifact_register",
            {"path": "data/experiment.csv", "type": "data", "task_id": task.id},
        ),
        ("science_artifact_list", {}),
    )
    payload = _payload(result)
    assert payload["path"] == "data/experiment.csv"
    assert payload["type"] == "data"
    assert payload["task_id"] == task.id
    assert len(payload["checksum_sha256"]) == 64
    assert payload["mime"] == "text/csv"
    assert [artifact["id"] for artifact in _payload(listed)] == [payload["id"]]


def test_skill_request_records_pending_approval(mcp_project):
    _, (result,) = run_session(
        mcp_project.project_dir,
        (
            "science_skill_request",
            {"skill_id": "scanpy", "reason": "single-cell analysis", "requesting_agent": "worker"},
        ),
    )
    payload = _payload(result)
    assert payload["status"] == "approval_required"
    assert "operator" in payload["message"]
    approval = payload["approval"]
    assert approval["action"] == "skill.enable:scanpy"
    assert approval["decision"] == ApprovalDecision.PENDING.value
    # the pending approval is persisted in the project db
    stored = [a for a in mcp_project.repo.list("approvals") if a.id == approval["id"]]
    assert len(stored) == 1
    assert stored[0].decision == ApprovalDecision.PENDING


def test_unknown_task_returns_structured_error(mcp_project):
    _, (result,) = run_session(
        mcp_project.project_dir,
        (
            "science_research_log_add",
            {"task_id": "task_doesnotexist", "summary": "nope"},
        ),
    )
    assert result.isError
    text = result.content[0].text
    assert "not_found:" in text
    assert "Traceback" not in text


def test_invalid_execution_spec_returns_structured_error(mcp_project):
    _, (result,) = run_session(
        mcp_project.project_dir,
        ("science_job_validate", {"spec": {"kind": "Execution"}}),
    )
    assert result.isError
    assert "invalid execution spec" in result.content[0].text
    assert "Traceback" not in result.content[0].text
