"""Science workbench routes (``/v1/science/*``).

Server half of the OmniSci seam (spec §17.1): a thin adapter over
``omnisci.service.ScienceService``. The science package is an optional
dependency of the fork, so it is imported lazily per request — an import
failure surfaces as a 503 and the rest of the server is unaffected.

Endpoint paths, bodies, and the error envelope are pinned by
``science/docs/server-api-contract.md``; the web UI client
(``web/src/lib/scienceApi.ts``) implements against the same document.
Workspaces are addressed by absolute folder path (``?project=...``) and a
``ScienceService`` is constructed per request. The folder is the project; there
is no server-side registry or project manifest.
"""

from __future__ import annotations

import functools
import importlib.util
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ValidationError

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user as _require_user


class ScienceUnavailableError(Exception):
    """Raised when the optional ``omnisci`` package cannot be imported."""


def science_available() -> bool:
    """True when the science package is importable on this server.

    Backs the ``science_enabled`` flag in ``GET /v1/info``; the web UI
    gates all science UI on it. ``find_spec`` is side-effect-free (no
    import is performed).
    """
    return importlib.util.find_spec("omnisci") is not None


def _service(project_dir: str) -> Any:
    """Construct a ``ScienceService`` for the project directory.

    Raises :class:`ScienceUnavailableError` when omnisci is not
    installed; ``omnisci.errors.NotFoundError`` (mapped to 404) when the
    directory does not exist.
    """
    try:
        from omnisci.service import ScienceService
    except ImportError as exc:
        raise ScienceUnavailableError(
            "the science workbench package (omnisci) is not installed on this server"
        ) from exc
    return ScienceService(project_dir)


def _science_service_class() -> Any:
    """Load the optional service class for app-scoped science operations."""
    try:
        from omnisci.service import ScienceService
    except ImportError as exc:
        raise ScienceUnavailableError(
            "the science workbench package (omnisci) is not installed on this server"
        ) from exc
    return ScienceService


def _init_service(project_dir: str, **fields: Any) -> Any:
    """Initialize a science project without importing the optional package at boot."""
    try:
        from omnisci.service import ScienceService
    except ImportError as exc:
        raise ScienceUnavailableError(
            "the science workbench package (omnisci) is not installed on this server"
        ) from exc
    return ScienceService.init_project(project_dir, **fields)


def _import_service(export_dir: str, project_dir: str) -> Any:
    """Restore a project without importing the optional package at boot."""
    try:
        from omnisci.service import ScienceService
    except ImportError as exc:
        raise ScienceUnavailableError(
            "the science workbench package (omnisci) is not installed on this server"
        ) from exc
    return ScienceService.import_project(export_dir, project_dir)


def _execution_spec(payload: dict[str, Any]) -> Any:
    """Parse a request body into an omnisci ``ExecutionSpec`` (503-safe import)."""
    try:
        from omnisci.compute.base import ExecutionSpec
    except ImportError as exc:
        raise ScienceUnavailableError(
            "the science workbench package (omnisci) is not installed on this server"
        ) from exc
    return ExecutionSpec.model_validate(payload)


class _NeverMatched(Exception):
    """Placeholder exception class used when omnisci cannot be imported."""


def _error_response(status: int, exc_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"type": exc_type, "message": message}},
    )


def _map_error(exc: Exception) -> JSONResponse:
    """Map an omnisci/validation failure to the contract error envelope.

    404 for not-found, 409 for state conflicts (e.g. review-gate
    violations), 400 for validation errors. Anything else is re-raised.
    """
    try:
        from omnisci.errors import NotFoundError, StateError
    except ImportError:  # unreachable in practice — the 503 path fires first
        NotFoundError = StateError = _NeverMatched  # type: ignore[assignment]
    if isinstance(exc, NotFoundError):
        return _error_response(404, type(exc).__name__, str(exc))
    if isinstance(exc, StateError):
        return _error_response(409, type(exc).__name__, str(exc))
    if isinstance(exc, (ValidationError, ValueError, TypeError)):
        return _error_response(400, type(exc).__name__, str(exc))
    raise exc


