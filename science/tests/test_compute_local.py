# SPDX-License-Identifier: Apache-2.0
"""Local compute provider unit tests (spec §12.3, §20)."""

from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest
from omnisci.compute import registry
from omnisci.compute.base import (
    ExecutionPlan,
    LogPage,
    ProviderCapabilities,
    RunReference,
    RunStatus,
)
from omnisci.compute.local import LocalComputeProvider, local_sandbox_available
from omnisci.domain.schemas import ApprovalDecision, Artifact, Run, RunState
from omnisci.errors import ApprovalRequiredError, StateError
from omnisci.infrastructure import InfrastructureConfigStore
from omnisci.service import ScienceService

from tests.conftest import make_spec, py


@pytest.fixture
def provider(tmp_path):
    (tmp_path / "analyses").mkdir()
    (tmp_path / "figures").mkdir()
    return LocalComputeProvider(tmp_path)


def test_validate_spec_hash_and_idempotency(provider):
    spec = make_spec(py("print('hi')"))
    plan = provider.validate(spec)
    assert len(plan.spec_hash) == 64
    assert plan.idempotency_key == plan.spec_hash  # default key = spec hash
    # stable across validations
    assert provider.validate(spec).spec_hash == plan.spec_hash


def test_compute_registry_uses_prd_entry_point_group(monkeypatch):
    class FakeEntryPoint:
        name = "batch"

        def load(self):
            return object

    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: [FakeEntryPoint()])

    assert registry.ENTRY_POINT_GROUP == "omnigent_science.compute"
    assert registry.load_provider_factories() == {"batch": object}


def test_service_routes_configured_entry_point_provider(tmp_path, monkeypatch):
    class BatchProvider:
        def __init__(self, *, project_dir, runs_dir, config):
            self.project_dir = project_dir
            self.runs_dir = runs_dir
            assert config == {"queue": "test"}

        def capabilities(self):
            return ProviderCapabilities(provider="batch", modes=["sandbox"])

        def validate(self, spec):
            return ExecutionPlan(
                provider="batch",
                spec=spec,
                spec_hash=spec.spec_hash(),
                idempotency_key=spec.spec_hash(),
                working_directory=str(self.project_dir),
                command=spec.spec.command,
            )

        def submit(self, _plan):
            return RunReference(provider="batch", provider_run_id="remote-1", run_id="run_batch")

        def status(self, run):
            return RunStatus(run_id=run.run_id, status=RunState.SUCCEEDED, exit_code=0)

        def logs(self, _run, _cursor):
            return LogPage(content="remote output")

        def cancel(self, _run):
            raise AssertionError("terminal test run must not be cancelled")

        def collect(self, _run):
            return []

    project_dir = tmp_path / "entry-point-project"
    base = ScienceService.init_project(project_dir)
    InfrastructureConfigStore().update(
        compute_config={
            "default_provider": "batch",
            "providers": {"batch": {"queue": "test"}},
        }
    )
    base.set_policies(
        {
            "compute": {
                "batch": {
                    "allow_unapproved_submit": True,
                    "allow_unapproved_network": True,
                }
            }
        }
    )
    monkeypatch.setattr(registry, "load_provider_factories", lambda: {"batch": BatchProvider})

    service = ScienceService(project_dir)
    spec = make_spec(py("print('remote')"), provider="batch", mode="sandbox")
    run, deduplicated = service.submit_job(spec)

    assert not deduplicated
    assert run.provider == "batch"
    assert run.provider_run_id == "remote-1"
    assert service.run_logs(run.id).content == "remote output"


def test_service_resolves_shared_compute_transport(tmp_path, monkeypatch):
    class SchedulerProvider:
        def __init__(self, *, config, **_kwargs):
            assert config == {
                "host": "cluster.example.edu",
                "user": "researcher",
                "partition": "gpu",
            }

        def capabilities(self):
            return ProviderCapabilities(provider="slurm", modes=["batch"])

    class TransportProvider:
        def __init__(self, **_kwargs):
            pass

        def capabilities(self):
            return ProviderCapabilities(provider="ssh", modes=["ssh"])

    project_dir = tmp_path / "transport-project"
    ScienceService.init_project(project_dir)
    InfrastructureConfigStore().update(
        compute_config={
            "default_provider": "slurm",
            "providers": {
                "ssh": {"host": "cluster.example.edu", "user": "researcher"},
                "slurm": {"transport_ref": "ssh", "partition": "gpu"},
            },
        }
    )
    monkeypatch.setattr(
        registry,
        "load_provider_factories",
        lambda: {"ssh": TransportProvider, "slurm": SchedulerProvider},
    )

    service = ScienceService(project_dir)

    assert set(service.compute_providers) == {"local", "ssh", "slurm"}


def test_service_rejects_missing_compute_transport():
    with pytest.raises(StateError, match="references missing transport"):
        ScienceService._resolve_compute_transport(
            "slurm",
            {"transport_ref": "missing", "partition": "gpu"},
            {"slurm": {"transport_ref": "missing", "partition": "gpu"}},
        )


