# SPDX-License-Identifier: Apache-2.0
"""Local subprocess compute provider (spec §12.3).

Executes the declared command via ``subprocess`` in the spec's working
directory, captures stdout/stderr to log files under
``.omnisci/runs/<run_id>/``, enforces the runtime limit with a process-group
kill, and registers declared output files as artifacts with SHA-256
checksums.

``submit`` is synchronous: the run reaches a terminal state before it
returns. Per-run state is kept in ``.omnisci/runs/<run_id>/run.json`` so
``status``/``logs``/``cancel``/``collect`` work from any process.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import signal
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from omnisci.compute.base import (
    ExecutionPlan,
    ExecutionSpec,
    LogPage,
    ProviderCapabilities,
    ProviderCheck,
    RunReference,
    RunStatus,
)
from omnisci.domain.schemas import Artifact, RunState, new_id, utcnow
from omnisci.errors import NotFoundError, StateError
from omnisci.storage.local import sha256_file

DEFAULT_TIMEOUT_MINUTES = 60.0
TERMINAL_STATES = {
    RunState.SUCCEEDED.value,
    RunState.FAILED.value,
    RunState.CANCELLED.value,
    RunState.TIMEOUT.value,
}

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def local_sandbox_available() -> bool:
    """Whether OmniGent's platform sandbox can be enforced on this host."""
    try:
        import omnigent.inner.sandbox  # noqa: F401
    except ImportError:
        return False
    if sys.platform == "darwin":
        executable = shutil.which("sandbox-exec")
        command = (
            [executable, "-p", "(version 1)(allow default)", "/usr/bin/true"]
            if executable
            else None
        )
    if sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        command = (
            [
                executable,
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--",
                "/bin/true",
            ]
            if executable
            else None
        )
    elif sys.platform != "darwin":
        command = None
    if command is None:
        return False
    try:
        probe = subprocess.run(command, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


class LocalComputeProvider:
    name = "local"

    def __init__(self, project_dir: Path, runs_dir: Path | None = None):
        self.project_dir = Path(project_dir).resolve()
        self.runs_dir = Path(runs_dir) if runs_dir else self.project_dir / ".omnisci" / "runs"

    # -- helpers --------------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def _record_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def _read_record(self, run_id: str) -> dict:
        path = self._record_path(run_id)
        if not path.exists():
            raise NotFoundError(f"run not found: {run_id}")
        return json.loads(path.read_text())

    def _write_record(self, run_id: str, record: dict) -> None:
        self._record_path(run_id).write_text(json.dumps(record, indent=2, sort_keys=True))

    # -- protocol ---------------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        sandbox_available = local_sandbox_available()
        return ProviderCapabilities(
            provider="local",
            modes=["subprocess", *(["sandbox"] if sandbox_available else [])],
            supports_cancel=True,
            supports_logs=True,
            resources={"cpu": os.cpu_count() or 1, "gpu": 0},
            extensions={
                "omnisci.local": {
                    "sandbox_available": sandbox_available,
                    "network_policy_enforced_in_sandbox": sandbox_available,
                    "synchronous": True,
                }
            },
        )

    def check(self) -> ProviderCheck:
        """Local execution has nothing to reach, so this only reports sandbox availability."""
        sandboxed = local_sandbox_available()
        return ProviderCheck(
            provider=self.name,
            status="ok",
            detail="local process execution available"
            + ("" if sandboxed else " (no OS sandbox on this host)"),
            remedy=""
            if sandboxed
            else "Install sandbox-exec (macOS) or bwrap (Linux) to run jobs with mode=sandbox.",
            checked_at=utcnow(),
            observed={"sandbox_available": sandboxed, "cpu_count": os.cpu_count() or 1},
        )

    def validate(self, spec: ExecutionSpec) -> ExecutionPlan:
        details = spec.spec
        if details.provider != self.name:
            raise StateError(
                f"local provider cannot execute spec for provider '{details.provider}'"
            )
        if not details.command:
            raise StateError("execution spec has an empty command")
        if details.mode not in {"sandbox", "subprocess"}:
            raise StateError(f"local provider does not support execution mode '{details.mode}'")
        if details.mode == "sandbox" and not local_sandbox_available():
            raise StateError(
                "sandbox mode requires OmniGent's platform sandbox "
                "(sandbox-exec on macOS or bwrap on Linux)"
            )
        if details.mode == "subprocess" and details.network.mode == "deny":
            raise StateError(
                "network.mode=deny requires mode=sandbox; plain subprocess mode "
                "cannot enforce network isolation"
            )
        if details.environment.image not in {None, "local"}:
            raise StateError(
                "local provider cannot enforce container image "
                f"'{details.environment.image}'; use image 'local' or omit it"
            )
        if details.resources and details.resources.gpu is not None:
            raise StateError("local provider does not advertise GPU execution")
        if details.inputs:
            raise StateError(
                "local provider does not stage execution-spec inputs; "
                "stage them with 'science storage stage' first"
            )
        if details.outputs is not None and details.outputs.destination:
            raise StateError(
                "local provider does not upload output destinations; "
                "collect locally and use 'science storage put'"
            )
        wd = (self.project_dir / details.source.working_directory).resolve()
        if not (wd == self.project_dir or wd.is_relative_to(self.project_dir)):
            raise StateError(
                f"working directory outside project: {details.source.working_directory}"
            )
        if not wd.is_dir():
            raise StateError(f"working directory does not exist: {wd}")

        resolved_outputs: list[str] = []
        if details.outputs is not None:
            candidates = list(details.outputs.files)
            if details.outputs.path:
                candidates.append(details.outputs.path)
            for rel in candidates:
                rp = (self.project_dir / rel).resolve()
                if not (rp == self.project_dir or rp.is_relative_to(self.project_dir)):
                    raise StateError(f"declared output outside project: {rel}")
                resolved_outputs.append(rel)

        spec_hash = spec.spec_hash()
        idempotency_key = spec.metadata.idempotency_key or spec_hash
        return ExecutionPlan(
            provider=self.name,
            spec=spec,
            spec_hash=spec_hash,
            idempotency_key=idempotency_key,
            working_directory=str(wd),
            command=list(details.command),
            resolved_outputs=resolved_outputs,
        )

    def submit(self, plan: ExecutionPlan) -> RunReference:
        run_id = new_id("run")
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)

        details = plan.spec.spec
        record = {
            "run_id": run_id,
            "provider": self.name,
            "spec_hash": plan.spec_hash,
            "idempotency_key": plan.idempotency_key,
            "command": plan.command,
            "working_directory": plan.working_directory,
            "status": RunState.QUEUED.value,
            "queued_at": utcnow(),
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "pid": None,
            "network_mode": details.network.mode,
            "sandbox_enforced": details.mode == "sandbox",
        }
        self._write_record(run_id, record)
        (run_dir / "execution_spec.json").write_text(plan.spec.canonical_json())

        timeout_s = (
            details.limits.max_runtime_minutes
            if details.limits.max_runtime_minutes is not None
            else DEFAULT_TIMEOUT_MINUTES
        ) * 60.0

        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
            command, launcher, env = self._launch_command(plan)
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=plan.working_directory,
                    env=env,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,  # own process group for cancel/timeout kill
                )
            except BaseException:
                if launcher is not None:
                    launcher.unlink(missing_ok=True)
                raise
            record.update(status=RunState.RUNNING.value, pid=proc.pid, started_at=utcnow())
            self._write_record(run_id, record)
            try:
                try:
                    exit_code = proc.wait(timeout=timeout_s)
                    # A concurrent cancel() may have already terminated the process.
                    latest = self._read_record(run_id)
                    if latest["status"] == RunState.CANCELLED.value:
                        status = RunState.CANCELLED.value
                    else:
                        status = (
                            RunState.SUCCEEDED.value if exit_code == 0 else RunState.FAILED.value
                        )
                except subprocess.TimeoutExpired:
                    _kill_process_group(proc)
                    proc.wait()
                    exit_code = None
                    status = RunState.TIMEOUT.value
            finally:
                if launcher is not None:
                    launcher.unlink(missing_ok=True)

        record.update(status=status, exit_code=exit_code, finished_at=utcnow())
        self._write_record(run_id, record)
        return RunReference(provider=self.name, provider_run_id=str(proc.pid), run_id=run_id)

    def _launch_command(
        self, plan: ExecutionPlan
    ) -> tuple[list[str], Path | None, dict[str, str] | None]:
        if plan.spec.spec.mode == "subprocess":
            return plan.command, None, None

        import omnigent
        from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec
        from omnigent.inner.sandbox import (
            create_exec_launcher,
            resolve_sandbox,
            with_spawn_env_allowlist,
        )

        backend = "darwin_seatbelt" if sys.platform == "darwin" else "linux_bwrap"
        write_paths = self._sandbox_write_paths(plan)
        sandbox = OSEnvSandboxSpec(
            type=backend,
            # The generated launcher imports OmniGent again after the OS-level
            # re-exec. Editable installs can place that package outside the
            # science project, so grant only the package tree it bootstraps from.
            read_paths=[str(Path(omnigent.__file__).resolve().parent)],
            write_paths=write_paths,
            allow_network=plan.spec.spec.network.mode == "allow",
            cwd_hidden_scan_overflow="error",
        )
        policy = resolve_sandbox(
            OSEnvSpec(cwd=str(self.project_dir), sandbox=sandbox), self.project_dir
        )
        env = _sandbox_environment()
        policy = with_spawn_env_allowlist(policy, env)
        executable = _resolve_executable(plan.command[0], Path(plan.working_directory))
        launcher = Path(create_exec_launcher(str(executable), policy))
        return [str(launcher), *plan.command[1:]], launcher, env

    def _sandbox_write_paths(self, plan: ExecutionPlan) -> list[str]:
        roots = set()
        for output in plan.resolved_outputs:
            path = Path(output)
            parent = path if (self.project_dir / path).is_dir() else path.parent
            roots.add(parent.as_posix() or ".")
        return sorted(roots)

    def status(self, run: RunReference) -> RunStatus:
        record = self._read_record(run.run_id)
        return RunStatus(
            run_id=run.run_id,
            status=RunState(record["status"]),
            exit_code=record.get("exit_code"),
            queued_at=record.get("queued_at"),
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
        )

    def logs(self, run: RunReference, _cursor: str | None = None) -> LogPage:
        run_dir = self._run_dir(run.run_id)
        if not run_dir.is_dir():
            raise NotFoundError(f"run not found: {run.run_id}")
        stdout = (run_dir / "stdout.log").read_text(errors="replace")
        stderr = (run_dir / "stderr.log").read_text(errors="replace")
        # The local provider returns full logs in a single page.
        return LogPage(content=stdout, stderr=stderr, next_cursor=None)

    def cancel(self, run: RunReference) -> None:
        record = self._read_record(run.run_id)
        if record["status"] in TERMINAL_STATES:
            raise StateError(
                f"run {run.run_id} already terminal ({record['status']}); cannot cancel"
            )
        pid = record.get("pid")
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                log.debug("run process already exited before cancellation: pid=%s", pid)
        record.update(status=RunState.CANCELLED.value, finished_at=utcnow())
        self._write_record(run.run_id, record)

    def collect(self, run: RunReference) -> list[Artifact]:
        record = self._read_record(run.run_id)
        spec_path = self._run_dir(run.run_id) / "execution_spec.json"
        spec = ExecutionSpec.model_validate(json.loads(spec_path.read_text()))
        details = spec.spec

        targets: list[Path] = []
        if details.outputs is not None:
            candidates = list(details.outputs.files)
            if details.outputs.path:
                candidates.append(details.outputs.path)
            for rel in candidates:
                p = (self.project_dir / rel).resolve()
                if p.is_dir():
                    targets.extend(sorted(f for f in p.rglob("*") if f.is_file()))
                elif p.is_file():
                    targets.append(p)

        artifacts: list[Artifact] = []
        for path in targets:
            rel = path.relative_to(self.project_dir).as_posix()
            artifacts.append(
                Artifact(
                    path=rel,
                    uri=f"file://{path}",
                    type="result",
                    mime=mimetypes.guess_type(str(path))[0],
                    size_bytes=path.stat().st_size,
                    checksum_sha256=sha256_file(path),
                    run_id=run.run_id,
                )
            )
        # Persist a manifest of what was registered for this run.
        manifest = [
            {"path": a.path, "checksum_sha256": a.checksum_sha256, "size_bytes": a.size_bytes}
            for a in artifacts
        ]
        (self._run_dir(run.run_id) / "outputs_manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )
        _ = record  # record kept for future fields (e.g. cost)
        return artifacts


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        log.debug("run process already exited before timeout kill: pid=%s", proc.pid)


def _resolve_executable(command: str, working_directory: Path) -> Path:
    candidate = Path(command)
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=True)
    elif "/" in command:
        resolved = (working_directory / candidate).resolve(strict=True)
    else:
        found = shutil.which(command)
        if found is None:
            raise StateError(f"command executable not found: {command}")
        resolved = Path(found).resolve(strict=True)
    if not resolved.is_file():
        raise StateError(f"command executable is not a file: {resolved}")
    return resolved


def _sandbox_environment() -> dict[str, str]:
    exact = {
        "HOME",
        "LANG",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        "USER",
    }
    return {
        name: value
        for name, value in os.environ.items()
        if name in exact or name.startswith("LC_")
    }