def _endpoint(fn):
    """Wrap a handler with the science error mapping.

    ``functools.wraps`` preserves the signature so FastAPI dependency
    resolution still sees the original parameters. Handlers are sync so
    FastAPI runs them in the threadpool — ``ScienceService`` does
    blocking sqlite/subprocess work (job submission is synchronous for
    the local provider).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ScienceUnavailableError as exc:
            return _error_response(503, type(exc).__name__, str(exc))
        except Exception as exc:  # noqa: BLE001 — mapped per the contract
            try:
                from omnisci.errors import ApprovalRequiredError
            except ImportError:
                ApprovalRequiredError = _NeverMatched  # type: ignore[assignment]
            if isinstance(exc, ApprovalRequiredError):
                return JSONResponse(status_code=202, content=exc.payload())
            return _map_error(exc)

    return wrapper


def _dump(obj: Any) -> Any:
    """Serialize domain objects to JSON-ready dicts (contract: snake_case)."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_dump(item) for item in obj]
    return obj


def create_science_router(*, auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the ``/v1/science`` router (mounted with ``prefix="/v1"``).

    :param auth_provider: Optional auth provider; when set, the caller
        must be authenticated (no-op in single-user mode).
    :returns: A FastAPI router exposing the science workbench API.
    """
    router = APIRouter()

    # -- project ------------------------------------------------------------

    @router.post("/science/projects")
    @_endpoint
    def create_project(request: Request, payload: dict[str, Any]) -> Any:
        """Create conventional folders/state; ``directory`` is required."""
        _require_user(request, auth_provider)
        fields = dict(payload)
        unknown = set(fields) - {"directory", "research_goal"}
        if unknown:
            raise ValueError(f"unknown project fields: {', '.join(sorted(unknown))}")
        directory = fields.pop("directory", None)
        if not directory:
            raise ValueError("project creation requires a directory")
        service = _init_service(
            directory,
            research_goal=fields.pop("research_goal", ""),
        )
        return _dump(service.status())

    @router.get("/science/project/status")
    @_endpoint
    def project_status(request: Request, project: str = Query(...)) -> Any:
        """Project overview — exact shape of ``ScienceService.status()``."""
        _require_user(request, auth_provider)
        return _dump(_service(project).status())

    @router.get("/science/tools")
    @_endpoint
    def app_tools(request: Request) -> Any:
        """Application-level CLI readiness shared by every project."""
        _require_user(request, auth_provider)
        return _dump(_science_service_class().app_tools_doctor())

    @router.get("/science/infrastructure")
    @_endpoint
    def app_infrastructure(request: Request) -> Any:
        """Reusable app-level compute and storage connector definitions."""
        _require_user(request, auth_provider)
        return _dump(_science_service_class().app_infrastructure_status())

    @router.patch("/science/infrastructure")
    @_endpoint
    def update_app_infrastructure(request: Request, payload: dict[str, Any]) -> Any:
        """Persist app-level connector definitions outside any project."""
        _require_user(request, auth_provider)
        unknown = set(payload) - {"compute_config", "storage_config"}
        if unknown:
            raise ValueError(f"unknown infrastructure fields: {', '.join(sorted(unknown))}")
        return _dump(
            _science_service_class().update_app_infrastructure(
                compute_config=payload.get("compute_config"),
                storage_config=payload.get("storage_config"),
            )
        )

    @router.post("/science/project:export")
    @_endpoint
    def export_project(request: Request, project: str = Query(...)) -> Any:
        _require_user(request, auth_provider)
        service = _service(project)
        return {"export_dir": str(service.export())}

    @router.post("/science/projects:import")
    @_endpoint
    def import_project(request: Request, payload: dict[str, Any]) -> Any:
        _require_user(request, auth_provider)
        export_dir = payload.get("export_dir")
        directory = payload.get("directory")
        if not export_dir or not directory:
            raise ValueError("project import requires export_dir and directory")
        return _dump(_import_service(export_dir, directory).status())

    # -- tasks ----------------------------------------------------------------

    @router.post("/science/tasks")
    @_endpoint
    def create_task(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Create a task; body is the task fields (``title`` required)."""
        _require_user(request, auth_provider)
        return _dump(_service(project).create_task(**payload))

    @router.get("/science/tasks")
    @_endpoint
    def list_tasks(
        request: Request,
        project: str = Query(...),
        status: str | None = Query(default=None),
    ) -> Any:
        """List tasks, optionally filtered by status."""
        _require_user(request, auth_provider)
        return _dump(_service(project).list_tasks(status=status))

    @router.patch("/science/tasks/{task_id}")
    @_endpoint
    def update_task(
        request: Request,
        task_id: str,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Partially update a task (``id``/``created_at`` are immutable)."""
        _require_user(request, auth_provider)
        return _dump(_service(project).update_task(task_id, **payload))

    # -- research log --------------------------------------------------------

    @router.post("/science/research-log")
    @_endpoint
    def create_research_log(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Append a research-log entry."""
        _require_user(request, auth_provider)
        return _dump(_service(project).create_research_log(**payload))

    @router.get("/science/research-log")
    @_endpoint
    def list_research_log(
        request: Request,
        project: str = Query(...),
        task_id: str | None = Query(default=None),
        session: str | None = Query(default=None),
    ) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).list_research_log(task_id=task_id, session=session))

    @router.get("/science/research-log/{entry_id}")
    @_endpoint
    def get_research_log(request: Request, entry_id: str, project: str = Query(...)) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).get_research_log(entry_id))

    # -- reviews ---------------------------------------------------------------

    @router.post("/science/reviews")
    @_endpoint
    def record_review(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Record one completed advisory background review."""
        _require_user(request, auth_provider)
        return _dump(_service(project).record_review(**payload))

    @router.get("/science/reviews")
    @_endpoint
    def list_reviews(
        request: Request,
        project: str = Query(...),
        session_id: str | None = Query(default=None),
    ) -> Any:
        """List background reviews, optionally filtered to one session."""
        _require_user(request, auth_provider)
        return _dump(_service(project).list_reviews(session_id=session_id))

    # -- issues ---------------------------------------------------------------

    @router.post("/science/issues")
    @_endpoint
    def create_issue(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).create_issue(**payload))

    @router.get("/science/issues")
    @_endpoint
    def list_issues(
        request: Request,
        project: str = Query(...),
        status: str | None = Query(default=None),
        session_id: str | None = Query(default=None),
    ) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).list_issues(status=status, session_id=session_id))

    @router.patch("/science/issues/{issue_id}")
    @_endpoint
    def update_issue(
        request: Request,
        issue_id: str,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).update_issue(issue_id, **payload))

    # -- jobs (compute) ---------------------------------------------------------

    @router.get("/science/compute/providers")
    @_endpoint
    def compute_providers(request: Request, project: str = Query(...)) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).job_providers())

    @router.post("/science/compute:check")
    @_endpoint
    def check_compute(
        request: Request,
        project: str = Query(...),
        provider: str | None = Query(default=None),
    ) -> Any:
        """Probe compute connectors and report why any cannot be used.

        Configuration alone does not prove a connector works; this is what the
        workbench calls behind "Test connection".
        """
        _require_user(request, auth_provider)
        return _dump(_service(project).check_compute(provider))

    @router.post("/science/jobs:validate")
    @_endpoint
    def validate_job(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Validate an execution spec; returns the resolved execution plan."""
        _require_user(request, auth_provider)
        spec = _execution_spec(payload)
        return _dump(_service(project).validate_job(spec))

    @router.post("/science/jobs:submit")
    @_endpoint
    def submit_job(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Validate and submit; an identical idempotency key deduplicates."""
        _require_user(request, auth_provider)
        spec = _execution_spec(payload)
        run, deduplicated = _service(project).submit_job(spec)
        return {"run": _dump(run), "deduplicated": deduplicated}

    @router.get("/science/jobs")
    @_endpoint
    def list_runs(request: Request, project: str = Query(...)) -> Any:
        """List run records for the project."""
        _require_user(request, auth_provider)
        return _dump(_service(project).list_runs())

    @router.get("/science/jobs/{run_id}")
    @_endpoint
    def get_run(request: Request, run_id: str, project: str = Query(...)) -> Any:
        """Run record, reconciled with the provider's state."""
        _require_user(request, auth_provider)
        return _dump(_service(project).get_run(run_id))

    @router.get("/science/jobs/{run_id}/logs")
    @_endpoint
    def run_logs(
        request: Request,
        run_id: str,
        project: str = Query(...),
        cursor: str | None = Query(default=None),
    ) -> Any:
        """Log page for a run (local provider returns everything at once)."""
        _require_user(request, auth_provider)
        page = _service(project).run_logs(run_id, cursor)
        return {"stdout": page.content, "stderr": page.stderr, "cursor": page.next_cursor}

    @router.post("/science/jobs/{run_id}:cancel")
    @_endpoint
    def cancel_run(request: Request, run_id: str, project: str = Query(...)) -> Any:
        """Cancel a run; 409 if it is already in a terminal state."""
        _require_user(request, auth_provider)
        return _dump(_service(project).cancel_run(run_id))

    @router.get("/science/jobs/{run_id}/outputs")
    @_endpoint
    def run_outputs(request: Request, run_id: str, project: str = Query(...)) -> Any:
        """Artifacts registered as outputs of a run."""
        _require_user(request, auth_provider)
        return _dump(_service(project).run_outputs(run_id))

    # -- artifacts ----------------------------------------------------------------

    @router.get("/science/artifacts")
    @_endpoint
    def list_artifacts(request: Request, project: str = Query(...)) -> Any:
        """List all artifacts registered in the project."""
        _require_user(request, auth_provider)
        return _dump(_service(project).list_artifacts())

    @router.post("/science/artifacts")
    @_endpoint
    def add_artifact(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Register an existing project file as an artifact (``path`` required)."""
        _require_user(request, auth_provider)
        return _dump(_service(project).add_artifact(**payload))

    @router.get("/science/artifacts/{artifact_id}/content")
    @_endpoint
    def artifact_content(
        request: Request,
        artifact_id: str,
        project: str = Query(...),
    ) -> Any:
        """Stream a local project artifact for inline preview or download."""
        _require_user(request, auth_provider)
        service = _service(project)
        artifact = service.get_artifact(artifact_id)
        return FileResponse(
            service.artifact_content_path(artifact_id),
            media_type=artifact.mime,
        )

    # -- approvals ------------------------------------------------------------------

    @router.get("/science/approvals")
    @_endpoint
    def list_approvals(request: Request, project: str = Query(...)) -> Any:
        """List approval requests recorded for the project."""
        _require_user(request, auth_provider)
        return _dump(_service(project).list_approvals())

    @router.post("/science/approvals/{approval_id}:resolve")
    @_endpoint
    def resolve_approval(
        request: Request,
        approval_id: str,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Approve or deny one pending semantic approval."""
        _require_user(request, auth_provider)
        return _dump(_service(project).resolve_approval(approval_id, **payload))

    @router.post("/science/approvals/{approval_id}:revoke")
    @_endpoint
    def revoke_approval(
        request: Request,
        approval_id: str,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        """Revoke one previously approved semantic permission."""
        _require_user(request, auth_provider)
        return _dump(_service(project).revoke_approval(approval_id, **payload))

    # -- storage ---------------------------------------------------------------

    @router.get("/science/storage:list")
    @_endpoint
    def list_storage(
        request: Request,
        project: str = Query(...),
        uri: str = Query(...),
        cursor: str | None = Query(default=None),
    ) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).storage_ls(uri, cursor))

    @router.post("/science/storage:stage")
    @_endpoint
    def stage_storage(
        request: Request,
        payload: dict[str, Any],
        project: str = Query(...),
    ) -> Any:
        _require_user(request, auth_provider)
        uri = payload.get("uri") or payload.get("source")
        destination = payload.get("destination") or payload.get("dest") or payload.get("to")
        if not uri or not destination:
            raise ValueError("storage staging requires uri/source and destination")
        return _dump(_service(project).storage_get(uri, destination))

    # -- skills ----------------------------------------------------------------

    @router.get("/science/skills")
    @_endpoint
    def list_skills(request: Request, project: str = Query(...)) -> Any:
        """Installed skill pins with project enablement state."""
        _require_user(request, auth_provider)
        return _dump(_service(project).skills_list()["installed"])

    @router.post("/science/skills:sync")
    @_endpoint
    def sync_skills(
        request: Request,
        payload: dict[str, Any] | None = None,
        project: str = Query(...),
    ) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).skills_sync(source=(payload or {}).get("source")))

    @router.post("/science/skills/{skill_id}:install")
    @_endpoint
    def install_skill(
        request: Request,
        skill_id: str,
        payload: dict[str, Any] | None = None,
        project: str = Query(...),
    ) -> Any:
        _require_user(request, auth_provider)
        options = payload or {}
        return _dump(
            _service(project).skill_install(
                skill_id,
                source=options.get("source"),
                allow_unknown_license=options.get("allow_unknown_license", False),
            )
        )

    @router.post("/science/skills/{skill_id}:enable")
    @_endpoint
    def enable_skill(request: Request, skill_id: str, project: str = Query(...)) -> Any:
        _require_user(request, auth_provider)
        return _dump(_service(project).skill_enable(skill_id))

    return router
