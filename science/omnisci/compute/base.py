# SPDX-License-Identifier: Apache-2.0
"""Compute provider protocol (spec §12.2, verbatim shape) and execution spec.

The execution spec YAML format follows spec §12.1.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from omnisci.domain.schemas import Artifact, RunState

# ---------------------------------------------------------------------------
# Execution specification (spec §12.1)
# ---------------------------------------------------------------------------


class _Aliased(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ExecutionMetadata(_Aliased):
    name: str = "run"
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class SourceSpec(_Aliased):
    working_directory: str = Field(default=".", alias="workingDirectory")
    git_commit: str | None = Field(default=None, alias="gitCommit")


class GpuSpec(_Aliased):
    type: str
    count: int = 1


class ResourceSpec(_Aliased):
    cpu: int | None = None
    memory_gib: float | None = Field(default=None, alias="memoryGiB")
    gpu: GpuSpec | None = None


class EnvironmentSpec(_Aliased):
    image: str | None = None
    lockfile: str | None = None


class InputSpec(_Aliased):
    uri: str
    path: str | None = None
    read_only: bool = Field(default=True, alias="readOnly")


class OutputSpec(_Aliased):
    """``path`` may name a single file or a directory (all files under it are
    registered). ``files`` is a local-provider convenience extension for
    enumerating explicit output files."""

    path: str | None = None
    destination: str | None = None
    files: list[str] = Field(default_factory=list)


class LimitSpec(_Aliased):
    max_runtime_minutes: float | None = Field(default=None, alias="maxRuntimeMinutes")
    max_estimated_cost_usd: float | None = Field(default=None, alias="maxEstimatedCostUsd")


class NetworkSpec(_Aliased):
    mode: Literal["allow", "deny"] = "allow"


class ExecutionDetails(_Aliased):
    provider: str = "local"
    mode: str = "subprocess"
    source: SourceSpec = Field(default_factory=SourceSpec)
    command: list[str]
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    resources: ResourceSpec | None = None
    inputs: list[InputSpec] = Field(default_factory=list)
    outputs: OutputSpec | None = None
    limits: LimitSpec = Field(default_factory=LimitSpec)
    network: NetworkSpec = Field(default_factory=NetworkSpec)


class ExecutionSpec(_Aliased):
    api_version: str = Field(alias="apiVersion")
    kind: str
    metadata: ExecutionMetadata = Field(default_factory=ExecutionMetadata)
    spec: ExecutionDetails

    @classmethod
    def from_yaml(cls, text: str) -> ExecutionSpec:
        return cls.model_validate(yaml.safe_load(text))

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(by_alias=True, mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def spec_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


# ---------------------------------------------------------------------------
# Provider contract types
# ---------------------------------------------------------------------------


class ProviderCapabilities(BaseModel):
    provider: str
    modes: list[str] = Field(default_factory=list)
    supports_cancel: bool = False
    supports_logs: bool = False
    resources: dict = Field(default_factory=dict)
    extensions: dict[str, dict] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    provider: str
    spec: ExecutionSpec
    spec_hash: str
    idempotency_key: str
    working_directory: str  # resolved absolute path
    command: list[str]
    resolved_outputs: list[str] = Field(default_factory=list)


class RunReference(BaseModel):
    provider: str
    provider_run_id: str
    run_id: str


class RunStatus(BaseModel):
    run_id: str
    status: RunState
    exit_code: int | None = None
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class LogPage(BaseModel):
    content: str = ""
    stderr: str = ""
    next_cursor: str | None = None


class ComputeProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def validate(self, spec: ExecutionSpec) -> ExecutionPlan: ...
    def submit(self, plan: ExecutionPlan) -> RunReference: ...
    def status(self, run: RunReference) -> RunStatus: ...
    def logs(self, run: RunReference, cursor: str | None) -> LogPage: ...
    def cancel(self, run: RunReference) -> None: ...
    def collect(self, run: RunReference) -> list[Artifact]: ...
