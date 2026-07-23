"""Contract tests for the ``/v1/science/*`` server seam (spec §17.1).

Drives ``create_science_router`` over ASGI against a real science
project initialized in ``tmp_path`` — no mocks, so the shapes asserted
here are exactly what ``ScienceService`` produces and what
``web/src/lib/scienceApi.ts`` consumes
(``science/docs/server-api-contract.md``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from omnisci.service import ScienceService

from omnigent.server.routes.science import create_science_router, science_available


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    """An initialized science project directory (the ``?project=`` value)."""
    svc = ScienceService.init_project(tmp_path / "proj", research_goal="test the seam")
    svc.set_policies(
        {
            "compute": {
                "local": {
                    "allow_unapproved_subprocess": True,
                    "allow_unapproved_network": True,
                }
            }
        }
    )
    return str(svc.project_dir)


@pytest.fixture()
def client() -> TestClient:
    """App mounting only the science router at ``/v1`` (auth disabled)."""
    app = FastAPI()
    app.include_router(create_science_router(), prefix="/v1")
    return TestClient(app)


def _execution_spec(command: list[str], **extra) -> dict:
    spec: dict = {
        "apiVersion": "v1",
        "kind": "Execution",
        "spec": {"command": command},
    }
    spec["spec"].update(extra)
    return spec


# -- project -----------------------------------------------------------------


def test_science_available_in_test_env() -> None:
    """The venv has omnisci installed, so the capability probe is true."""
    assert science_available() is True


def test_project_status(client: TestClient, project_dir: str) -> None:
    """``GET /project/status`` returns the ``ScienceService.status()`` shape."""
    resp = client.get("/v1/science/project/status", params={"project": project_dir})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project"]["name"] == "proj"
    assert body["project"]["directory"] == project_dir
    assert body["counts"]["tasks"] == 0
    assert body["counts"]["research_log"] == 0
    assert body["counts"]["open_issues"] == 0
    assert body["counts"]["runs"] == 0


def test_existing_folder_is_a_project(client: TestClient, tmp_path: Path) -> None:
    """A folder is sufficient identity; state is initialized lazily."""
    resp = client.get("/v1/science/project/status", params={"project": str(tmp_path)})

    assert resp.status_code == 200, resp.text
    assert resp.json()["project"] == {
        "name": tmp_path.name,
        "directory": str(tmp_path),
    }
    assert (tmp_path / ".omnisci" / "state.db").is_file()


def test_tools_report_available_app_infrastructure_without_project(client: TestClient) -> None:
    resp = client.get("/v1/science/tools")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "app"
    assert body["compute_providers"] == ["local"]
    assert body["storage_schemes"] == ["file"]
    assert "tools" in body


def test_project_configuration_endpoint_is_removed(client: TestClient, project_dir: str) -> None:
    resp = client.patch(
        "/v1/science/project/configuration",
        params={"project": project_dir},
        json={"compute_config": {"default_provider": "local"}},
    )

    assert resp.status_code == 404, resp.text


def test_app_infrastructure_persists_and_is_available_to_projects(
    client: TestClient, project_dir: str
) -> None:
    response = client.patch(
        "/v1/science/infrastructure",
        json={
            "compute_config": {
                "default_provider": "local",
                "providers": {},
            },
            "storage_config": {
                "default_provider": "s3",
                "allowed_roots": ["data", "results"],
                "providers": {
                    "s3": {
                        "allowed_buckets": ["research-data"],
                        "allow_write": False,
                    }
                },
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "app"
    assert body["storage_config"]["default_provider"] == "s3"
    assert body["config_path"].endswith("infrastructure.yaml")

    service = ScienceService(project_dir)
    assert service.storage_config["allowed_roots"] == ["data", "results"]
    assert service.storage_config["providers"]["s3"]["allowed_buckets"] == ["research-data"]


def test_app_infrastructure_does_not_require_project(client: TestClient) -> None:
    response = client.get("/v1/science/infrastructure")

    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "app"


def test_project_create_and_export(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "created"
    resp = client.post(
        "/v1/science/projects",
        json={
            "directory": str(project),
            "research_goal": "test project creation",
        },
    )
    assert resp.status_code == 200, resp.text
    status = resp.json()
    assert status["project"]["name"] == "created"
    assert not (project / ".omnisci" / "project.yaml").exists()

    resp = client.post(
        "/v1/science/project:export",
        params={"project": str(project)},
    )
    assert resp.status_code == 200, resp.text
    export_dir = Path(resp.json()["export_dir"])
    assert export_dir.is_dir()

    imported = tmp_path / "imported"
    resp = client.post(
        "/v1/science/projects:import",
        json={"export_dir": str(export_dir), "directory": str(imported)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["project"]["name"] == "imported"
    assert resp.json()["project"]["directory"] == str(imported)


# -- tasks ---------------------------------------------------------------------


def test_task_create_and_list(client: TestClient, project_dir: str) -> None:
    """``POST /tasks`` echoes the created Task; ``GET /tasks`` lists it."""
    resp = client.post(
        "/v1/science/tasks",
        params={"project": project_dir},
        json={"title": "collect data"},
    )

    assert resp.status_code == 200, resp.text
    task = resp.json()
    assert task["id"].startswith("task_")
    assert task["title"] == "collect data"
    assert task["status"] == "pending"

    resp = client.get("/v1/science/tasks", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    assert [t["id"] for t in resp.json()] == [task["id"]]


def test_task_create_missing_title_is_400(client: TestClient, project_dir: str) -> None:
    """A body without the required ``title`` is a validation error."""
    resp = client.post("/v1/science/tasks", params={"project": project_dir}, json={})

    assert resp.status_code == 400, resp.text
    assert "error" in resp.json()


def test_task_patch(client: TestClient, project_dir: str) -> None:
    """``PATCH /tasks/{id}`` applies partial updates; id stays immutable."""
    task = client.post(
        "/v1/science/tasks", params={"project": project_dir}, json={"title": "t"}
    ).json()

    resp = client.patch(
        f"/v1/science/tasks/{task['id']}",
        params={"project": project_dir},
        json={"status": "in_progress", "id": "task_forged"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "in_progress"
    assert body["id"] == task["id"]


def test_task_patch_unknown_is_404(client: TestClient, project_dir: str) -> None:
    resp = client.patch(
        "/v1/science/tasks/task_missing",
        params={"project": project_dir},
        json={"status": "done"},
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["type"] == "NotFoundError"


# -- research log + advisory review ------------------------------------------------


def _make_task(client: TestClient, project_dir: str, **fields) -> dict:
    resp = client.post(
        "/v1/science/tasks",
        params={"project": project_dir},
        json={"title": "t", **fields},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_research_log_create_and_get(client: TestClient, project_dir: str) -> None:
    """Research-log entries are append-only records, not workflow gates."""
    task = _make_task(client, project_dir)

    resp = client.post(
        "/v1/science/research-log",
        params={"project": project_dir},
        json={
            "task_id": task["id"],
            "summary": "first pass done",
            "assumptions": ["the sample labels are correct"],
        },
    )

    assert resp.status_code == 200, resp.text
    entry = resp.json()
    assert entry["id"].startswith("log_")
    assert entry["assumptions"] == ["the sample labels are correct"]

    resp = client.get(
        f"/v1/science/research-log/{entry['id']}",
        params={"project": project_dir},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"] == "first pass done"


def test_research_log_unknown_task_is_404(client: TestClient, project_dir: str) -> None:
    resp = client.post(
        "/v1/science/research-log",
        params={"project": project_dir},
        json={"task_id": "task_missing", "summary": "s"},
    )

    assert resp.status_code == 404, resp.text


def test_issue_checklist_and_review_scan(client: TestClient, project_dir: str) -> None:
    """Reviewer concerns are actionable issues; the scan itself is advisory."""
    task = _make_task(client, project_dir)
    entry = client.post(
        "/v1/science/research-log",
        params={"project": project_dir},
        json={"task_id": task["id"], "summary": "s"},
    ).json()

    resp = client.post(
        "/v1/science/issues",
        params={"project": project_dir},
        json={
            "task_id": task["id"],
            "research_log_id": entry["id"],
            "session_id": "session-main",
            "title": "Possible label leakage",
            "description": "The split appears to happen after preprocessing.",
            "verification_question": "Was the train/test split created first?",
            "evidence_refs": ["analyses/train.py:42"],
            "raised_by": "reviewer",
        },
    )
    assert resp.status_code == 200, resp.text
    issue = resp.json()
    assert issue["id"].startswith("issue_")
    assert issue["status"] == "open"

    review_resp = client.post(
        "/v1/science/reviews",
        params={"project": project_dir},
        json={
            "session_id": "session-main",
            "summary": "One concern requires verification.",
            "issue_ids": [issue["id"]],
            "reviewer_agent": "reviewer",
            "reviewed_through": entry["id"],
        },
    )
    assert review_resp.status_code == 200, review_resp.text
    review = review_resp.json()
    assert review["issue_ids"] == [issue["id"]]
    assert "verdict" not in review

    issues = client.get(
        "/v1/science/issues",
        params={"project": project_dir, "status": "open", "session_id": "session-main"},
    )
    assert issues.status_code == 200, issues.text
    assert [item["id"] for item in issues.json()] == [issue["id"]]

    resolved = client.patch(
        f"/v1/science/issues/{issue['id']}",
        params={"project": project_dir},
        json={
            "status": "resolved",
            "resolution": "Confirmed split occurs before preprocessing.",
            "resolved_by": "main-agent",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"

    reviews = client.get(
        "/v1/science/reviews",
        params={"project": project_dir, "session_id": "session-main"},
    )
    assert reviews.status_code == 200, reviews.text
    assert [item["id"] for item in reviews.json()] == [review["id"]]


def test_resolving_issue_requires_note(client: TestClient, project_dir: str) -> None:
    issue = client.post(
        "/v1/science/issues",
        params={"project": project_dir},
        json={
            "title": "Unverified assumption",
            "description": "The response assumes normality.",
            "verification_question": "Was normality tested?",
        },
    ).json()

    resp = client.patch(
        f"/v1/science/issues/{issue['id']}",
        params={"project": project_dir},
        json={"status": "dismissed"},
    )

    assert resp.status_code == 409, resp.text


# -- jobs --------------------------------------------------------------------------


def test_job_validate(client: TestClient, project_dir: str) -> None:
    """``POST /jobs:validate`` returns the resolved execution plan."""
    resp = client.post(
        "/v1/science/jobs:validate",
        params={"project": project_dir},
        json=_execution_spec([sys.executable, "-c", "print('hi')"]),
    )

    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["provider"] == "local"
    assert plan["spec_hash"]
    assert plan["idempotency_key"] == plan["spec_hash"]
    assert plan["command"][0] == sys.executable


def test_job_validate_empty_command_is_409(client: TestClient, project_dir: str) -> None:
    """An empty command is a state conflict per the contract's mapping."""
    resp = client.post(
        "/v1/science/jobs:validate",
        params={"project": project_dir},
        json=_execution_spec([]),
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["type"] == "StateError"


def test_job_validate_malformed_spec_is_400(client: TestClient, project_dir: str) -> None:
    resp = client.post(
        "/v1/science/jobs:validate",
        params={"project": project_dir},
        json={"kind": "Execution"},
    )

    assert resp.status_code == 400, resp.text


def test_job_submit_status_outputs_and_dedup(client: TestClient, project_dir: str) -> None:
    """Full local run: submit → succeeded, outputs collected, logs, dedup."""
    spec = _execution_spec(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('results/out.txt').write_text('42'); print('done')",
        ],
        outputs={"files": ["results/out.txt"]},
    )

    resp = client.post("/v1/science/jobs:submit", params={"project": project_dir}, json=spec)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deduplicated"] is False
    run = body["run"]
    assert run["id"].startswith("run_")
    assert run["status"] == "succeeded"
    assert run["exit_code"] == 0
    assert len(run["output_artifact_ids"]) == 1

    resp = client.get("/v1/science/jobs", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    assert [item["id"] for item in resp.json()] == [run["id"]]

    # Status endpoint reconciles with the provider record.
    resp = client.get(f"/v1/science/jobs/{run['id']}", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "succeeded"

    # Outputs are the artifacts registered from the declared output files.
    resp = client.get(f"/v1/science/jobs/{run['id']}/outputs", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    outputs = resp.json()
    assert [a["path"] for a in outputs] == ["results/out.txt"]
    assert outputs[0]["run_id"] == run["id"]
    assert outputs[0]["checksum_sha256"]

    # Logs come back in the contract's stdout/stderr/cursor envelope.
    resp = client.get(f"/v1/science/jobs/{run['id']}/logs", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    logs = resp.json()
    assert "done" in logs["stdout"]
    assert logs["stderr"] == ""
    assert logs["cursor"] is None

    # Same idempotency key (here the spec hash) returns the existing run.
    resp = client.post("/v1/science/jobs:submit", params={"project": project_dir}, json=spec)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deduplicated"] is True
    assert resp.json()["run"]["id"] == run["id"]


def test_job_submit_requires_then_consumes_http_approval(
    client: TestClient, tmp_path: Path
) -> None:
    project = ScienceService.init_project(tmp_path / "approval-project")
    spec = _execution_spec([sys.executable, "-c", "print('approved')"])

    pending = client.post(
        "/v1/science/jobs:submit", params={"project": str(project.project_dir)}, json=spec
    )
    assert pending.status_code == 202, pending.text
    approval_id = pending.json()["approval_id"]
    assert pending.json()["status"] == "approval_required"

    resolved = client.post(
        f"/v1/science/approvals/{approval_id}:resolve",
        params={"project": str(project.project_dir)},
        json={"decision": "approved", "actor": "researcher"},
    )
    assert resolved.status_code == 200, resolved.text

    submitted = client.post(
        "/v1/science/jobs:submit", params={"project": str(project.project_dir)}, json=spec
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["run"]["status"] == "succeeded"
    assert ScienceService(project.project_dir).get_approval(approval_id).consumed_at is not None


def test_job_cancel_terminal_run_is_409(client: TestClient, project_dir: str) -> None:
    """Cancelling an already-terminal run is a state conflict."""
    run = client.post(
        "/v1/science/jobs:submit",
        params={"project": project_dir},
        json=_execution_spec([sys.executable, "-c", "pass"]),
    ).json()["run"]

    resp = client.post(f"/v1/science/jobs/{run['id']}:cancel", params={"project": project_dir})

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["type"] == "StateError"


def test_job_unknown_run_is_404(client: TestClient, project_dir: str) -> None:
    resp = client.get("/v1/science/jobs/run_missing", params={"project": project_dir})

    assert resp.status_code == 404, resp.text


# -- artifacts + approvals ---------------------------------------------------------


def test_artifacts_add_and_list(client: TestClient, project_dir: str) -> None:
    """``POST /artifacts`` registers an existing project file; GET lists it."""
    data_file = Path(project_dir) / "data" / "observations.csv"
    data_file.write_text("x,y\n1,2\n")

    resp = client.post(
        "/v1/science/artifacts",
        params={"project": project_dir},
        json={"path": "data/observations.csv", "type": "data"},
    )

    assert resp.status_code == 200, resp.text
    artifact = resp.json()
    assert artifact["path"] == "data/observations.csv"
    assert artifact["type"] == "data"
    assert artifact["checksum_sha256"]

    resp = client.get("/v1/science/artifacts", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    assert [a["id"] for a in resp.json()] == [artifact["id"]]

    content = client.get(
        f"/v1/science/artifacts/{artifact['id']}/content",
        params={"project": project_dir},
    )
    assert content.status_code == 200, content.text
    assert content.headers["content-type"].startswith("text/csv")
    assert content.content == b"x,y\n1,2\n"


def test_artifact_missing_file_is_404(client: TestClient, project_dir: str) -> None:
    resp = client.post(
        "/v1/science/artifacts",
        params={"project": project_dir},
        json={"path": "data/nope.csv"},
    )

    assert resp.status_code == 404, resp.text


def test_artifact_content_missing_record_is_404(client: TestClient, project_dir: str) -> None:
    resp = client.get(
        "/v1/science/artifacts/art_missing/content",
        params={"project": project_dir},
    )

    assert resp.status_code == 404, resp.text


def test_approvals_list_empty(client: TestClient, project_dir: str) -> None:
    """``GET /approvals`` returns a list (empty on a fresh project)."""
    resp = client.get("/v1/science/approvals", params={"project": project_dir})

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_approval_resolve(client: TestClient, project_dir: str) -> None:
    approval = ScienceService(project_dir).request_skill("scanpy")["approval"]
    resp = client.post(
        f"/v1/science/approvals/{approval.id}:resolve",
        params={"project": project_dir},
        json={"decision": "approved", "actor": "researcher"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"] == "approved"
    assert resp.json()["actor"] == "researcher"


def test_approval_revoke(client: TestClient, project_dir: str) -> None:
    service = ScienceService(project_dir)
    approval = service.request_skill("scanpy")["approval"]
    service.resolve_approval(
        approval.id,
        "approved",
        actor="researcher",
        scope_kind="project",
    )
    resp = client.post(
        f"/v1/science/approvals/{approval.id}:revoke",
        params={"project": project_dir},
        json={"actor": "researcher", "reason": "Permission withdrawn"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"] == "revoked"
    assert resp.json()["revoked_by"] == "researcher"


def test_storage_stage_and_skills_list(client: TestClient, project_dir: str) -> None:
    source = Path(project_dir) / "data" / "source.txt"
    source.write_text("payload")
    resp = client.post(
        "/v1/science/storage:stage",
        params={"project": project_dir},
        json={"uri": "data/source.txt", "destination": "data/staged.txt"},
    )
    assert resp.status_code == 200, resp.text
    assert (Path(project_dir) / "data" / "staged.txt").read_text() == "payload"

    resp = client.get("/v1/science/skills", params={"project": project_dir})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# -- science unavailable -----------------------------------------------------------


def test_science_unavailable_is_503(
    client: TestClient, project_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When omnisci can't be imported, every endpoint returns the 503 envelope.

    Simulated by poisoning ``sys.modules`` — ``None`` entries make the
    lazy per-request import raise ``ImportError``, the same failure a
    server without the science package installed would hit.
    """
    monkeypatch.setitem(sys.modules, "omnisci", None)
    monkeypatch.setitem(sys.modules, "omnisci.service", None)

    resp = client.get("/v1/science/project/status", params={"project": project_dir})

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["error"]["type"] == "ScienceUnavailableError"
    assert "omnisci" in body["error"]["message"]

    # The jobs path imports omnisci.compute.base before touching the
    # service — it must hit the same 503, not a 500.
    monkeypatch.setitem(sys.modules, "omnisci.compute", None)
    monkeypatch.setitem(sys.modules, "omnisci.compute.base", None)
    resp = client.post(
        "/v1/science/jobs:validate",
        params={"project": project_dir},
        json=_execution_spec(["true"]),
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["type"] == "ScienceUnavailableError"
