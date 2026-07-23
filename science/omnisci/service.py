# SPDX-License-Identifier: Apache-2.0
"""Deterministic application layer for folder-scoped scientific state.

OmniGent owns conversations, agents, and ordinary tool execution. This service
only validates and records science-specific state and managed jobs.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import mimetypes
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from omnisci.compute import registry as compute_registry
from omnisci.compute.base import (
    ExecutionPlan,
    ExecutionSpec,
    ProviderCapabilities,
    RunReference,
)
from omnisci.compute.local import DEFAULT_TIMEOUT_MINUTES, LocalComputeProvider
from omnisci.domain import db as project_db
from omnisci.domain.repository import Repository
from omnisci.domain.schemas import (
    Approval,
    ApprovalDecision,
    Artifact,
    Issue,
    IssueSeverity,
    IssueStatus,
    ResearchLogEntry,
    Review,
    Run,
    RunState,
    Task,
    TaskStatus,
    Workspace,
    utcnow,
)
from omnisci.errors import ApprovalRequiredError, NotFoundError, StateError
from omnisci.infrastructure import InfrastructureConfigStore
from omnisci.skills import install as skills_install
from omnisci.skills import registry as skills_registry
from omnisci.skills.lockfile import LockedSkill, load_lockfile
from omnisci.storage import registry as storage_registry
from omnisci.storage.base import ObjectMetadata, ObjectPage
from omnisci.storage.local import LocalStorageProvider, sha256_file
from omnisci.storage.s3 import S3StorageProvider

PROJECT_DIRS = ["papers", "data", "analyses", "notebooks", "figures", "reports", "results"]

DOMAIN_MODELS = [Task, ResearchLogEntry, Run, Artifact, Review, Issue, Approval]

STATE_DIR_NAME = ".omnisci"
LEGACY_STATE_DIR_NAME = ".science"
STATE_DB_NAME = "state.db"
LEGACY_DB_NAME = "project.db"

_CLI_TOOL_CATALOG = (
    ("science", "science", "OmniSci project, compute, storage and review broker"),
    ("python", "python", "Python interpreter"),
    ("uv", "uv", "Python environment and package manager"),
    ("git", "git", "Git version control"),
    ("aws", "aws", "AWS command-line client"),
    ("modal", "modal", "Modal command-line client"),
    ("ssh", "ssh", "OpenSSH remote shell client"),
    ("scp", "scp", "OpenSSH secure copy client"),
    ("rclone", "rclone", "Remote object-storage client"),
)

_README_TEMPLATE = """# {name}

Scientific workspace managed by OmniSci.

## Research goal

{goal}

## Layout