def test_list_runs_reconciles_async_provider_and_collects_outputs(tmp_path, monkeypatch):
    class AsyncProvider:
        status_calls = 0

        def __init__(self, *, project_dir, **_kwargs):
            self.project_dir = project_dir

        def bind_storage(self, resolver):
            self.storage_resolver = resolver

        def capabilities(self):
            return ProviderCapabilities(provider="async-test", modes=["sandbox"])

        def validate(self, spec):
            return ExecutionPlan(
                provider="async-test",
                spec=spec,
                spec_hash=spec.spec_hash(),
                idempotency_key=spec.spec_hash(),
                working_directory=str(self.project_dir),
                command=spec.spec.command,
            )

        def submit(self, _plan):
            return RunReference(
                provider="async-test", provider_run_id="remote-async", run_id="run_async"
            )

        def status(self, run):
            self.status_calls += 1
            state = RunState.RUNNING if self.status_calls == 1 else RunState.SUCCEEDED
            return RunStatus(
                run_id=run.run_id,
                status=state,
                exit_code=0 if state == RunState.SUCCEEDED else None,
            )

        def logs(self, _run, _cursor):
            return LogPage(content="async output")

        def cancel(self, _run):
            raise AssertionError("test run completes during reconciliation")

        def collect(self, run):
            return [
                Artifact(
                    path="results/async.json",
                    uri=f"file://{self.project_dir}/results/async.json",
                    run_id=run.run_id,
                )
            ]

    project_dir = tmp_path / "async-project"
    base = ScienceService.init_project(project_dir)
    InfrastructureConfigStore().update(
        compute_config={
            "default_provider": "async-test",
            "providers": {"async-test": {}},
        }
    )
    base.set_policies(
        {
            "compute": {
                "async-test": {
                    "allow_unapproved_submit": True,
                    "allow_unapproved_network": True,
                }
            }
        }
    )
    monkeypatch.setattr(registry, "load_provider_factories", lambda: {"async-test": AsyncProvider})

    service = ScienceService(project_dir)
    spec = make_spec(py("print('remote')"), provider="async-test", mode="sandbox")
    submitted, _ = service.submit_job(spec)
    assert submitted.status == RunState.RUNNING

    reconciled = service.list_runs()[0]
    assert reconciled.status == RunState.SUCCEEDED
    assert len(reconciled.output_artifact_ids) == 1


def test_list_runs_keeps_history_when_provider_is_no_longer_configured(service):
    historical = service.repo.add(
        "runs",
        Run(
            provider="qsub",
            provider_run_id="123.fixture",
            status=RunState.SUCCEEDED,
            exit_code=0,
        ),
    )

    assert service.list_runs() == [historical]
    assert service.get_run(historical.id) == historical


def test_validate_rejects_other_provider(provider):
    spec = make_spec(py("print('hi')"), provider="modal")
    with pytest.raises(StateError, match="modal"):
        provider.validate(spec)


def test_validate_rejects_missing_workdir(provider):
    spec = make_spec(py("print('hi')"), working_directory="nope")
    with pytest.raises(StateError, match="does not exist"):
        provider.validate(spec)


def test_validate_rejects_outside_workdir(provider):
    spec = make_spec(py("print('hi')"), working_directory="..")
    with pytest.raises(StateError, match="outside project"):
        provider.validate(spec)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("environment", {"image": "python:3.12"}, "container image"),
        ("resources", {"gpu": {"type": "L4", "count": 1}}, "GPU"),
        ("inputs", [{"uri": "s3://bucket/input.csv"}], "does not stage"),
        (
            "outputs",
            {"path": "results", "destination": "s3://bucket/results/"},
            "does not upload",
        ),
    ],
)
def test_validate_rejects_unsupported_execution_features(provider, field, value, message):
    spec = make_spec(py("print('hi')"))
    payload = spec.model_dump(by_alias=True, mode="json")
    payload["spec"][field] = value
    with pytest.raises(StateError, match=message):
        provider.validate(type(spec).model_validate(payload))


def test_submit_success_and_collect(provider):
    code = (
        "from pathlib import Path; "
        "Path('figures/out.csv').write_text('a,b\\n1,2\\n'); "
        "print('done-marker')"
    )
    spec = make_spec(py(code), output_files=["figures/out.csv"])
    plan = provider.validate(spec)
    ref = provider.submit(plan)

    status = provider.status(ref)
    assert status.status == RunState.SUCCEEDED
    assert status.exit_code == 0
    assert status.started_at and status.finished_at

    logs = provider.logs(ref, None)
    assert "done-marker" in logs.content

    artifacts = provider.collect(ref)
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.path == "figures/out.csv"
    expected = hashlib.sha256((provider.project_dir / "figures/out.csv").read_bytes())
    assert art.checksum_sha256 == expected.hexdigest()
    assert art.run_id == ref.run_id


