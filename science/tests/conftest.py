# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys

import pytest
from omnisci.compute.base import ExecutionSpec
from omnisci.service import ScienceService


@pytest.fixture(autouse=True)
def isolate_app_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OMNISCI_INFRASTRUCTURE_CONFIG",
        str(tmp_path / "app-config" / "infrastructure.yaml"),
    )


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path / "proj"


@pytest.fixture
def service(project_dir):
    service = ScienceService.init_project(project_dir, research_goal="test goal")
    return allow_unapproved_local_compute(service)


def allow_unapproved_local_compute(service: ScienceService) -> ScienceService:
    """Keep general tests focused on their subject rather than approval setup."""
    service.set_policies(
        {
            "compute": {
                "local": {
                    "allow_unapproved_subprocess": True,
                    "allow_unapproved_network": True,
                }
            }
        }
    )
    return service


def make_spec(
    command: list[str],
    *,
    working_directory: str = ".",
    output_path: str | None = None,
    output_files: list[str] | None = None,
    max_runtime_minutes: float | None = None,
    name: str = "test-run",
    provider: str = "local",
    mode: str = "subprocess",
    network_mode: str = "allow",
) -> ExecutionSpec:
    outputs = None
    if output_path or output_files:
        outputs = {}
        if output_path:
            outputs["path"] = output_path
        if output_files:
            outputs["files"] = output_files
    return ExecutionSpec.model_validate(
        {
            "apiVersion": "science.omnigent.ai/v1alpha1",
            "kind": "Execution",
            "metadata": {"name": name},
            "spec": {
                "provider": provider,
                "mode": mode,
                "source": {"workingDirectory": working_directory},
                "command": command,
                "environment": {"image": "local"},
                "outputs": outputs,
                "limits": {"maxRuntimeMinutes": max_runtime_minutes},
                "network": {"mode": network_mode},
            },
        }
    )


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]
