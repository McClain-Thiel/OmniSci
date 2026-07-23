"""Routes for discovering and registering durable template agents.

Built-in agents are the long-lived, shared agents the server provides
out of the box — the seeded ``claude-native-ui`` agent plus anything
registered at startup with ``omnigent server --agent``. They are the
``session_id IS NULL`` rows in ``agent_store``; ``agent_store.list()``
already filters to exactly these. Session-scoped agents (created via
multipart ``POST /v1/sessions``) belong to one conversation and are read
through ``GET /v1/sessions/{id}/agent`` — never here.

The Web UI's new-session picker calls this to discover bindable
built-ins, then creates a session with
``POST /v1/sessions {agent_id, host_id, workspace}``. See
``designs/BUILTIN_AGENTS.md``.

``POST /v1/agents`` is intentionally limited to explicit single-user local
runtimes. Browser-created templates are operator-authored in that deployment
shape and may use the same environment-reference expansion as ``--agent``.
Multi-user ownership and credential delegation need a separate data model;
accepting these uploads there would let one tenant reference server secrets.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy.exc import IntegrityError

from omnigent.db.utils import builtin_agent_id, generate_agent_id
from omnigent.entities import Agent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.auth import AuthProvider, local_single_user_enabled
from omnigent.server.bundles import bundle_location, validate_agent_bundle
from omnigent.server.routes._auth_helpers import require_user as _require_user
from omnigent.server.routes._origin import require_trusted_origin
from omnigent.server.schemas import AgentObject, MCPServerSummary, PaginatedList, SkillSummary
from omnigent.stores import AgentStore
from omnigent.stores.artifact_store import ArtifactStore

_logger = logging.getLogger(__name__)


def _to_agent_object(agent: Agent, agent_cache: AgentCache) -> AgentObject:
    """
    Convert a runtime Agent entity to an API-layer AgentObject.

    Loads the spec from cache to populate ``mcp_servers``,
    ``skills``, and (when the stored row has none) the
    ``description``; on any load failure those fall back to empty /
    the stored value rather than failing the whole list — one
    unreadable bundle must not break discovery.

    :param agent: The runtime agent entity, e.g. the seeded
        ``claude-native-ui`` agent.
    :param agent_cache: Cache used to load the agent spec.
    :returns: An :class:`AgentObject` for the API response.
    """
    mcp_servers: list[MCPServerSummary] = []
    skills: list[SkillSummary] = []
    terminals: list[str] = []
    harness: str | None = None
    # Prefer the stored entity's description; fall back to the spec's
    # top-level description when the stored value is unset (single-file
    # YAML agents don't persist it at registration today). Lets the
    # new-session picker show a hover description without a migration.
    description: str | None = agent.description
    try:
        # Built-ins are operator-authored template agents
        # (session_id is None), so ${VAR} expansion against the server
        # env is allowed here; a tenant session-scoped agent would not
        # expand.
        loaded = agent_cache.load(
            agent.id, agent.bundle_location, expand_env=agent.session_id is None
        )
        if description is None:
            description = loaded.spec.description
        # Declared terminal names, in spec order (mirrors the
        # session-agent endpoint so both report it consistently).
        terminals = list(loaded.spec.terminals or {})
        # Bundled skills only — host-discovered skills are runner-owned
        # and unknowable here (no session, no runner). The new-session
        # composer uses this list for its "/" menu.
        skills = [SkillSummary(name=s.name, description=s.description) for s in loaded.spec.skills]
        mcp_servers = [
            MCPServerSummary(
                name=srv.name,
                transport=srv.transport,
                description=srv.description,
                url=srv.url,
                command=srv.command,
                args=srv.args,
            )
            for srv in loaded.spec.mcp_servers
        ]
        # Kind for the Add Agent picker (Codex vs Claude). Stays None
        # when the bundle can't be loaded (the except below).
        harness = loaded.spec.executor.harness_kind
    except Exception:  # noqa: BLE001 — spec load failure must not break the list
        _logger.debug(
            "Failed to load spec for agent %s; mcp_servers/skills will be empty",
            agent.id,
            exc_info=True,
        )
    return AgentObject(
        id=agent.id,
        name=agent.name,
        version=agent.version,
        description=description,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        harness=harness,
        mcp_servers=mcp_servers,
        mcp_servers_editable=False,
        skills=skills,
        terminals=terminals,
        # Seeded built-ins use a deterministic, name-derived id; an
        # operator/user-registered template (e.g. ``--agent``) uses a
        # random id. The picker protects the former from being shadowed
        # by a same-named ``omnigent run`` upload, but lets a newer
        # upload supersede the latter.
        builtin=agent.session_id is None and agent.id == builtin_agent_id(agent.name),
    )


def create_builtin_agents_router(
    agent_store: AgentStore,
    agent_cache: AgentCache,
    artifact_store: ArtifactStore | None = None,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for app-level template agent discovery and creation.

    Mounted with ``prefix="/v1"`` so the final path is ``/v1/agents``.

    :param agent_store: Store whose ``list()`` returns only built-in
        (``session_id IS NULL``) agents.
    :param agent_cache: Cache for loading specs (populates
        ``mcp_servers`` on each agent).
    :param auth_provider: Optional auth provider; when set, the caller
        must be authenticated.
    :returns: A FastAPI router exposing template creation and discovery.
    """
    router = APIRouter()

    @router.post(
        "/agents",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_trusted_origin)],
    )
    async def create_template_agent(
        request: Request,
        bundle: Annotated[UploadFile, File(...)],
    ) -> AgentObject:
        """Validate and register one reusable app-level agent template."""
        _require_user(request, auth_provider)
        if not local_single_user_enabled():
            raise OmnigentError(
                "Creating app-level agents in the UI is currently limited to "
                "single-user local OmniSci runtimes.",
                code=ErrorCode.FORBIDDEN,
            )
        if artifact_store is None:
            raise OmnigentError(
                "Agent bundle storage is not configured",
                code=ErrorCode.INTERNAL_ERROR,
            )

        bundle_bytes = await bundle.read()
        spec = await asyncio.to_thread(
            validate_agent_bundle,
            bundle_bytes,
            enforce_handler_allowlist=False,
        )
        assert spec.name is not None
        if await asyncio.to_thread(agent_store.get_by_name, spec.name) is not None:
            raise OmnigentError(
                f"An app-level agent named {spec.name!r} already exists.",
                code=ErrorCode.CONFLICT,
            )

        agent_id = generate_agent_id()
        location = bundle_location(agent_id, bundle_bytes)
        await asyncio.to_thread(artifact_store.put, location, bundle_bytes)
        try:
            agent = await asyncio.to_thread(
                agent_store.create,
                agent_id,
                spec.name,
                location,
                spec.description,
            )
        except IntegrityError as exc:
            await asyncio.to_thread(artifact_store.delete, location)
            raise OmnigentError(
                f"An app-level agent named {spec.name!r} already exists.",
                code=ErrorCode.CONFLICT,
            ) from exc
        except Exception:
            await asyncio.to_thread(artifact_store.delete, location)
            raise
        return _to_agent_object(agent, agent_cache)

    @router.get("/agents")
    async def list_builtin_agents(
        request: Request,
        limit: int = Query(default=20, ge=1, le=1000),
        after: str | None = Query(default=None),
        before: str | None = Query(default=None),
        order: str = Query(default="desc", pattern="^(asc|desc)$"),
    ) -> PaginatedList:
        """List built-in agents with cursor-based pagination.

        Returns only built-in agents — ``agent_store.list()`` filters
        ``session_id IS NULL`` — so session-scoped agents never appear.

        :param request: The incoming FastAPI request (for auth).
        :param limit: Maximum number of agents to return (1-1000).
        :param after: Cursor — return agents after this id.
        :param before: Cursor — return agents before this id.
        :param order: Sort order, ``"asc"`` or ``"desc"``.
        :returns: A :class:`PaginatedList` of built-in agents.
        """
        _require_user(request, auth_provider)
        page = agent_store.list(limit=limit, after=after, before=before, order=order)
        return PaginatedList(
            data=[_to_agent_object(a, agent_cache) for a in page.data],
            first_id=page.first_id,
            last_id=page.last_id,
            has_more=page.has_more,
        )

    return router
