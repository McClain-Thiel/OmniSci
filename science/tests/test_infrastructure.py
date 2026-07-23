# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import stat

import pytest
import yaml
from omnisci.domain import db as project_db
from omnisci.errors import StateError
from omnisci.infrastructure import InfrastructureConfigStore
from omnisci.service import ScienceService


def test_app_infrastructure_is_persisted_outside_projects(tmp_path):
    path = tmp_path / "app" / "infrastructure.yaml"
    store = InfrastructureConfigStore(path)
    compute_config = {
        "default_provider": "slurm",
        "providers": {
            "ssh": {
                "host": "cluster.example.edu",
                "user": "researcher",
                "known_hosts_file": "~/.ssh/known_hosts",
            },
            "slurm": {
                "transport_ref": "ssh",
                "partition": "gpu",
            },
        },
    }

    saved = store.update(compute_config=compute_config)

    assert saved["compute_config"] == compute_config
    assert store.load()["compute_config"] == compute_config
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.read_text())["version"] == 1


def test_app_infrastructure_rejects_credentials_and_missing_transport(tmp_path):
    store = InfrastructureConfigStore(tmp_path / "infrastructure.yaml")

    with pytest.raises(StateError, match="inline credentials"):
        store.update(
            storage_config={
                "default_provider": "s3",
                "providers": {"s3": {"access_key": "must-not-persist"}},
            }
        )
    with pytest.raises(StateError, match="references missing transport"):
        store.update(
            compute_config={
                "default_provider": "slurm",
                "providers": {"slurm": {"transport_ref": "ssh"}},
            }
        )
    assert not store.path.exists()


def test_projects_use_only_app_connectors(tmp_path, monkeypatch):
    path = tmp_path / "infrastructure.yaml"
    monkeypatch.setenv("OMNISCI_INFRASTRUCTURE_CONFIG", str(path))
    InfrastructureConfigStore().update(
        compute_config={
            "default_provider": "batch",
            "providers": {"batch": {"queue": "app"}},
        }
    )

    class BatchProvider:
        def __init__(self, *, config, **_kwargs):
            self.config = config

        def capabilities(self):
            from omnisci.compute.base import ProviderCapabilities

            return ProviderCapabilities(provider="batch")

    from omnisci.compute import registry

    monkeypatch.setattr(registry, "load_provider_factories", lambda: {"batch": BatchProvider})
    first = ScienceService.init_project(tmp_path / "first")
    second = ScienceService.init_project(tmp_path / "second")

    assert first.compute_providers["batch"].config == {"queue": "app"}
    assert second.compute_providers["batch"].config == {"queue": "app"}

    assert not (first.state_dir / "project.yaml").exists()
    reloaded = ScienceService(first.project_dir)
    assert reloaded.compute_providers["batch"].config == {"queue": "app"}
    assert "compute_config" not in reloaded.workspace.model_dump()
    assert "storage_config" not in reloaded.workspace.model_dump()


def test_legacy_workspace_imports_manifest_settings_once(tmp_path):
    project = tmp_path / "legacy"
    state_dir = project / ".science"
    state_dir.mkdir(parents=True)
    conn = project_db.connect(state_dir / "project.db")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for statement in project_db.MIGRATIONS[1]:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
        ("2026-07-23T00:00:00+00:00",),
    )
    conn.execute(
        "INSERT INTO checkpoints (id, created_at, data) VALUES (?, ?, ?)",
        (
            "ckpt_legacy",
            "2026-07-23T00:00:00+00:00",
            '{"id":"ckpt_legacy","summary":"legacy result",'
            '"created_at":"2026-07-23T00:00:00+00:00"}',
        ),
    )
    conn.commit()
    conn.close()
    manifest = state_dir / "project.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "id": "project_legacy",
                "name": "Old project name",
                "policies": {"compute": {"local": {"allow_unapproved_subprocess": True}}},
                "enabled_skills": ["scanpy"],
            }
        )
    )

    service = ScienceService(project)

    assert service.workspace.name == "legacy"
    assert service.state_dir == state_dir
    assert service.policies["compute"]["local"]["allow_unapproved_subprocess"] is True
    assert service._enabled_skills() == ["scanpy"]
    assert service.list_research_log()[0].summary == "legacy result"

    manifest.unlink()
    reloaded = ScienceService(project)
    assert reloaded.policies == service.policies
    assert reloaded._enabled_skills() == ["scanpy"]
