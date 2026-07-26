# SPDX-License-Identifier: Apache-2.0
"""Slurm and qsub compute providers using OpenSSH as the transport."""

from __future__ import annotations

import math
import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

from omnisci.compute.base import (
    ExecutionPlan,
    ExecutionSpec,
    LogPage,
    ProviderCapabilities,
    ProviderCheck,
    RunReference,
    RunStatus,
)
from omnisci.compute.ssh import _TERMINAL, SshComputeProvider
from omnisci.domain.schemas import RunState, new_id, utcnow
from omnisci.errors import StateError

_DIRECTIVE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/+,[\]()&|!*=~-]+$")
_SLURM_JOB_ID_PATTERN = re.compile(r"^[0-9]+(?:[._][0-9]+)?$")
_QSUB_JOB_ID_PATTERN = re.compile(r"^[0-9]+(?:\.[A-Za-z0-9_.-]+)?$")


class SchedulerComputeProvider(SshComputeProvider, ABC):
    """Shared asynchronous scheduler lifecycle over the SSH staging substrate."""

    mode = "batch"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            modes=[self.mode],
            supports_cancel=True,
            supports_logs=True,
            resources={"cpu": True, "memory_gib": True, "gpu": True},
            extensions={
                "omnisci.scheduler": {
                    "asynchronous": True,
                    "transport": "ssh",
                    "restart_reconciliation": True,
                    "filesystem_staging": True,
                }
            },
        )

    #: Binaries this provider drives, and the marker that identifies the flavour.
    required_commands: tuple[str, ...] = ()

    def check(self) -> ProviderCheck:
        """Connectivity, then whether this login node actually runs a scheduler.

        Reaching the host proves nothing for a scheduler provider: a cluster
        gateway may be a pure jump host with no ``qsub`` on it at all, and a
        site may run Grid Engine where the configuration claims PBS. Both only
        showed up as a failed submission or a job that queued forever.
        """
        connectivity = super().check()
        if not connectivity.ok:
            return connectivity

        probe = "; ".join(
            f'printf "{command}=%s\\n" "$(command -v {command} 2>/dev/null || printf -)"'
            for command in (*self.required_commands, "qconf", "pbsnodes")
        )
        try:
            completed = self._ssh_run(
                probe, capture_output=True, timeout=self.connect_timeout_seconds + 30
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._failed_check("unreachable", str(exc), "")

        found = {}
        for line in self._output_bytes(completed.stdout).decode(errors="replace").splitlines():
            name, _, path = line.partition("=")
            found[name.strip()] = path.strip()

        missing = [c for c in self.required_commands if found.get(c, "-") == "-"]
        if missing:
            return self._failed_check(
                "missing_dependency",
                f"{', '.join(missing)} not found on {self.host}",
                f"{self.host} does not appear to run {self.name}. Cluster gateways are often "
                f"jump hosts with no scheduler; point the connector at the submit/login node.",
            )

        observed = {
            **self._observed(),
            "scheduler_paths": {c: found[c] for c in self.required_commands},
        }
        detected = (
            "sge"
            if found.get("qconf", "-") != "-"
            else ("pbs" if found.get("pbsnodes", "-") != "-" else None)
        )
        if detected:
            observed["detected_dialect"] = detected
        mismatch = self._dialect_mismatch(detected)
        if mismatch:
            return ProviderCheck(
                provider=self.name,
                status="misconfigured",
                detail=mismatch[0],
                remedy=mismatch[1],
                checked_at=utcnow(),
                observed=observed,
            )
        return ProviderCheck(
            provider=self.name,
            status="ok",
            detail=f"{self.name} reachable on {self.host}",
            checked_at=utcnow(),
            observed=observed,
        )

    def _dialect_mismatch(self, _detected: str | None) -> tuple[str, str] | None:
        """Subclasses that carry a dialect setting report a disagreement here.

        Slurm has no dialect to get wrong, so the base answer is always None.
        """
        return None

    def validate(self, spec: ExecutionSpec) -> ExecutionPlan:
        details = spec.spec
        if details.provider != self.name:
            raise StateError(
                f"{self.name} provider cannot execute spec for provider '{details.provider}'"
            )
        if details.mode != self.mode:
            raise StateError(f"{self.name} provider supports only mode={self.mode}")
        if not details.command:
            raise StateError("execution spec has an empty command")
        if details.network.mode != "allow":
            raise StateError(f"{self.name} compute cannot enforce network.mode=deny")
        if details.environment.image not in {None, "host", "local"}:
            raise StateError(
                f"{self.name} compute uses the cluster environment; use image 'host' or omit it"
            )
        if details.inputs:
            raise StateError(
                f"{self.name} compute does not stage execution-spec inputs; include project "
                "files or stage them with 'science storage stage' first"
            )
        if details.outputs is not None and details.outputs.destination:
            raise StateError(
                f"{self.name} compute does not upload output destinations; collect locally and "
                "use 'science storage put'"
            )

        resources = details.resources
        if resources is not None:
            if resources.cpu is not None and resources.cpu <= 0:
                raise StateError("CPU request must be positive")
            if resources.memory_gib is not None and resources.memory_gib <= 0:
                raise StateError("memory request must be positive")
            if resources.gpu is not None:
                if resources.gpu.count <= 0:
                    raise StateError("GPU count must be positive")
                self._directive_value(resources.gpu.type, "GPU type")

        working_directory = (self.project_dir / details.source.working_directory).resolve()
        self._require_project_path(working_directory, "working directory")
        if not working_directory.is_dir():
            raise StateError(f"working directory does not exist: {working_directory}")
        if any(part in {".omnisci", ".science", ".git"} for part in working_directory.parts):
            raise StateError("working directory cannot be inside internal state or .git")

        runtime = self._runtime_minutes(spec)
        if runtime <= 0 or runtime > self.max_runtime_minutes:
            raise StateError(
                f"{self.name} runtime must be positive and no more than "
                f"{self.max_runtime_minutes:g} minutes"
            )

        resolved_outputs: list[str] = []
        if details.outputs is not None:
            candidates = [*details.outputs.files]
            if details.outputs.path:
                candidates.append(details.outputs.path)
            for candidate in candidates:
                output = (self.project_dir / candidate).resolve()
                self._require_project_path(output, "declared output")
                relative = output.relative_to(self.project_dir).as_posix()
                if relative == ".":
                    raise StateError(f"{self.name} output cannot be the project root")
                if relative not in resolved_outputs:
                    resolved_outputs.append(relative)

        spec_hash = spec.spec_hash()
        return ExecutionPlan(
            provider=self.name,
            spec=spec,
            spec_hash=spec_hash,
            idempotency_key=spec.metadata.idempotency_key or spec_hash,
            working_directory=str(working_directory),
            command=list(details.command),
            resolved_outputs=resolved_outputs,
        )

    def submit(self, plan: ExecutionPlan) -> RunReference:
        run_id = new_id("run")
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        remote_run = self.remote_root / run_id
        remote_project = remote_run / "project"
        remote_working_directory = remote_project.joinpath(
            *Path(plan.working_directory).relative_to(self.project_dir).parts
        )
        archive = run_dir / "source.tar.gz"
        job_script = run_dir / "job.sh"
        stdout_file = remote_run / "stdout.log"
        stderr_file = remote_run / "stderr.log"
        started_file = remote_run / ".omnisci-started"
        exit_file = remote_run / ".omnisci-exit-code"
        record = {
            "run_id": run_id,
            "provider": self.name,
            "provider_run_id": None,
            "scheduler_job_id": None,
            "spec_hash": plan.spec_hash,
            "idempotency_key": plan.idempotency_key,
            "command": plan.command,
            "working_directory": plan.working_directory,
            "resolved_outputs": plan.resolved_outputs,
            "remote_run": remote_run.as_posix(),
            "remote_project": remote_project.as_posix(),
            "remote_working_directory": remote_working_directory.as_posix(),
            "stdout_file": stdout_file.as_posix(),
            "stderr_file": stderr_file.as_posix(),
            "started_file": started_file.as_posix(),
            "exit_file": exit_file.as_posix(),
            "status": RunState.QUEUED.value,
            "queued_at": utcnow(),
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "remote_cleaned": False,
        }
        self._write_record(run_id, record)
        (run_dir / "execution_spec.json").write_text(plan.spec.canonical_json())
        job_script.write_text(self._render_job_script(plan, record))

        remote_created = False
        try:
            self._create_source_archive(Path(plan.working_directory), archive)
            self._checked_ssh(
                f"mkdir -p -- {shlex.quote(remote_project.as_posix())} "
                f"&& chmod 700 -- {shlex.quote(remote_run.as_posix())}"
            )
            remote_created = True
            self._checked_scp(archive, remote_run / archive.name)
            self._checked_ssh(
                f"tar --extract --gzip --file "
                f"{shlex.quote((remote_run / archive.name).as_posix())} "
                f"--directory {shlex.quote(remote_project.as_posix())} "
                f"--no-same-owner --no-same-permissions "
                f"&& rm -- {shlex.quote((remote_run / archive.name).as_posix())}"
            )
            self._checked_scp(job_script, remote_run / job_script.name)
            completed = self._remote_command(
                self._submission_command(remote_run / job_script.name),
                operation=f"{self.name} submission",
            )
            scheduler_job_id = self._parse_job_id(
                self._output_bytes(completed.stdout).decode(errors="replace")
            )
        except (OSError, subprocess.SubprocessError, StateError) as exc:
            (run_dir / "stderr.log").write_text(f"{self.name} submission failed: {exc}\n")
            (run_dir / "stdout.log").write_text("")
            record.update(status=RunState.FAILED.value, finished_at=utcnow())
            self._write_record(run_id, record)
            if remote_created:
                self._cleanup_remote(record, run_id, raise_on_error=False)
            raise
        finally:
            archive.unlink(missing_ok=True)

        record.update(
            provider_run_id=scheduler_job_id,
            scheduler_job_id=scheduler_job_id,
        )
        self._write_record(run_id, record)
        return RunReference(
            provider=self.name,
            provider_run_id=scheduler_job_id,
            run_id=run_id,
        )

    def status(self, run: RunReference) -> RunStatus:
        record = self._read_record(run.run_id)
        if record["status"] not in _TERMINAL:
            exit_code = self._read_remote_exit_code(record)
            if exit_code is not None:
                state = RunState.SUCCEEDED if exit_code == 0 else RunState.FAILED
                self._record_state(record, state, exit_code=exit_code)
                self._refresh_logs(record)
            else:
                state, scheduler_exit_code = self._query_scheduler(record)
                if state is not None:
                    self._record_state(record, state, exit_code=scheduler_exit_code)
                    if state.value in _TERMINAL:
                        self._refresh_logs(record)
        return RunStatus(
            run_id=run.run_id,
            status=RunState(record["status"]),
            exit_code=record.get("exit_code"),
            queued_at=record.get("queued_at"),
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
        )

    def logs(self, run: RunReference, _cursor: str | None = None) -> LogPage:
        record = self._read_record(run.run_id)
        if not record.get("remote_cleaned"):
            self._refresh_logs(record)
        run_dir = self._run_dir(run.run_id)
        return LogPage(
            content=self._read_log(run_dir / "stdout.log"),
            stderr=self._read_log(run_dir / "stderr.log"),
            next_cursor=None,
        )

    def cancel(self, run: RunReference) -> None:
        record = self._read_record(run.run_id)
        if record["status"] in _TERMINAL:
            raise StateError(
                f"run {run.run_id} already terminal ({record['status']}); cannot cancel"
            )
        scheduler_job_id = self._scheduler_job_id(record)
        self._remote_command(
            self._cancellation_command(scheduler_job_id),
            operation=f"{self.name} cancellation",
        )
        self._refresh_logs(record)
        self._record_state(record, RunState.CANCELLED)
        self._cleanup_remote(record, run.run_id, raise_on_error=True)

    def _record_state(
        self,
        record: dict,
        state: RunState,
        *,
        exit_code: int | None = None,
    ) -> None:
        if state == RunState.RUNNING and record.get("started_at") is None:
            record["started_at"] = utcnow()
        if state.value in _TERMINAL and record.get("finished_at") is None:
            record["finished_at"] = utcnow()
        record["status"] = state.value
        if exit_code is not None:
            record["exit_code"] = exit_code
        self._write_record(record["run_id"], record)

    def _read_remote_exit_code(self, record: dict) -> int | None:
        exit_file = shlex.quote(record["exit_file"])
        completed = self._remote_command(
            f"if test -f {exit_file}; then cat -- {exit_file}; fi",
            operation=f"{self.name} exit-status check",
        )
        value = self._output_bytes(completed.stdout).decode(errors="replace").strip()
        if not value:
            return None
        if not re.fullmatch(r"-?[0-9]+", value):
            raise StateError(f"invalid {self.name} exit status: {value}")
        return int(value)

    def _refresh_logs(self, record: dict) -> None:
        if record.get("remote_cleaned"):
            return
        for key, filename in (("stdout_file", "stdout.log"), ("stderr_file", "stderr.log")):
            remote_path = shlex.quote(record[key])
            completed = self._remote_command(
                f"if test -f {remote_path}; then cat -- {remote_path}; fi",
                operation=f"{self.name} log retrieval",
            )
            (self._run_dir(record["run_id"]) / filename).write_bytes(
                self._output_bytes(completed.stdout)
            )

    def _remote_command(self, remote_command: str, *, operation: str):
        completed = self._ssh_run(
            remote_command,
            capture_output=True,
            timeout=self.connect_timeout_seconds + 60,
        )
        self._require_success(completed, operation)
        return completed

    def _scheduler_job_id(self, record: dict) -> str:
        job_id = record.get("scheduler_job_id")
        if not isinstance(job_id, str) or not job_id:
            raise StateError(f"run {record['run_id']} has no scheduler job id")
        self._validate_job_id(job_id)
        return job_id

    @staticmethod
    def _read_log(path: Path) -> str:
        if not path.is_file():
            return ""
        return path.read_text(errors="replace")

    @staticmethod
    def _job_name(spec: ExecutionSpec) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]+", "-", spec.metadata.name).strip("-.")
        return (value or "omnisci-run")[:128]

    @staticmethod
    def _walltime(minutes: float) -> str:
        seconds = max(1, math.ceil(minutes * 60))
        hours, remainder = divmod(seconds, 3600)
        minutes_part, seconds_part = divmod(remainder, 60)
        return f"{hours:02d}:{minutes_part:02d}:{seconds_part:02d}"

    @staticmethod
    def _memory_mebibytes(gib: float) -> int:
        return max(1, math.ceil(gib * 1024))

    @staticmethod
    def _directive_value(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized or not _DIRECTIVE_VALUE_PATTERN.fullmatch(normalized):
            raise StateError(f"invalid scheduler {label}: {value}")
        return normalized

    @abstractmethod
    def _render_job_script(self, plan: ExecutionPlan, record: dict) -> str: ...

    @abstractmethod
    def _submission_command(self, job_script: PurePosixPath) -> str: ...

    @abstractmethod
    def _parse_job_id(self, output: str) -> str: ...

    @abstractmethod
    def _validate_job_id(self, job_id: str) -> None: ...

    @abstractmethod
    def _query_scheduler(self, record: dict) -> tuple[RunState | None, int | None]: ...

    @abstractmethod
    def _cancellation_command(self, job_id: str) -> str: ...

    def _script_body(self, plan: ExecutionPlan, record: dict) -> list[str]:
        started_file = shlex.quote(record["started_file"])
        exit_file = shlex.quote(record["exit_file"])
        working_directory = shlex.quote(record["remote_working_directory"])
        return [
            "set +e",
            f"printf '%s\\n' \"$$\" > {started_file}",
            f"cd {working_directory}",
            "omnisci_exit_code=$?",
            'if test "$omnisci_exit_code" -ne 0; then',
            f"  printf '%s\\n' \"$omnisci_exit_code\" > {exit_file}.tmp.$$",
            f"  mv -- {exit_file}.tmp.$$ {exit_file}",
            '  exit "$omnisci_exit_code"',
            "fi",
            shlex.join(plan.command),
            "omnisci_exit_code=$?",
            f"printf '%s\\n' \"$omnisci_exit_code\" > {exit_file}.tmp.$$",
            f"mv -- {exit_file}.tmp.$$ {exit_file}",
            'exit "$omnisci_exit_code"',
        ]


class SlurmComputeProvider(SchedulerComputeProvider):
    name = "slurm"
    required_commands = ("sbatch", "squeue", "scancel")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.partition = self._optional_directive("partition")
        self.account = self._optional_directive("account")
        self.qos = self._optional_directive("qos")
        self.constraint = self._optional_directive("constraint")

    def _render_job_script(self, plan: ExecutionPlan, record: dict) -> str:
        spec = plan.spec
        details = spec.spec
        directives = [
            f"#SBATCH --job-name={self._job_name(spec)}",
            f"#SBATCH --output={record['stdout_file']}",
            f"#SBATCH --error={record['stderr_file']}",
            f"#SBATCH --chdir={record['remote_working_directory']}",
            f"#SBATCH --time={self._walltime(self._runtime_minutes(spec))}",
        ]
        resources = details.resources
        if resources is not None:
            if resources.cpu is not None:
                directives.append(f"#SBATCH --cpus-per-task={resources.cpu}")
            if resources.memory_gib is not None:
                directives.append(f"#SBATCH --mem={self._memory_mebibytes(resources.memory_gib)}M")
            if resources.gpu is not None:
                gpu_type = resources.gpu.type.strip().casefold()
                gres = (
                    f"gpu:{resources.gpu.count}"
                    if gpu_type in {"gpu", "any", "generic"}
                    else f"gpu:{self._directive_value(resources.gpu.type, 'GPU type')}:"
                    f"{resources.gpu.count}"
                )
                directives.append(f"#SBATCH --gres={gres}")
        for key, value in (
            ("partition", self.partition),
            ("account", self.account),
            ("qos", self.qos),
            ("constraint", self.constraint),
        ):
            if value:
                directives.append(f"#SBATCH --{key}={value}")
        return "\n".join(["#!/bin/sh", *directives, "", *self._script_body(plan, record), ""])

    def _submission_command(self, job_script: PurePosixPath) -> str:
        return f"sbatch --parsable {shlex.quote(job_script.as_posix())}"

    def _parse_job_id(self, output: str) -> str:
        normalized = output.strip()
        match = re.fullmatch(r"([0-9]+(?:[._][0-9]+)?)(?:;[A-Za-z0-9_.-]+)?", normalized)
        if match is None:
            raise StateError(f"invalid Slurm job id: {normalized or '<empty>'}")
        job_id = match.group(1)
        self._validate_job_id(job_id)
        return job_id

    def _validate_job_id(self, job_id: str) -> None:
        if not _SLURM_JOB_ID_PATTERN.fullmatch(job_id):
            raise StateError(f"invalid Slurm job id: {job_id or '<empty>'}")

    def _query_scheduler(self, record: dict) -> tuple[RunState | None, int | None]:
        job_id = self._scheduler_job_id(record)
        command = (
            "command -v squeue >/dev/null || { "
            "printf 'squeue is not installed on the remote host\\n' >&2; exit 127; }; "
            f"omnisci_state=$(squeue --noheader --jobs {shlex.quote(job_id)} "
            "--format=%T 2>/dev/null | head -n 1); "
            'if test -n "$omnisci_state"; then '
            "printf 'SQUEUE|%s\\n' \"$omnisci_state\"; "
            "elif command -v sacct >/dev/null; then sacct --noheader --parsable2 -X --jobs "
            f"{shlex.quote(job_id)} --format=State,ExitCode 2>/dev/null | head -n 1; fi"
        )
        completed = self._remote_command(command, operation="Slurm status query")
        output = self._output_bytes(completed.stdout).decode(errors="replace").strip()
        if not output:
            return None, None
        if output.startswith("SQUEUE|"):
            state_name = output.split("|", 1)[1]
            return self._map_state(state_name), None
        state_name, _, exit_value = output.partition("|")
        state = self._map_state(state_name)
        exit_code = self._parse_slurm_exit_code(exit_value) if exit_value else None
        return state, exit_code

    def _cancellation_command(self, job_id: str) -> str:
        self._validate_job_id(job_id)
        return f"scancel {shlex.quote(job_id)}"

    def _optional_directive(self, key: str) -> str | None:
        value = self.config.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise StateError(f"Slurm {key} must be a string")
        return self._directive_value(value, key)

    @staticmethod
    def _parse_slurm_exit_code(value: str) -> int | None:
        match = re.match(r"^([0-9]+):[0-9]+$", value.strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def _map_state(value: str) -> RunState | None:
        state = value.strip().split()[0].rstrip("+").upper() if value.strip() else ""
        if state in {"PENDING", "CONFIGURING", "REQUEUED", "RESIZING", "SUSPENDED"}:
            return RunState.QUEUED
        if state in {"RUNNING", "COMPLETING", "STAGE_OUT", "SIGNALING"}:
            return RunState.RUNNING
        if state == "COMPLETED":
            return RunState.SUCCEEDED
        if state.startswith("CANCELLED"):
            return RunState.CANCELLED
        if state == "TIMEOUT":
            return RunState.TIMEOUT
        if state in {
            "BOOT_FAIL",
            "DEADLINE",
            "FAILED",
            "NODE_FAIL",
            "OUT_OF_MEMORY",
            "PREEMPTED",
            "REVOKED",
        }:
            return RunState.FAILED
        return None


class QsubComputeProvider(SchedulerComputeProvider):
    name = "qsub"
    required_commands = ("qsub", "qstat", "qdel")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        dialect = self.config.get("dialect", "pbs")
        if dialect not in {"pbs", "sge"}:
            raise StateError("qsub dialect must be 'pbs' or 'sge'")
        self.dialect = dialect
        self.queue = self._optional_directive("queue")
        self.account = self._optional_directive("account")
        self.parallel_environment = self._optional_directive("parallel_environment") or "smp"
        self.gpu_resource = self._optional_directive("gpu_resource") or (
            "ngpus" if self.dialect == "pbs" else "gpu"
        )

    def _dialect_mismatch(self, _detected: str | None) -> tuple[str, str] | None:
        detected = _detected
        if detected is None or detected == self.dialect:
            return None
        return (
            f"configured dialect is '{self.dialect}' but {self.host} runs {detected.upper()}",
            f"Set dialect: {detected} on this connector. The two emit different directives, "
            f"and the wrong ones are silently ignored -- the job queues but never gets the "
            f"resources it asked for.",
        )

    def _render_job_script(self, plan: ExecutionPlan, record: dict) -> str:
        if self.dialect == "pbs":
            directives = self._pbs_directives(plan, record)
        else:
            directives = self._sge_directives(plan, record)
        return "\n".join(["#!/bin/sh", *directives, "", *self._script_body(plan, record), ""])

    def _pbs_directives(self, plan: ExecutionPlan, record: dict) -> list[str]:
        spec = plan.spec
        resources = spec.spec.resources
        directives = [
            f"#PBS -N {self._job_name(spec)}",
            # PBS runs the job under the account's login shell unless told
            # otherwise, which would reject this script's POSIX body on a
            # csh/tcsh account. The shebang alone is not enough.
            "#PBS -S /bin/sh",
            f"#PBS -o {record['stdout_file']}",
            f"#PBS -e {record['stderr_file']}",
            f"#PBS -l walltime={self._walltime(self._runtime_minutes(spec))}",
        ]
        chunks = ["select=1"]
        if resources is not None:
            if resources.cpu is not None:
                chunks.append(f"ncpus={resources.cpu}")
            if resources.memory_gib is not None:
                chunks.append(f"mem={self._memory_mebibytes(resources.memory_gib)}mb")
            if resources.gpu is not None:
                chunks.append(f"{self.gpu_resource}={resources.gpu.count}")
                gpu_type = resources.gpu.type.strip().casefold()
                if gpu_type not in {"gpu", "any", "generic"}:
                    chunks.append(
                        f"gpu_type={self._directive_value(resources.gpu.type, 'GPU type')}"
                    )
        if len(chunks) > 1:
            directives.append(f"#PBS -l {':'.join(chunks)}")
        if self.queue:
            directives.append(f"#PBS -q {self.queue}")
        if self.account:
            directives.append(f"#PBS -A {self.account}")
        return directives

    def _sge_directives(self, plan: ExecutionPlan, record: dict) -> list[str]:
        spec = plan.spec
        resources = spec.spec.resources
        directives = [
            f"#$ -N {self._job_name(spec)}",
            # Same reason as the PBS branch: Grid Engine otherwise runs the
            # script under the account's login shell.
            "#$ -S /bin/sh",
            f"#$ -o {record['stdout_file']}",
            f"#$ -e {record['stderr_file']}",
            "#$ -cwd",
            f"#$ -l h_rt={self._walltime(self._runtime_minutes(spec))}",
        ]
        if resources is not None:
            if resources.cpu is not None:
                directives.append(f"#$ -pe {self.parallel_environment} {resources.cpu}")
            if resources.memory_gib is not None:
                slots = resources.cpu or 1
                memory_per_slot = math.ceil(self._memory_mebibytes(resources.memory_gib) / slots)
                directives.append(f"#$ -l h_vmem={memory_per_slot}M")
            if resources.gpu is not None:
                directives.append(f"#$ -l {self.gpu_resource}={resources.gpu.count}")
        if self.queue:
            directives.append(f"#$ -q {self.queue}")
        if self.account:
            directives.append(f"#$ -P {self.account}")
        return directives

    def _submission_command(self, job_script: PurePosixPath) -> str:
        return f"qsub {shlex.quote(job_script.as_posix())}"

    def _parse_job_id(self, output: str) -> str:
        normalized = output.strip()
        match = re.search(
            r"\b(?:Your job(?:-array)?\s+)?([0-9]+(?:\.[A-Za-z0-9_.-]+)?)\b",
            normalized,
        )
        if match is None:
            raise StateError(f"could not parse qsub job id from: {normalized or '<empty>'}")
        job_id = match.group(1)
        self._validate_job_id(job_id)
        return job_id

    def _validate_job_id(self, job_id: str) -> None:
        if not _QSUB_JOB_ID_PATTERN.fullmatch(job_id):
            raise StateError(f"invalid qsub job id: {job_id or '<empty>'}")

    def _query_scheduler(self, record: dict) -> tuple[RunState | None, int | None]:
        if self.dialect == "pbs":
            return self._query_pbs(record)
        return self._query_sge(record)

    def _query_pbs(self, record: dict) -> tuple[RunState | None, int | None]:
        job_id = self._scheduler_job_id(record)
        command = (
            "command -v qstat >/dev/null || { "
            "printf 'qstat is not installed on the remote host\\n' >&2; exit 127; }; "
            f"qstat -xf {shlex.quote(job_id)} 2>/dev/null "
            f"|| qstat -f {shlex.quote(job_id)} 2>/dev/null || true"
        )
        completed = self._remote_command(command, operation="PBS status query")
        output = self._output_bytes(completed.stdout).decode(errors="replace")
        state_match = re.search(r"^\s*job_state\s*=\s*([A-Za-z])", output, re.MULTILINE)
        if state_match is None:
            return None, None
        exit_match = re.search(r"^\s*Exit_status\s*=\s*(-?[0-9]+)", output, re.MULTILINE)
        exit_code = int(exit_match.group(1)) if exit_match else None
        state = state_match.group(1).upper()
        if state in {"Q", "H", "S", "W", "T"}:
            return RunState.QUEUED, None
        if state in {"R", "E", "B"}:
            return RunState.RUNNING, None
        if state in {"F", "C", "X"}:
            return (RunState.SUCCEEDED if exit_code == 0 else RunState.FAILED), exit_code
        return None, exit_code

    def _query_sge(self, record: dict) -> tuple[RunState | None, int | None]:
        job_id = self._scheduler_job_id(record)
        command = (
            "command -v qstat >/dev/null || { "
            "printf 'qstat is not installed on the remote host\\n' >&2; exit 127; }; "
            "omnisci_state=$(qstat 2>/dev/null | awk '$1 == \""
            f"{job_id}"
            "\" {print $5; exit}'); "
            'if test -n "$omnisci_state"; then '
            "printf 'QSTAT|%s\\n' \"$omnisci_state\"; "
            f"elif command -v qacct >/dev/null; then qacct -j {shlex.quote(job_id)} "
            "2>/dev/null || true; fi"
        )
        completed = self._remote_command(command, operation="Grid Engine status query")
        output = self._output_bytes(completed.stdout).decode(errors="replace").strip()
        if not output:
            return None, None
        if output.startswith("QSTAT|"):
            scheduler_state = output.split("|", 1)[1]
            if "r" in scheduler_state.casefold() or "t" in scheduler_state.casefold():
                return RunState.RUNNING, None
            if "e" in scheduler_state.casefold():
                return RunState.FAILED, None
            return RunState.QUEUED, None
        exit_match = re.search(r"^exit_status\s+(-?[0-9]+)", output, re.MULTILINE)
        failed_match = re.search(r"^failed\s+([0-9]+)", output, re.MULTILINE)
        if exit_match is None:
            return None, None
        exit_code = int(exit_match.group(1))
        failed = int(failed_match.group(1)) if failed_match else 0
        state = RunState.SUCCEEDED if exit_code == 0 and failed == 0 else RunState.FAILED
        return state, exit_code

    def _cancellation_command(self, job_id: str) -> str:
        self._validate_job_id(job_id)
        return f"qdel {shlex.quote(job_id)}"

    def _optional_directive(self, key: str) -> str | None:
        value = self.config.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise StateError(f"qsub {key} must be a string")
        return self._directive_value(value, key)