def test_submit_failure_records_exit_code(provider):
    spec = make_spec(py("import sys; sys.stderr.write('boom\\n'); sys.exit(3)"))
    ref = provider.submit(provider.validate(spec))
    status = provider.status(ref)
    assert status.status == RunState.FAILED
    assert status.exit_code == 3
    assert "boom" in provider.logs(ref, None).stderr


def test_timeout_enforced(provider):
    spec = make_spec(
        py("import time; time.sleep(30)"),
        max_runtime_minutes=0.01,  # 0.6 s
    )
    ref = provider.submit(provider.validate(spec))
    status = provider.status(ref)
    assert status.status == RunState.TIMEOUT
    assert status.exit_code is None


def test_cancel_running_process(provider):
    spec = make_spec(py("import time; time.sleep(60)"))
    plan = provider.validate(spec)

    ref_holder: dict = {}

    def run():
        ref_holder["ref"] = provider.submit(plan)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        # wait until the run record shows a running process
        record = None
        for _ in range(200):
            if provider.runs_dir.exists():
                dirs = [d for d in provider.runs_dir.iterdir() if d.is_dir()]
                if dirs:
                    candidate = json.loads((dirs[0] / "run.json").read_text())
                    if candidate["status"] == "running":
                        record = candidate
                        break
            time.sleep(0.05)
        assert record is not None, "run never reached running state"

        ref = RunReference(provider="local", provider_run_id="", run_id=record["run_id"])
        provider.cancel(ref)
        thread.join(timeout=30)

        status = provider.status(ref)
        assert status.status == RunState.CANCELLED

        # cancel on a terminal run is an error
        with pytest.raises(StateError, match="already terminal"):
            provider.cancel(ref)
    finally:
        if thread.is_alive():
            thread.join(timeout=5)


def test_idempotent_double_submit(service):
    """Spec §20: double submission with the same idempotency key must not
    create duplicate work."""
    (service.project_dir / "figures").mkdir(exist_ok=True)
    code = "from pathlib import Path; Path('figures/idem.txt').write_text('x')"
    spec = make_spec(py(code), output_files=["figures/idem.txt"])

    run1, dedup1 = service.submit_job(spec)
    run2, dedup2 = service.submit_job(spec)

    assert not dedup1
    assert dedup2
    assert run1.id == run2.id
    assert len(service.list_runs()) == 1
    assert len(service.list_artifacts()) == 1  # not re-registered


def test_unsafe_local_job_requires_and_consumes_one_time_approval(tmp_path):
    service = ScienceService.init_project(tmp_path / "approval-proj")
    spec = make_spec(py("print('approved')"))

    with pytest.raises(ApprovalRequiredError) as first:
        service.submit_job(spec)
    with pytest.raises(ApprovalRequiredError) as repeated:
        service.submit_job(spec)

    assert repeated.value.approval_id == first.value.approval_id
    approval = service.get_approval(first.value.approval_id)
    assert approval.decision == ApprovalDecision.PENDING
    assert "unsandboxed subprocess" in (approval.reason or "")
    assert "network" in (approval.reason or "")

    service.resolve_approval(approval.id, "approved", actor="researcher")
    run, deduplicated = service.submit_job(spec)

    assert not deduplicated
    assert run.status == RunState.SUCCEEDED
    assert service.get_approval(approval.id).consumed_at is not None

    repeated_run, deduplicated = service.submit_job(spec)
    assert deduplicated
    assert repeated_run.id == run.id


def test_capabilities_disclose_local_isolation(provider):
    limits = provider.capabilities().extensions["omnisci.local"]
    assert limits["sandbox_available"] is local_sandbox_available()
    assert limits["network_policy_enforced_in_sandbox"] is local_sandbox_available()


def test_plain_subprocess_rejects_network_deny(provider):
    spec = make_spec(py("print('hi')"), mode="subprocess", network_mode="deny")
    with pytest.raises(StateError, match="requires mode=sandbox"):
        provider.validate(spec)


@pytest.mark.skipif(not local_sandbox_available(), reason="platform sandbox is unavailable")
def test_sandbox_blocks_undeclared_write(provider):
    spec = make_spec(
        py("from pathlib import Path; Path('undeclared.txt').write_text('no')"),
        mode="sandbox",
        network_mode="deny",
    )
    ref = provider.submit(provider.validate(spec))
    assert provider.status(ref).status == RunState.FAILED
    assert not (provider.project_dir / "undeclared.txt").exists()


@pytest.mark.skipif(not local_sandbox_available(), reason="platform sandbox is unavailable")
def test_sandbox_allows_declared_output(provider):
    spec = make_spec(
        py("from pathlib import Path; Path('figures/sandboxed.txt').write_text('ok')"),
        output_files=["figures/sandboxed.txt"],
        mode="sandbox",
        network_mode="deny",
    )
    ref = provider.submit(provider.validate(spec))
    assert provider.status(ref).status == RunState.SUCCEEDED, provider.logs(ref, None).stderr
    assert (provider.project_dir / "figures" / "sandboxed.txt").read_text() == "ok"