- `papers/` — reference papers and notes
- `data/` — input datasets
- `analyses/` — analysis code
- `notebooks/` — exploratory notebooks
- `figures/` — generated figures
- `reports/` — written reports
- `results/` — structured results
- `.omnisci/` — internal state, run logs, and exports
"""


def _resolve_project_dir(project_dir: str | Path) -> Path:
    """Canonicalize a project path without resolving a sandboxed relative cwd."""
    path = Path(project_dir).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


class ScienceService:
    """Science state and managed resources for one workspace folder."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = _resolve_project_dir(project_dir)
        if not self.project_dir.is_dir():
            raise NotFoundError(f"workspace folder not found: {self.project_dir}")
        self.state_dir = self._state_dir_for(self.project_dir)
        self.workspace = Workspace(
            name=self.project_dir.name,
            directory=str(self.project_dir),
        )
        app_infrastructure = InfrastructureConfigStore().load()
        self.compute_config = app_infrastructure["compute_config"]
        self.storage_config = app_infrastructure["storage_config"]
        db_name = LEGACY_DB_NAME if self.state_dir.name == LEGACY_STATE_DIR_NAME else STATE_DB_NAME
        conn = project_db.connect(self.state_dir / db_name)
        self.repo = Repository(conn)
        self.repo.migrate()  # idempotent
        self._migrate_legacy_project_settings()
        self.policies = self.repo.get_setting("policies", {})
        allowed_roots = self.storage_config.get("allowed_roots") or []
        self.storage = LocalStorageProvider(self.project_dir, allowed_roots)
        self.storage_providers = {"file": self.storage}
        storage_configs = self.storage_config.get("providers") or {}
        if not isinstance(storage_configs, dict):
            raise StateError("storage_config.providers must be a mapping")
        s3_config = storage_configs.get("s3")
        if s3_config is None:
            s3_config = self.storage_config.get("s3")
        if s3_config is not None:
            if not isinstance(s3_config, dict):
                raise StateError("storage_config.providers.s3 must be a mapping")
            allowed_keys = {
                "allowed_buckets",
                "allowed_prefixes",
                "allow_write",
                "endpoint_url",
                "region_name",
            }
            self.storage_providers["s3"] = S3StorageProvider(
                **{key: value for key, value in s3_config.items() if key in allowed_keys}
            )
        storage_factories = storage_registry.load_providers()
        for name, config in storage_configs.items():
            if name == "s3":
                continue
            factory = storage_factories.get(name)
            if factory is None:
                raise StateError(f"configured storage provider is not installed: {name}")
            if not isinstance(config, dict):
                raise StateError(f"storage provider config must be a mapping: {name}")
            provider = factory(project_dir=self.project_dir, config=config)
            for scheme in provider.schemes():
                if scheme in self.storage_providers:
                    raise StateError(f"duplicate storage provider for scheme: {scheme}")
                self.storage_providers[scheme] = provider

        local_compute = LocalComputeProvider(self.project_dir, runs_dir=self.state_dir / "runs")
        self.compute_providers = {"local": local_compute}
        compute_configs = self.compute_config.get("providers") or {}
        if not isinstance(compute_configs, dict):
            raise StateError("compute_config.providers must be a mapping")
        compute_factories = compute_registry.load_provider_factories()
        for name, config in compute_configs.items():
            if name == "local":
                continue
            factory = compute_factories.get(name)
            if factory is None:
                raise StateError(f"configured compute provider is not installed: {name}")
            if not isinstance(config, dict):
                raise StateError(f"compute provider config must be a mapping: {name}")
            resolved_config = self._resolve_compute_transport(name, config, compute_configs)
            provider = factory(
                project_dir=self.project_dir,
                runs_dir=self.state_dir / "runs",
                config=resolved_config,
            )
            bind_storage = getattr(provider, "bind_storage", None)
            if bind_storage is not None:
                bind_storage(self._storage_for_uri)
            advertised_name = provider.capabilities().provider
            if advertised_name != name:
                raise StateError(
                    f"compute provider '{name}' advertises mismatched name '{advertised_name}'"
                )
            self.compute_providers[name] = provider
        self.compute = local_compute  # compatibility for callers that inspect the local provider

    @staticmethod
    def _state_dir_for(project_dir: Path) -> Path:
        current = project_dir / STATE_DIR_NAME
        if current.exists():
            return current
        legacy = project_dir / LEGACY_STATE_DIR_NAME
        if (legacy / LEGACY_DB_NAME).is_file():
            return legacy
        return current

    def _migrate_legacy_project_settings(self) -> None:
        """Import durable settings once from the removed pre-release manifest."""
        if self.state_dir.name != LEGACY_STATE_DIR_NAME:
            return
        manifest = self.state_dir / "project.yaml"
        if not manifest.is_file():
            return
        if self.repo.get_setting("legacy_project_yaml_imported", False):
            return
        try:
            payload = yaml.safe_load(manifest.read_text()) or {}
        except yaml.YAMLError as exc:
            raise StateError(f"invalid legacy project manifest: {manifest}") from exc
        if not isinstance(payload, dict):
            raise StateError(f"legacy project manifest must contain a mapping: {manifest}")

        policies = payload.get("policies")
        if policies is not None:
            if not isinstance(policies, dict):
                raise StateError("legacy project policies must be a mapping")
            if self.repo.get_setting("policies") is None:
                self.repo.set_setting("policies", policies)

        enabled_skills = payload.get("enabled_skills")
        if enabled_skills is not None:
            if not isinstance(enabled_skills, list) or not all(
                isinstance(name, str) for name in enabled_skills
            ):
                raise StateError("legacy enabled_skills must be a list of names")
            if self.repo.get_setting("enabled_skills") is None:
                self.repo.set_setting("enabled_skills", enabled_skills)

        self.repo.set_setting("legacy_project_yaml_imported", True)

    @staticmethod
    def _resolve_compute_transport(
        provider_name: str,
        config: dict,
        compute_configs: dict,
    ) -> dict:
        """Merge a referenced transport profile without duplicating SSH settings."""
        transport_ref = config.get("transport_ref")
        if transport_ref is None:
            return config
        if not isinstance(transport_ref, str) or not transport_ref:
            raise StateError(f"compute provider '{provider_name}' transport_ref must be an id")
        if transport_ref == provider_name:
            raise StateError(f"compute provider '{provider_name}' cannot reference itself")
        transport_config = compute_configs.get(transport_ref)
        if not isinstance(transport_config, dict):
            raise StateError(
                f"compute provider '{provider_name}' references missing transport: {transport_ref}"
            )
        nested_ref = transport_config.get("transport_ref")
        if nested_ref is not None:
            raise StateError("nested compute transport references are not supported")
        return {
            **copy.deepcopy(transport_config),
            **{
                key: copy.deepcopy(value)
                for key, value in config.items()
                if key != "transport_ref"
            },
        }

    # -- project --------------------------------------------------------------

    @classmethod
    def init_project(
        cls,
        project_dir: str | Path,
        research_goal: str = "",
    ) -> ScienceService:
        project_dir = _resolve_project_dir(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        if not project_dir.is_dir():
            raise StateError(f"workspace path is not a directory: {project_dir}")
        for sub in PROJECT_DIRS:
            (project_dir / sub).mkdir(exist_ok=True)
        state_dir = project_dir / STATE_DIR_NAME
        (state_dir / "exports").mkdir(parents=True, exist_ok=True)
        readme = project_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                _README_TEMPLATE.format(
                    name=project_dir.name, goal=research_goal or "(not stated)"
                )
            )
        conn = project_db.connect(state_dir / STATE_DB_NAME)
        project_db.migrate(conn)
        conn.close()
        return cls(project_dir)

    def status(self) -> dict:
        tasks = self.repo.list("tasks")
        by_status: dict[str, int] = {}
        for t in tasks:
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        runs = self.repo.list("runs")
        return {
            "project": self.workspace.model_dump(mode="json"),
            "counts": {
                "tasks": len(tasks),
                "tasks_by_status": by_status,
                "research_log": len(self.repo.list("research_log")),
                "reviews": len(self.repo.list("reviews")),
                "issues": len(self.repo.list("issues")),
                "open_issues": len(
                    [
                        issue
                        for issue in self.repo.list("issues")
                        if issue.status == IssueStatus.OPEN
                    ]
                ),
                "runs": len(runs),
                "artifacts": len(self.repo.list("artifacts")),
                "approvals": len(self.repo.list("approvals")),
            },
        }

    # -- tool discovery -------------------------------------------------------

    @classmethod
    def app_tools_list(cls) -> list[dict]:
        """Report CLI tools installed on the OmniSci application host."""
        catalog = list(_CLI_TOOL_CATALOG)

        tools = []
        for tool_id, command, description in catalog:
            if tool_id == "python":
                path = sys.executable
            else:
                path = shutil.which(command)
                if path is None:
                    sibling = Path(sys.executable).parent / command
                    if sibling.is_file() and os.access(sibling, os.X_OK):
                        path = str(sibling)
            installed = path is not None or tool_id == "science"
            invocation = (
                [sys.executable, "-m", "omnisci.cli.main"]
                if tool_id == "science" and path is None
                else [path or command]
            )
            tools.append(
                {
                    "id": tool_id,
                    "kind": "cli",
                    "command": command,
                    "description": description,
                    "installed": installed,
                    "enabled": True,
                    "available": installed,
                    "path": path,
                    "invocation": invocation,
                }
            )
        return tools

    @classmethod
    def app_tools_search(cls, query: str) -> list[dict]:
        needle = query.casefold().strip()
        if not needle:
            return cls.app_tools_list()
        return [
            tool
            for tool in cls.app_tools_list()
            if needle in tool["id"].casefold()
            or needle in tool["command"].casefold()
            or needle in tool["description"].casefold()
        ]

    @classmethod
    def app_tools_doctor(cls) -> dict:
        """Return application tool readiness without opening a project."""
        tools = cls.app_tools_list()
        infrastructure = cls.app_infrastructure_status()
        compute = [
            provider["id"]
            for provider in infrastructure["compute"]
            if provider["registered"]
            and provider["dependency_available"]
            and provider["configured"]
        ]
        storage = [
            provider["id"]
            for provider in infrastructure["storage"]
            if provider["registered"]
            and provider["dependency_available"]
            and provider["configured"]
        ]
        return {
            "ok": True,
            "scope": "app",
            "compute_providers": compute,
            "storage_schemes": [
                "file" if provider == "local" else provider for provider in storage
            ],
            "tools": tools,
        }

    @classmethod
    def app_infrastructure_status(cls) -> dict:
        """Return reusable app-level connectors without requiring a project."""
        infrastructure = InfrastructureConfigStore().load()
        compute_config = infrastructure["compute_config"]
        storage_config = infrastructure["storage_config"]
        compute_factories = compute_registry.load_provider_factories()
        storage_factories = storage_registry.load_providers()
        dependencies = {
            "local": True,
            "modal": importlib.util.find_spec("modal") is not None,
            "ssh": shutil.which("ssh") is not None and shutil.which("scp") is not None,
            "slurm": shutil.which("ssh") is not None and shutil.which("scp") is not None,
            "qsub": shutil.which("ssh") is not None and shutil.which("scp") is not None,
            "s3": importlib.util.find_spec("boto3") is not None,
        }
        compute_configs = compute_config.get("providers") or {}
        storage_configs = storage_config.get("providers") or {}
        default_compute = compute_config.get("default_provider", "local")
        default_storage = storage_config.get("default_provider", "local")
        return {
            "scope": "app",
            "config_path": str(InfrastructureConfigStore().path),
            "compute_config": copy.deepcopy(compute_config),
            "storage_config": copy.deepcopy(storage_config),
            "compute": [
                {
                    "id": provider_id,
                    "registered": provider_id == "local" or provider_id in compute_factories,
                    "dependency_available": dependencies.get(provider_id, True),
                    "configured": provider_id == "local" or provider_id in compute_configs,
                    "default": provider_id == default_compute,
                }
                for provider_id in sorted({"local", *compute_factories})
            ],
            "storage": [
                {
                    "id": provider_id,
                    "registered": provider_id == "local" or provider_id in storage_factories,
                    "dependency_available": dependencies.get(provider_id, True),
                    "configured": provider_id == "local" or provider_id in storage_configs,
                    "default": provider_id == default_storage,
                }
                for provider_id in sorted({"local", *storage_factories})
            ],
        }

    @classmethod
    def update_app_infrastructure(
        cls,
        *,
        compute_config: dict | None = None,
        storage_config: dict | None = None,
    ) -> dict:
        InfrastructureConfigStore().update(
            compute_config=compute_config,
            storage_config=storage_config,
        )
        return cls.app_infrastructure_status()

    # -- tasks ------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        instructions: str = "",
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        assigned_agent: str | None = None,
        assigned_session: str | None = None,
    ) -> Task:
        if parent_id is not None:
            self.repo.get("tasks", parent_id)
        for dep in depends_on or []:
            self.repo.get("tasks", dep)
        task = Task(
            title=title,
            instructions=instructions,
            parent_id=parent_id,
            depends_on=depends_on or [],
            expected_outputs=expected_outputs or [],
            assigned_agent=assigned_agent,
            assigned_session=assigned_session,
        )
        return self.repo.add("tasks", task)

    def get_task(self, task_id: str) -> Task:
        return self.repo.get("tasks", task_id)

    def list_tasks(self, status: str | None = None) -> list[Task]:
        tasks = self.repo.list("tasks")
        if status is not None:
            tasks = [t for t in tasks if t.status.value == status]
        return tasks

    def update_task(self, task_id: str, **fields) -> Task:
        task = self.repo.get("tasks", task_id)
        new_status = fields.get("status")
        if new_status == TaskStatus.DONE.value and task.status != TaskStatus.DONE:
            entries = [
                entry for entry in self.repo.list("research_log") if entry.task_id == task.id
            ]
            if not entries:
                raise StateError(
                    f"task {task.id} cannot be marked done without a research-log entry"
                )
        data = task.model_dump()
        for key, value in fields.items():
            if value is None or key not in data or key in {"id", "created_at"}:
                continue
            data[key] = value
        data["updated_at"] = utcnow()
        task = Task.model_validate(data)
        return self.repo.update("tasks", task)

    # -- research log ---------------------------------------------------------

    def create_research_log(
        self,
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
    ) -> ResearchLogEntry:
        if task_id is not None:
            self.repo.get("tasks", task_id)
        for run_id in run_ids or []:
            self.repo.get("runs", run_id)
        for artifact_id in artifact_ids or []:
            self.repo.get("artifacts", artifact_id)
        entry = ResearchLogEntry(
            task_id=task_id,
            summary=summary,
            agent=agent,
            session=session,
            files_changed=files_changed or [],
            run_ids=run_ids or [],
            artifact_ids=artifact_ids or [],
            sources=sources or [],
            assumptions=assumptions or [],
            limitations=limitations or [],
            uncertainties=uncertainties or [],
            requested_next_step=requested_next_step,
        )
        return self.repo.add("research_log", entry)

    def get_research_log(self, entry_id: str) -> ResearchLogEntry:
        return self.repo.get("research_log", entry_id)

    def list_research_log(
        self,
        task_id: str | None = None,
        session: str | None = None,
    ) -> list[ResearchLogEntry]:
        entries = self.repo.list("research_log")
        if task_id is not None:
            entries = [entry for entry in entries if entry.task_id == task_id]
        if session is not None:
            entries = [entry for entry in entries if entry.session == session]
        return entries

    # -- reviews and issues ----------------------------------------------------

    def record_review(
        self,
        summary: str,
        session_id: str | None = None,
        reviewer_agent: str | None = None,
        reviewer_session: str | None = None,
        reviewer_model: str | None = None,
        reviewer_harness: str | None = None,
        reviewed_through: str | None = None,
        issue_ids: list[str] | None = None,
    ) -> Review:
        for issue_id in issue_ids or []:
            self.repo.get("issues", issue_id)
        review = Review(
            session_id=session_id,
            reviewer_agent=reviewer_agent,
            reviewer_session=reviewer_session,
            reviewer_model=reviewer_model,
            reviewer_harness=reviewer_harness,
            reviewed_through=reviewed_through,
            summary=summary,
            issue_ids=issue_ids or [],
        )
        return self.repo.add("reviews", review)

    def get_review(self, review_id: str) -> Review:
        return self.repo.get("reviews", review_id)

    def list_reviews(self, session_id: str | None = None) -> list[Review]:
        reviews = self.repo.list("reviews")
        if session_id is not None:
            reviews = [review for review in reviews if review.session_id == session_id]
        return reviews

    def create_issue(
        self,
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
    ) -> Issue:
        if task_id is not None:
            self.repo.get("tasks", task_id)
        if research_log_id is not None:
            self.repo.get("research_log", research_log_id)
        fingerprint = fingerprint or self._issue_fingerprint(
            session_id=session_id,
            category=category,
            title=title,
            evidence_refs=evidence_refs or [],
        )
        existing = self.repo.find_issue_by_fingerprint(fingerprint, session_id)
        if existing is not None:
            existing.title = title
            existing.description = description
            existing.verification_question = verification_question
            existing.category = category
            existing.severity = IssueSeverity(severity)
            existing.evidence_refs = evidence_refs or []
            existing.confidence = confidence
            existing.task_id = task_id
            existing.research_log_id = research_log_id
            existing.raised_by = raised_by or existing.raised_by
            existing.status = IssueStatus.OPEN
            existing.resolution = None
            existing.resolved_by = None
            existing.updated_at = utcnow()
            return self.repo.update("issues", existing)
        issue = Issue(
            session_id=session_id,
            task_id=task_id,
            research_log_id=research_log_id,
            category=category,
            severity=IssueSeverity(severity),
            title=title,
            description=description,
            evidence_refs=evidence_refs or [],
            verification_question=verification_question,
            confidence=confidence,
            fingerprint=fingerprint,
            raised_by=raised_by,
        )
        return self.repo.add("issues", issue)

    @staticmethod
    def _issue_fingerprint(
        *,
        session_id: str | None,
        category: str,
        title: str,
        evidence_refs: list[str],
    ) -> str:
        payload = json.dumps(
            {
                "session_id": session_id,
                "category": category.casefold().strip(),
                "title": title.casefold().strip(),
                "evidence_refs": sorted(evidence_refs),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def get_issue(self, issue_id: str) -> Issue:
        return self.repo.get("issues", issue_id)

    def list_issues(
        self,
        status: str | None = None,
        session_id: str | None = None,
    ) -> list[Issue]:
        issues = self.repo.list("issues")
        if status is not None:
            expected = IssueStatus(status)
            issues = [issue for issue in issues if issue.status == expected]
        if session_id is not None:
            issues = [issue for issue in issues if issue.session_id == session_id]
        return issues

    def update_issue(self, issue_id: str, **fields) -> Issue:
        issue = self.repo.get("issues", issue_id)
        data = issue.model_dump()
        for key, value in fields.items():
            if value is None or key not in data or key in {"id", "created_at", "fingerprint"}:
                continue
            data[key] = value
        status = IssueStatus(data["status"])
        if status != IssueStatus.OPEN and not data.get("resolution"):
            raise StateError("resolving or dismissing an issue requires a resolution note")
        data["updated_at"] = utcnow()
        updated = Issue.model_validate(data)
        return self.repo.update("issues", updated)

    # -- skills registry (spec §10) ----------------------------------------------

    def set_policies(self, policies: dict) -> dict:
        if not isinstance(policies, dict):
            raise StateError("workspace policies must be a mapping")
        self.policies = copy.deepcopy(policies)
        self.repo.set_setting("policies", self.policies)
        return copy.deepcopy(self.policies)

    def _enabled_skills(self) -> list[str]:
        enabled = self.repo.get_setting("enabled_skills", [])
        if not isinstance(enabled, list) or not all(isinstance(name, str) for name in enabled):
            raise StateError("enabled_skills setting must be a list of names")
        return enabled

    def skills_sync(self, source: str | None = None) -> dict[str, str]:
        """Fetch source refs into the cache; returns source -> resolved commit.
        Never touches installed skills or the lockfile (spec §10.3)."""
        return skills_registry.sync_sources(self.state_dir, only=source)

    def skills_list(self) -> dict:
        sources = skills_registry.load_sources(self.state_dir)
        locked = load_lockfile(self.state_dir)
        enabled_skills = self._enabled_skills()
        return {
            "sources": {
                name: {
                    **src.model_dump(mode="json"),
                    "synced_revision": skills_registry.resolved_revision(self.state_dir, name),
                }
                for name, src in sources.items()
            },
            "installed": [
                {
                    "name": name,
                    **entry.model_dump(mode="json"),
                    "enabled": name in enabled_skills,
                }
                for name, entry in locked.items()
            ],
        }

    def skills_search(self, query: str = "") -> list[dict]:
        """Skills available in synced sources, optionally filtered by a
        case-insensitive substring of the name or path."""
        results = []
        for name in skills_registry.load_sources(self.state_dir):
            if not skills_registry.cache_repo(self.state_dir, name).exists():
                continue  # unsynced sources have nothing to search yet
            results.extend(skills_registry.list_source_skills(self.state_dir, name))
        if query:
            needle = query.lower()
            results = [
                r for r in results if needle in r["name"].lower() or needle in r["path"].lower()
            ]
        return results

    def skill_install(
        self,
        name: str,
        source: str | None = None,
        allow_unknown_license: bool = False,
    ) -> LockedSkill:
        return skills_install.install_skill(
            self.state_dir,
            name,
            source=source,
            allow_unknown_license=allow_unknown_license,
        )

    def skill_upgrade(self, name: str, allow_unknown_license: bool = False) -> LockedSkill:
        return skills_install.upgrade_skill(
            self.state_dir, name, allow_unknown_license=allow_unknown_license
        )

    def skill_rollback(self, name: str) -> LockedSkill:
        return skills_install.rollback_skill(self.state_dir, name)

    def skill_enable(self, name: str) -> dict:
        if name not in load_lockfile(self.state_dir):
            raise NotFoundError(f"skill '{name}' is not installed")
        enabled_skills = self._enabled_skills()
        if name not in enabled_skills:
            enabled_skills.append(name)
            self.repo.set_setting("enabled_skills", enabled_skills)
        return {"skill": name, "enabled_skills": enabled_skills}

    def skill_disable(self, name: str) -> dict:
        enabled_skills = self._enabled_skills()
        if name in enabled_skills:
            enabled_skills.remove(name)
            self.repo.set_setting("enabled_skills", enabled_skills)
        return {"skill": name, "enabled_skills": enabled_skills}

    # -- skills (agent-facing approval stand-in, spec §9.4) ------------------------

    def request_skill(
        self,
        skill_id: str,
        reason: str = "",
        requesting_agent: str | None = None,
        requesting_session: str | None = None,
        requesting_task: str | None = None,
    ) -> dict:
        """Record a skill install/enable request as a pending Approval
        (spec §9.4: installation requires approval). This is the agent-facing
        path; the skills registry methods above are the operator-facing ones.
        Approval-driven auto-install is not wired up yet, so this always
        returns ``approval_required``; an operator installs and enables the
        pinned skill after approval."""
        approval = Approval(
            action=f"skill.enable:{skill_id}",
            scope=skill_id,
            requesting_agent=requesting_agent,
            requesting_session=requesting_session,
            requesting_task=requesting_task,
            reason=reason or None,
        )
        approval = self.repo.add("approvals", approval)
        return {
            "status": "approval_required",
            "approval": approval,
            "message": (
                f"Skill '{skill_id}' requires approval; the request is recorded "
                "as a pending approval for an operator to resolve before install."
            ),
        }

    # -- approvals ---------------------------------------------------------------

    def get_approval(self, approval_id: str) -> Approval:
        return self.repo.get("approvals", approval_id)

    def list_approvals(self, decision: str | None = None) -> list[Approval]:
        approvals = self.repo.list("approvals")
        if decision is not None:
            approvals = [approval for approval in approvals if approval.decision.value == decision]
        return approvals

    def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        actor: str,
        reason: str | None = None,
        scope_kind: str | None = None,
    ) -> Approval:
        approval = self.repo.get("approvals", approval_id)
        if approval.decision != ApprovalDecision.PENDING:
            raise StateError(f"approval {approval_id} is already {approval.decision.value}")
        resolved = ApprovalDecision(decision)
        if resolved == ApprovalDecision.PENDING:
            raise StateError("approval resolution must be approved or denied")
        if resolved == ApprovalDecision.REVOKED:
            raise StateError("use revoke_approval to revoke an approved permission")
        approval.decision = resolved
        approval.actor = actor
        approval.decided_at = utcnow()
        approval.reason = reason or approval.reason
        if scope_kind is not None:
            if scope_kind not in {"one_time", "prefix", "project"}:
                raise StateError(f"invalid approval scope kind: {scope_kind}")
            approval.scope_kind = scope_kind
        return self.repo.update("approvals", approval)

    def revoke_approval(
        self,
        approval_id: str,
        actor: str,
        reason: str | None = None,
    ) -> Approval:
        approval = self.repo.get("approvals", approval_id)
        if approval.decision != ApprovalDecision.APPROVED:
            raise StateError(
                f"approval {approval_id} is {approval.decision.value}; only approved "
                "permissions can be revoked"
            )
        if approval.scope_kind == "one_time" and approval.consumed_at is not None:
            raise StateError(
                f"approval {approval_id} was already consumed; the completed action "
                "cannot be revoked"
            )
        approval.decision = ApprovalDecision.REVOKED
        approval.revoked_at = utcnow()
        approval.revoked_by = actor
        approval.revocation_reason = reason
        return self.repo.update("approvals", approval)

    # -- jobs (compute) -----------------------------------------------------------

    def job_providers(self) -> list[ProviderCapabilities]:
        return [
            self.compute_providers[name].capabilities() for name in sorted(self.compute_providers)
        ]

    def _compute_for(self, provider_name: str):
        provider = self.compute_providers.get(provider_name)
        if provider is None:
            raise StateError(f"compute provider is not configured: {provider_name}")
        return provider

    def validate_job(self, spec: ExecutionSpec) -> ExecutionPlan:
        return self._compute_for(spec.spec.provider).validate(spec)

    def submit_job(self, spec: ExecutionSpec) -> tuple[Run, bool]:
        """Validate and submit. Returns ``(run, deduplicated)``: a second
        submission with the same idempotency key returns the existing run
        record instead of creating duplicate work (spec §20)."""
        provider = self._compute_for(spec.spec.provider)
        plan = provider.validate(spec)
        existing = self.repo.find_run_by_idempotency_key(plan.idempotency_key)
        if existing is not None:
            return existing, True

        self._enforce_compute_approval(plan)

        details = spec.spec
        run = Run(
            provider=plan.provider,
            spec_hash=plan.spec_hash,
            idempotency_key=plan.idempotency_key,
            command=plan.command,
            environment=details.environment.model_dump(mode="json"),
            inputs=[i.model_dump(mode="json") for i in details.inputs],
            resources=(details.resources.model_dump(mode="json") if details.resources else None),
            status=RunState.QUEUED,
            execution_spec=spec.model_dump(by_alias=True, mode="json"),
        )
        ref = provider.submit(plan)
        status = provider.status(ref)
        run.provider_run_id = ref.provider_run_id
        run.id = ref.run_id
        run.status = status.status
        run.exit_code = status.exit_code
        run.queued_at = status.queued_at or run.queued_at
        run.started_at = status.started_at
        run.finished_at = status.finished_at
        run.logs_path = f"{self.state_dir.name}/runs/{ref.run_id}"
        self.repo.add("runs", run)

        if run.status == RunState.SUCCEEDED:
            self._collect_run_outputs(run, provider)
        return run, False

    def _enforce_compute_approval(self, plan: ExecutionPlan) -> None:
        details = plan.spec.spec
        compute_policy = self.policies.get("compute") or {}
        if not isinstance(compute_policy, dict):
            raise StateError("policies.compute must be a mapping")
        config = compute_policy.get(plan.provider) or {}
        if not isinstance(config, dict):
            raise StateError(f"policies.compute.{plan.provider} must be a mapping")
        reasons = []
        if plan.provider != "local" and not config.get("allow_unapproved_submit", False):
            remote_reason = "remote provider execution"
            if details.resources and details.resources.gpu:
                gpu = details.resources.gpu
                remote_reason += f" using {gpu.count}x {gpu.type} GPU"
            if details.limits.max_estimated_cost_usd is not None:
                remote_reason += (
                    f" with ${details.limits.max_estimated_cost_usd:g} estimated cost cap"
                )
            reasons.append(remote_reason)
        if (
            plan.provider == "local"
            and details.mode == "subprocess"
            and not config.get("allow_unapproved_subprocess", False)
        ):
            reasons.append("unsandboxed subprocess execution")
        if details.network.mode == "allow" and not config.get("allow_unapproved_network", False):
            reasons.append("unrestricted network access")
        runtime_limit = details.limits.max_runtime_minutes or DEFAULT_TIMEOUT_MINUTES
        normal_runtime = config.get("max_runtime_minutes_without_approval", 60)
        if not isinstance(normal_runtime, (int, float)) or normal_runtime <= 0:
            raise StateError(
                f"policies.compute.{plan.provider}.max_runtime_minutes_without_approval "
                "must be a positive number"
            )
        if runtime_limit > normal_runtime:
            reasons.append(f"runtime limit {runtime_limit:g}m exceeds {normal_runtime:g}m")
        if not reasons:
            return
        reason = "; ".join(reasons)
        self._require_approval(
            action=f"compute.submit:{plan.provider}",
            scope=plan.idempotency_key,
            message=f"Approval required for {plan.provider} job: {reason}",
            reason=reason,
        )

    def _require_approval(self, action: str, scope: str, message: str, reason: str) -> None:
        approvals = [
            approval
            for approval in reversed(self.repo.list("approvals"))
            if approval.action == action
            and (
                approval.scope == scope
                or approval.scope_kind == "project"
                or (
                    approval.scope_kind == "prefix"
                    and approval.scope is not None
                    and scope.startswith(approval.scope)
                )
            )
        ]
        for approval in approvals:
            if approval.decision == ApprovalDecision.PENDING and approval.scope == scope:
                raise ApprovalRequiredError(approval.id, action, message)
            if approval.decision == ApprovalDecision.APPROVED:
                if approval.scope_kind in {"prefix", "project"}:
                    return
                if approval.scope == scope and approval.consumed_at is None:
                    approval.consumed_at = utcnow()
                    self.repo.update("approvals", approval)
                    return
            if approval.decision == ApprovalDecision.DENIED and approval.scope == scope:
                raise StateError(f"approval denied for {action} ({scope})")

        approval = self.repo.add(
            "approvals",
            Approval(action=action, scope=scope, reason=reason),
        )
        raise ApprovalRequiredError(approval.id, action, message)

    def _run_ref(self, run: Run) -> RunReference:
        return RunReference(
            provider=run.provider,
            provider_run_id=run.provider_run_id or "",
            run_id=run.id,
        )

    def _sync_run(self, run: Run) -> Run:
        """Reconcile the db record with the provider's run record."""
        provider = self.compute_providers.get(run.provider)
        if provider is None:
            return run
        try:
            status = provider.status(self._run_ref(run))
        except NotFoundError:
            return run
        changed = (
            run.status != status.status
            or run.exit_code != status.exit_code
            or run.finished_at != status.finished_at
        )
        if changed:
            run.status = status.status
            run.exit_code = status.exit_code
            run.started_at = status.started_at
            run.finished_at = status.finished_at
            self.repo.update("runs", run)
        if run.status == RunState.SUCCEEDED and not run.output_artifact_ids:
            self._collect_run_outputs(run, provider)
        return run

    def _collect_run_outputs(self, run: Run, provider) -> None:
        artifacts = provider.collect(self._run_ref(run))
        for artifact in artifacts:
            self.repo.add("artifacts", artifact)
            run.output_artifact_ids.append(artifact.id)
        self.repo.update("runs", run)

    def get_run(self, run_id: str) -> Run:
        run = self.repo.get("runs", run_id)
        return self._sync_run(run)

    def list_runs(self) -> list[Run]:
        return [self._sync_run(run) for run in self.repo.list("runs")]

    def run_logs(self, run_id: str, cursor: str | None = None):
        run = self.repo.get("runs", run_id)
        return self._compute_for(run.provider).logs(self._run_ref(run), cursor)

    def run_outputs(self, run_id: str) -> list[Artifact]:
        run = self.repo.get("runs", run_id)
        return [self.repo.get("artifacts", artifact_id) for artifact_id in run.output_artifact_ids]

    def cancel_run(self, run_id: str) -> Run:
        run = self.repo.get("runs", run_id)
        self._compute_for(run.provider).cancel(self._run_ref(run))
        return self._sync_run(run)

    # -- storage ------------------------------------------------------------------

    def _storage_for_uri(self, uri: str):
        scheme = uri.split("://", 1)[0] if "://" in uri else "file"
        provider = self.storage_providers.get(scheme)
        if provider is None:
            raise StateError(f"no configured storage provider for scheme: {scheme}")
        return provider

    def storage_ls(self, uri: str, cursor: str | None = None) -> ObjectPage:
        self._enforce_storage_approval(uri, "read")
        return self._storage_for_uri(uri).list(uri, cursor)

    def storage_stat(self, uri: str) -> ObjectMetadata:
        self._enforce_storage_approval(uri, "read")
        return self._storage_for_uri(uri).stat(uri)

    def storage_get(self, uri: str, dest: str) -> ObjectMetadata:
        self._enforce_storage_approval(uri, "read")
        self._enforce_storage_approval(dest, "write")
        with self._storage_for_uri(uri).open_read(uri) as src:
            return self._storage_for_uri(dest).put(dest, src, None)

    def storage_put(self, src_path: str, uri: str) -> ObjectMetadata:
        self._enforce_storage_approval(uri, "write")
        src = self.storage._resolve(src_path)  # same root policy applies
        if not src.is_file():
            raise NotFoundError(f"no such file: {src_path}")
        with open(src, "rb") as fh:
            return self._storage_for_uri(uri).put(uri, fh, None)

    def storage_copy(self, source: str, destination: str) -> ObjectMetadata:
        self._enforce_storage_approval(source, "read")
        self._enforce_storage_approval(destination, "write")
        with self._storage_for_uri(source).open_read(source) as src:
            return self._storage_for_uri(destination).put(destination, src, None)

    def storage_presign(self, uri: str, ttl_seconds: int = 900, write: bool = False) -> str:
        if ttl_seconds <= 0:
            raise StateError("presigned URL expiry must be positive")
        self._enforce_storage_approval(uri, "write" if write else "read")
        provider = self._storage_for_uri(uri)
        if write:
            return provider.presign_write(uri, ttl_seconds)
        return provider.presign_read(uri, ttl_seconds)

    def _enforce_storage_approval(self, uri: str, operation: str) -> None:
        scheme = uri.split("://", 1)[0] if "://" in uri else "file"
        if scheme == "file":
            return
        storage_policy = self.policies.get("storage") or {}
        if not isinstance(storage_policy, dict):
            raise StateError("policies.storage must be a mapping")
        config = storage_policy.get(scheme) or {}
        if not isinstance(config, dict):
            raise StateError(f"policies.storage.{scheme} must be a mapping")
        if config.get(f"allow_unapproved_{operation}", False):
            return
        scope = self._storage_approval_scope(uri, scheme)
        self._require_approval(
            action=f"storage.{operation}:{scheme}",
            scope=scope,
            message=f"Approval required to {operation} storage under {scope}",
            reason=f"{operation} access to remote storage prefix {scope}",
        )

    def _storage_approval_scope(self, uri: str, scheme: str) -> str:
        provider_configs = self.storage_config.get("providers") or {}
        config = provider_configs.get(scheme) if isinstance(provider_configs, dict) else None
        if config is None:
            config = self.storage_config.get(scheme)
        if not isinstance(config, dict):
            return uri
        if scheme == "s3":
            candidates = []
            for entry in config.get("allowed_prefixes") or []:
                prefix = f"s3://{str(entry).lstrip('/')}"
                if uri.startswith(prefix):
                    candidates.append(prefix)
            if candidates:
                return max(candidates, key=len)
            bucket = uri.removeprefix("s3://").partition("/")[0]
            if bucket in (config.get("allowed_buckets") or []):
                return f"s3://{bucket}/"
        return uri

    # -- artifacts -----------------------------------------------------------------

    def add_artifact(
        self,
        path: str,
        type: str = "file",
        mime: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        research_log_id: str | None = None,
        viewer: dict | None = None,
    ) -> Artifact:
        resolved = self.storage._resolve(path)
        if not resolved.is_file():
            raise NotFoundError(f"no such file: {path}")
        if task_id:
            self.repo.get("tasks", task_id)
        if run_id:
            self.repo.get("runs", run_id)
        if research_log_id:
            self.repo.get("research_log", research_log_id)
        rel = (
            resolved.relative_to(self.project_dir).as_posix()
            if resolved.is_relative_to(self.project_dir)
            else None
        )
        artifact = Artifact(
            path=rel,
            uri=f"file://{resolved}",
            type=type,
            mime=mime or mimetypes.guess_type(str(resolved))[0],
            size_bytes=resolved.stat().st_size,
            checksum_sha256=sha256_file(resolved),
            task_id=task_id,
            run_id=run_id,
            research_log_id=research_log_id,
            viewer=viewer or {},
        )
        return self.repo.add("artifacts", artifact)

    def get_artifact(self, artifact_id: str) -> Artifact:
        return self.repo.get("artifacts", artifact_id)

    def artifact_content_path(self, artifact_id: str) -> Path:
        artifact = self.get_artifact(artifact_id)
        if artifact.path is None:
            raise StateError(f"artifact {artifact_id} has no local project path")
        resolved = self.storage._resolve(artifact.path)
        if not resolved.is_file():
            raise NotFoundError(f"artifact file no longer exists: {artifact.path}")
        return resolved

    def list_artifacts(self) -> list[Artifact]:
        return self.repo.list("artifacts")

    # -- export (spec §20 "No lock-in") ----------------------------------------------

    def export(self) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = self.state_dir / "exports" / ts
        suffix = 1
        while out.exists():  # same-second exports must not collide
            out = self.state_dir / "exports" / f"{ts}-{suffix}"
            suffix += 1
        out.mkdir(parents=True, exist_ok=False)

        # 1. Open JSON schemas of the durable domain objects.
        schemas_dir = out / "schemas"
        schemas_dir.mkdir()
        for model in [*DOMAIN_MODELS, ExecutionSpec]:
            (schemas_dir / f"{model.__name__}.schema.json").write_text(
                json.dumps(model.model_json_schema(), indent=2, sort_keys=True)
            )

        # 2. Preserve source pins and small workspace settings without caches.
        configuration = []
        configuration_dir = out / "configuration"
        for filename in ("sources.yaml", "sources.lock.yaml"):
            source = self.state_dir / filename
            if source.is_file():
                configuration_dir.mkdir(exist_ok=True)
                shutil.copy2(source, configuration_dir / filename)
                configuration.append(filename)
        configuration_dir.mkdir(exist_ok=True)
        (configuration_dir / "settings.json").write_text(
            json.dumps(
                {
                    "enabled_skills": self._enabled_skills(),
                    "policies": self.policies,
                },
                indent=2,
                sort_keys=True,
            )
        )
        configuration.append("settings.json")

        # 3. All durable records as JSON.
        records_dir = out / "records"
        records_dir.mkdir()
        record_sets = {
            "tasks": self.repo.list("tasks"),
            "research_log": self.repo.list("research_log"),
            "reviews": self.repo.list("reviews"),
            "issues": self.repo.list("issues"),
            "runs": self.repo.list("runs"),
            "artifacts": self.repo.list("artifacts"),
            "approvals": self.repo.list("approvals"),
        }
        for name, objects in record_sets.items():
            (records_dir / f"{name}.json").write_text(
                json.dumps([o.model_dump(mode="json") for o in objects], indent=2, sort_keys=True)
            )

        # 4. run manifests (execution spec + command + environment hashes)
        manifests: dict[str, dict] = {}
        runs_dir = out / "runs"
        for run in record_sets["runs"]:
            manifest = {
                "run_id": run.id,
                "provider": run.provider,
                "spec_hash": run.spec_hash,
                "command": run.command,
                "status": run.status.value,
                "exit_code": run.exit_code,
                "execution_spec": run.execution_spec,
                "environment_hashes": self._environment_hashes(run),
            }
            manifests[run.id] = manifest
            run_out = runs_dir / run.id
            run_out.mkdir(parents=True, exist_ok=True)
            (run_out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
            source_run = self.state_dir / "runs" / run.id
            for filename in ("execution_spec.json", "run.json", "stdout.log", "stderr.log"):
                source = source_run / filename
                if source.is_file():
                    shutil.copy2(source, run_out / filename)

        # 5. copies of local artifacts with checksums
        artifacts_dir = out / "artifacts"
        artifact_entries = []
        for artifact in record_sets["artifacts"]:
            entry = {
                "id": artifact.id,
                "path": artifact.path,
                "uri": artifact.uri,
                "checksum_sha256": artifact.checksum_sha256,
                "copied": False,
            }
            if artifact.path:
                src = self.project_dir / artifact.path
                if src.is_file():
                    dest = artifacts_dir / artifact.path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    digest = sha256_file(dest)
                    if artifact.checksum_sha256 is None:
                        raise StateError(f"local artifact has no checksum: {artifact.id}")
                    if digest != artifact.checksum_sha256:
                        raise StateError(
                            f"artifact changed since registration: {artifact.id} ({artifact.path})"
                        )
                    entry["copied"] = True
                    entry["verified"] = True
            artifact_entries.append(entry)

        export_manifest = {
            "format_version": 2,
            "created_at": utcnow(),
            "workspace_name": self.workspace.name,
            "record_counts": {k: len(v) for k, v in record_sets.items()},
            "configuration": configuration,
            "artifacts": artifact_entries,
        }
        (out / "export_manifest.json").write_text(
            json.dumps(export_manifest, indent=2, sort_keys=True)
        )
        return out

    @classmethod
    def import_project(cls, export_dir: str | Path, project_dir: str | Path) -> ScienceService:
        """Restore an exported project into a new directory after integrity checks."""
        export_dir = Path(export_dir).resolve()
        project_dir = Path(project_dir).resolve()
        if not export_dir.is_dir():
            raise NotFoundError(f"export directory not found: {export_dir}")
        if project_dir.exists():
            raise StateError(f"import destination already exists: {project_dir}")

        manifest_file = export_dir / "export_manifest.json"
        if not manifest_file.is_file():
            raise StateError("invalid export: export_manifest.json is missing")
        manifest = json.loads(manifest_file.read_text())
        if not isinstance(manifest, dict) or manifest.get("format_version") != 2:
            raise StateError("invalid export: unsupported format version")

        record_models = {
            "tasks": Task,
            "research_log": ResearchLogEntry,
            "reviews": Review,
            "issues": Issue,
            "runs": Run,
            "artifacts": Artifact,
            "approvals": Approval,
        }
        records: dict[str, list] = {}
        expected_counts = manifest.get("record_counts")
        if not isinstance(expected_counts, dict):
            raise StateError("invalid export: record_counts is missing")
        for name, model in record_models.items():
            path = export_dir / "records" / f"{name}.json"
            if not path.is_file():
                raise StateError(f"invalid export: missing records/{name}.json")
            payload = json.loads(path.read_text())
            if not isinstance(payload, list):
                raise StateError(f"invalid export: records/{name}.json must contain a list")
            records[name] = [model.model_validate(item) for item in payload]
            if expected_counts.get(name) != len(records[name]):
                raise StateError(f"invalid export: record count mismatch for {name}")

        artifact_entries = manifest.get("artifacts")
        if not isinstance(artifact_entries, list):
            raise StateError("invalid export: artifact manifest is missing")
        entries_by_id = {
            entry.get("id"): entry for entry in artifact_entries if isinstance(entry, dict)
        }
        copied_artifacts: list[tuple[Artifact, Path]] = []
        for artifact in records["artifacts"]:
            entry = entries_by_id.get(artifact.id)
            if entry is None:
                raise StateError(f"invalid export: artifact manifest missing {artifact.id}")
            if not entry.get("copied"):
                continue
            if not artifact.path or not artifact.checksum_sha256:
                raise StateError(
                    f"invalid export: copied artifact {artifact.id} lacks path/checksum"
                )
            if entry.get("path") != artifact.path:
                raise StateError(f"invalid export: path mismatch for artifact {artifact.id}")
            relative = _safe_relative_path(artifact.path, f"artifact {artifact.id}")
            source = export_dir / "artifacts" / relative
            if not source.is_file():
                raise StateError(f"invalid export: copied artifact file missing: {artifact.path}")
            if sha256_file(source) != artifact.checksum_sha256:
                raise StateError(f"invalid export: checksum mismatch for artifact {artifact.id}")
            copied_artifacts.append((artifact, source))

        project_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = project_dir.parent / f".{project_dir.name}.import-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=False, exist_ok=False)
            for sub in PROJECT_DIRS:
                (staging / sub).mkdir()
            state_dir = staging / STATE_DIR_NAME
            (state_dir / "exports").mkdir(parents=True)
            (state_dir / "runs").mkdir()

            configuration = manifest.get("configuration", [])
            if not isinstance(configuration, list):
                raise StateError("invalid export: configuration must contain a list")
            configuration_dir = export_dir / "configuration"
            for filename in configuration:
                if filename not in {"sources.yaml", "sources.lock.yaml", "settings.json"}:
                    raise StateError(f"invalid export configuration file: {filename}")
                source = configuration_dir / filename
                if not source.is_file():
                    raise StateError(f"invalid export: configuration file missing: {filename}")
                if filename != "settings.json":
                    shutil.copy2(source, state_dir / filename)

            for artifact, source in copied_artifacts:
                relative = _safe_relative_path(artifact.path or "", f"artifact {artifact.id}")
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if sha256_file(destination) != artifact.checksum_sha256:
                    raise StateError(f"checksum verification failed while importing {artifact.id}")
                artifact.uri = f"file://{project_dir / relative}"

            source_runs = export_dir / "runs"
            for run in records["runs"]:
                source_run = source_runs / run.id
                if not source_run.is_dir():
                    continue
                destination_run = state_dir / "runs" / run.id
                destination_run.mkdir()
                for filename in (
                    "manifest.json",
                    "execution_spec.json",
                    "run.json",
                    "stdout.log",
                    "stderr.log",
                ):
                    source = source_run / filename
                    if source.is_file():
                        shutil.copy2(source, destination_run / filename)

            conn = project_db.connect(state_dir / STATE_DB_NAME)
            try:
                repo = Repository(conn)
                repo.migrate()
                for name in (
                    "tasks",
                    "runs",
                    "artifacts",
                    "research_log",
                    "reviews",
                    "issues",
                    "approvals",
                ):
                    for record in records[name]:
                        repo.add(name, record)
                settings_file = configuration_dir / "settings.json"
                if settings_file.is_file():
                    settings = json.loads(settings_file.read_text())
                    if not isinstance(settings, dict):
                        raise StateError("invalid export: settings.json must contain an object")
                    repo.set_setting("enabled_skills", settings.get("enabled_skills", []))
                    repo.set_setting("policies", settings.get("policies", {}))
            finally:
                conn.close()
            staging.replace(project_dir)
        except BaseException:
            try:
                if staging.exists():
                    shutil.rmtree(staging)
            except OSError as cleanup_error:
                raise StateError(
                    f"project import failed and staging cleanup also failed: {staging}"
                ) from cleanup_error
            raise
        return cls(project_dir)

    def _environment_hashes(self, run: Run) -> dict:
        hashes: dict[str, str] = {}
        env = (
            run.execution_spec.get("spec", {}).get("environment", {}) if run.execution_spec else {}
        )
        lockfile = env.get("lockfile")
        if lockfile:
            path = (self.project_dir / lockfile).resolve()
            if path.is_file() and path.is_relative_to(self.project_dir):
                hashes["lockfile_sha256"] = sha256_file(path)
                hashes["lockfile"] = lockfile
        return hashes


def _safe_relative_path(value: str, context: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise StateError(f"invalid relative path for {context}: {value}")
    return path
