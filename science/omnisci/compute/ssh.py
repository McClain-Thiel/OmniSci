# SPDX-License-Identifier: Apache-2.0
"""SSH compute provider for existing Linux hosts.

The provider stages the declared working tree with ``scp``, executes through
OpenSSH with strict host-key checking, and collects declared outputs through a
validated tar stream. Private-key contents are never read into project
configuration; only an optional identity-file path is stored.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import shlex
import shutil
import subprocess
import tarfile
from collections.abc import Callable
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
from omnisci.domain.schemas import Artifact, RunState, new_id, utcnow
from omnisci.errors import NotFoundError, StateError
from omnisci.storage.local import sha256_file

DEFAULT_RUNTIME_MINUTES = 60.0
DEFAULT_REMOTE_ROOT = "/tmp/omnisci"
_SOURCE_EXCLUDES = {".git", ".omnisci", ".science", ".env", ".env.local"}
_TERMINAL = {
    RunState.SUCCEEDED.value,
    RunState.FAILED.value,
    RunState.CANCELLED.value,
    RunState.TIMEOUT.value,
}
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
_USER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_REMOTE_ROOT_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]+$")

log = logging.getLogger(__name__)


class SshComputeProvider:
    name = "ssh"

    def __init__(
        self,
        *,
        project_dir: Path,
        runs_dir: Path | None = None,
        config: dict | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.runs_dir = Path(runs_dir) if runs_dir else self.project_dir / ".omnisci" / "runs"
        self.config = dict(config or {})
        self._run_command = command_runner or subprocess.run

        self.host = self._required_string("host")
        self.user = self._string("user", "ec2-user")
        if not _HOST_PATTERN.fullmatch(self.host):
            raise StateError(f"invalid SSH host: {self.host}")
        if not _USER_PATTERN.fullmatch(self.user):
            raise StateError(f"invalid SSH user: {self.user}")

        self.port = self._integer("port", 22)
        if not 1 <= self.port <= 65535:
            raise StateError("SSH port must be between 1 and 65535")
        self.connect_timeout_seconds = self._integer("connect_timeout_seconds", 10)
        if self.connect_timeout_seconds <= 0:
            raise StateError("SSH connect timeout must be positive")
        self.max_runtime_minutes = self._number("max_runtime_minutes", DEFAULT_RUNTIME_MINUTES)
        if self.max_runtime_minutes <= 0:
            raise StateError("SSH maximum runtime must be positive")

        identity = self._string("identity_file", "")
        self.identity_file = Path(identity).expanduser().resolve() if identity else None
        if self.identity_file is not None and not self.identity_file.is_file():
            raise StateError(f"SSH identity file does not exist: {self.identity_file}")

        known_hosts = self._string("known_hosts_file", "~/.ssh/known_hosts")
        self.known_hosts_file = Path(known_hosts).expanduser().resolve()
        if not self.known_hosts_file.is_file():
            raise StateError(f"SSH known-hosts file does not exist: {self.known_hosts_file}")

        remote_root = PurePosixPath(self._string("remote_root", DEFAULT_REMOTE_ROOT))
        if not remote_root.is_absolute() or remote_root == PurePosixPath("/"):
            raise StateError("SSH remote root must be an absolute path other than /")
        if ".." in remote_root.parts:
            raise StateError("SSH remote root cannot contain '..'")
        if not _REMOTE_ROOT_PATTERN.fullmatch(remote_root.as_posix()):
            raise StateError("SSH remote root contains unsupported characters")
        self.remote_root = remote_root

        cleanup_remote = self.config.get("cleanup_remote", True)
        if not isinstance(cleanup_remote, bool):
            raise StateError("SSH cleanup_remote must be a boolean")
        self.cleanup_remote = cleanup_remote

        if shutil.which("ssh") is None or shutil.which("scp") is None:
            raise StateError("SSH compute requires the ssh and scp commands")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            modes=["ssh"],
            supports_cancel=False,
            supports_logs=True,
            resources={"cpu": False, "memory_gib": False, "gpu": False},
            extensions={
                "omnisci.ssh": {
                    "synchronous": True,
                    "strict_host_key_checking": True,
                    "filesystem_staging": True,
                }
            },
        )

    def check(self) -> ProviderCheck:
        """Probe whether this connector can be used right now.

        Configuration alone proves nothing: a key can be unauthorized, a host
        key can be missing, a VPN can be down, or a multiplexed session can
        expire. Without this the first sign of trouble is a failed job
        submission reporting raw ssh stderr, which does not say what to fix.
        """
        try:
            completed = self._ssh_run(
                "printf omnisci-ok\n",
                capture_output=True,
                timeout=self.connect_timeout_seconds + 15,
            )
        except subprocess.TimeoutExpired:
            return self._failed_check(
                "unreachable",
                f"no response from {self.host} within {self.connect_timeout_seconds + 15}s",
                "Check the host is up and that you are on any VPN it requires.",
            )
        except OSError as exc:
            return self._failed_check("unreachable", str(exc), "Check the ssh client is usable.")

        if completed.returncode == 0 and b"omnisci-ok" in self._output_bytes(completed.stdout):
            return ProviderCheck(
                provider=self.name,
                status="ok",
                detail=f"{self.user}@{self.host} reachable",
                checked_at=utcnow(),
                observed=self._observed(),
            )
        return self._classify_ssh_failure(
            self._output_bytes(completed.stderr).decode(errors="replace").strip()
        )

    def _classify_ssh_failure(self, stderr: str) -> ProviderCheck:
        """Turn ssh's stderr into a named cause and a concrete next step."""
        lowered = stderr.lower()
        if "host key verification failed" in lowered or "no matching host key" in lowered:
            return self._failed_check(
                "misconfigured",
                stderr,
                f"The host key for {self.host} is not in {self.known_hosts_file}. "
                f"Connect once by hand (`ssh {self.host}`) to record it.",
            )
        if "permission denied" in lowered or "publickey" in lowered:
            return self._failed_check(
                "auth_failed",
                stderr,
                # BatchMode is deliberate -- a background poller must never sit
                # on a password prompt -- so an interactive-only host needs a
                # session opened out of band that OpenSSH can then reuse.
                f"OmniSci connects non-interactively, so it cannot answer a password or "
                f"MFA prompt. If {self.host} requires one, run `ssh {self.host}` yourself "
                f"and keep it open; with ControlMaster/ControlPersist in your SSH config "
                f"OmniSci will reuse that session.",
            )
        if any(s in lowered for s in ("could not resolve", "name or service not known")):
            return self._failed_check(
                "unreachable",
                stderr,
                f"{self.host} does not resolve. If it is an ~/.ssh/config alias, check the "
                f"Host block; otherwise check DNS or your VPN.",
            )
        if any(s in lowered for s in ("connection refused", "timed out", "no route to host")):
            return self._failed_check(
                "unreachable",
                stderr,
                f"Could not reach {self.host}:{self.port}. Check the host is up and that "
                f"you are on any VPN it requires.",
            )
        return self._failed_check("misconfigured", stderr or "ssh failed with no output", "")

    def _failed_check(self, status: str, detail: str, remedy: str) -> ProviderCheck:
        return ProviderCheck(
            provider=self.name,
            status=status,  # type: ignore[arg-type]
            detail=detail,
            remedy=remedy,
            checked_at=utcnow(),
        )

    def _observed(self) -> dict:
        """Facts worth reporting once connected. Subclasses add scheduler detail."""
        return {"host": self.host, "user": self.user, "remote_root": self.remote_root.as_posix()}

    def validate(self, spec: ExecutionSpec) -> ExecutionPlan:
        details = spec.spec
        if details.provider != self.name:
            raise StateError(f"ssh provider cannot execute spec for provider '{details.provider}'")
        if details.mode != "ssh":
            raise StateError("ssh provider supports only mode=ssh")
        if not details.command:
            raise StateError("execution spec has an empty command")
        if details.network.mode != "allow":
            raise StateError("SSH compute cannot enforce network.mode=deny")
        if details.environment.image not in {None, "host", "local"}:
            raise StateError("SSH compute uses the host environment; use image 'host' or omit it")
        if details.resources is not None and any(
            value is not None
            for value in (
                details.resources.cpu,
                details.resources.memory_gib,
                details.resources.gpu,
            )
        ):
            raise StateError("SSH compute cannot enforce CPU, memory, or GPU resource requests")
        if details.inputs:
            raise StateError(
                "SSH compute does not stage execution-spec inputs; include project files "
                "or stage them with 'science storage stage' first"
            )
        if details.outputs is not None and details.outputs.destination:
            raise StateError(
                "SSH compute does not upload output destinations; collect locally and use "
                "'science storage put'"
            )

        working_directory = (self.project_dir / details.source.working_directory).resolve()
        self._require_project_path(working_directory, "working directory")
        if not working_directory.is_dir():
            raise StateError(f"working directory does not exist: {working_directory}")
        if any(part in {".omnisci", ".science", ".git"} for part in working_directory.parts):
            raise StateError("working directory cannot be inside internal state or .git")

        runtime = self._runtime_minutes(spec)
        if runtime <= 0 or runtime > self.max_runtime_minutes:
            raise StateError(
                f"SSH runtime must be positive and no more than "
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
                    raise StateError("SSH output cannot be the project root")
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
        archive = run_dir / "source.tar.gz"
        record = {
            "run_id": run_id,
            "provider": self.name,
            "provider_run_id": remote_run.as_posix(),
            "spec_hash": plan.spec_hash,
            "idempotency_key": plan.idempotency_key,
            "command": plan.command,
            "working_directory": plan.working_directory,
            "resolved_outputs": plan.resolved_outputs,
            "remote_run": remote_run.as_posix(),
            "remote_project": remote_project.as_posix(),
            "status": RunState.QUEUED.value,
            "queued_at": utcnow(),
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "remote_cleaned": False,
        }
        self._write_record(run_id, record)
        (run_dir / "execution_spec.json").write_text(plan.spec.canonical_json())

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
        except (OSError, subprocess.SubprocessError, StateError) as exc:
            (run_dir / "stderr.log").write_text(f"SSH staging failed: {exc}\n")
            (run_dir / "stdout.log").write_text("")
            record.update(status=RunState.FAILED.value, finished_at=utcnow())
            self._write_record(run_id, record)
            if remote_created:
                self._cleanup_remote(record, run_id, raise_on_error=False)
            raise
        finally:
            archive.unlink(missing_ok=True)

        record.update(status=RunState.RUNNING.value, started_at=utcnow())
        self._write_record(run_id, record)
        runtime_seconds = int(self._runtime_minutes(plan.spec) * 60)
        relative_working_directory = Path(plan.working_directory).relative_to(self.project_dir)
        remote_working_directory = remote_project.joinpath(*relative_working_directory.parts)
        remote_command = (
            f"cd {shlex.quote(remote_working_directory.as_posix())} "
            f"&& timeout --signal=TERM --kill-after=5s {runtime_seconds}s "
            f"{shlex.join(plan.command)}"
        )
        try:
            completed = self._ssh_run(
                remote_command,
                capture_output=True,
                timeout=runtime_seconds + self.connect_timeout_seconds + 15,
            )
            stdout = self._output_bytes(completed.stdout)
            stderr = self._output_bytes(completed.stderr)
            exit_code = completed.returncode
            status = (
                RunState.SUCCEEDED.value
                if exit_code == 0
                else RunState.TIMEOUT.value
                if exit_code == 124
                else RunState.FAILED.value
            )
        except subprocess.TimeoutExpired as exc:
            stdout = self._output_bytes(exc.stdout)
            stderr = self._output_bytes(exc.stderr)
            exit_code = None
            status = RunState.TIMEOUT.value
        except (OSError, subprocess.SubprocessError) as exc:
            stdout = b""
            stderr = f"SSH execution failed: {exc}\n".encode()
            exit_code = None
            status = RunState.FAILED.value
        (run_dir / "stdout.log").write_bytes(stdout)
        (run_dir / "stderr.log").write_bytes(stderr)
        record.update(status=status, exit_code=exit_code, finished_at=utcnow())
        self._write_record(run_id, record)
        if status != RunState.SUCCEEDED.value:
            self._cleanup_remote(record, run_id, raise_on_error=False)
        return RunReference(
            provider=self.name,
            provider_run_id=remote_run.as_posix(),
            run_id=run_id,
        )

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
        return LogPage(
            content=(run_dir / "stdout.log").read_text(errors="replace"),
            stderr=(run_dir / "stderr.log").read_text(errors="replace"),
            next_cursor=None,
        )

    def cancel(self, run: RunReference) -> None:
        record = self._read_record(run.run_id)
        if record["status"] in _TERMINAL:
            raise StateError(
                f"run {run.run_id} already terminal ({record['status']}); cannot cancel"
            )
        raise StateError("SSH compute is synchronous and does not support cancellation")

    def collect(self, run: RunReference) -> list[Artifact]:
        record = self._read_record(run.run_id)
        manifest_path = self._run_dir(run.run_id) / "outputs_manifest.json"
        if manifest_path.is_file():
            self._cleanup_remote(record, run.run_id, raise_on_error=True)
            return self._artifacts_from_manifest(run.run_id, manifest_path)

        outputs = list(record.get("resolved_outputs") or [])
        if outputs:
            archive = self._run_dir(run.run_id) / "outputs.tar"
            remote_project = record["remote_project"]
            remote_command = (
                f"cd {shlex.quote(remote_project)} && tar --create --file=- -- "
                f"{shlex.join(outputs)}"
            )
            with archive.open("wb") as stream:
                completed = self._ssh_run(
                    remote_command,
                    stdout=stream,
                    stderr=subprocess.PIPE,
                    timeout=self.connect_timeout_seconds + 60,
                )
            if completed.returncode != 0:
                stderr = self._output_bytes(completed.stderr).decode(errors="replace").strip()
                raise StateError(f"SSH output collection failed: {stderr or completed.returncode}")
            artifacts = self._extract_outputs(run.run_id, archive, outputs)
            archive.unlink(missing_ok=True)
        else:
            artifacts = []

        manifest = [
            {
                "path": artifact.path,
                "uri": artifact.uri,
                "type": artifact.type,
                "mime": artifact.mime,
                "size_bytes": artifact.size_bytes,
                "checksum_sha256": artifact.checksum_sha256,
            }
            for artifact in artifacts
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        self._cleanup_remote(record, run.run_id, raise_on_error=True)
        return artifacts

    def _create_source_archive(self, working_directory: Path, archive: Path) -> None:
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(working_directory.rglob("*")):
                relative = path.relative_to(self.project_dir)
                if any(
                    part in _SOURCE_EXCLUDES or part.startswith(".env.") for part in relative.parts
                ):
                    continue
                if path.is_symlink():
                    raise StateError(f"SSH source tree contains a symlink: {relative}")
                tar.add(path, arcname=relative.as_posix(), recursive=False)

    def _extract_outputs(
        self, run_id: str, archive: Path, declared_outputs: list[str]
    ) -> list[Artifact]:
        extracted: list[Path] = []
        declared = [PurePosixPath(output) for output in declared_outputs]
        with tarfile.open(archive, "r:") as tar:
            for member in tar.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise StateError(f"unsafe path in SSH output archive: {member.name}")
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise StateError(f"unsafe entry in SSH output archive: {member.name}")
                if not any(relative == root or relative.is_relative_to(root) for root in declared):
                    raise StateError(f"undeclared path in SSH output archive: {member.name}")
                destination = (self.project_dir / Path(*relative.parts)).resolve()
                self._require_project_path(destination, "collected output")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                source = tar.extractfile(member)
                if source is None:
                    raise StateError(f"could not read SSH output archive entry: {member.name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                extracted.append(destination)

        return [
            Artifact(
                path=path.relative_to(self.project_dir).as_posix(),
                uri=f"file://{path}",
                type="result",
                mime=mimetypes.guess_type(path.name)[0],
                size_bytes=path.stat().st_size,
                checksum_sha256=sha256_file(path),
                run_id=run_id,
            )
            for path in sorted(set(extracted))
        ]

    def _artifacts_from_manifest(self, run_id: str, path: Path) -> list[Artifact]:
        entries = json.loads(path.read_text())
        return [Artifact(run_id=run_id, **entry) for entry in entries]

    def _checked_ssh(self, remote_command: str) -> None:
        completed = self._ssh_run(
            remote_command,
            capture_output=True,
            timeout=self.connect_timeout_seconds + 60,
        )
        self._require_success(completed, "SSH command")

    def _checked_scp(self, source: Path, destination: PurePosixPath) -> None:
        command = ["scp", *self._common_options(port_flag="-P")]
        command.extend([str(source), f"{self.user}@{self.host}:{destination.as_posix()}"])
        completed = self._run_command(
            command,
            capture_output=True,
            timeout=self.connect_timeout_seconds + 60,
        )
        self._require_success(completed, "SCP upload")

    def _cleanup_remote(self, record: dict, run_id: str, *, raise_on_error: bool) -> None:
        if not self.cleanup_remote or record.get("remote_cleaned"):
            return
        try:
            self._checked_ssh(f"rm -rf -- {shlex.quote(record['remote_run'])}")
        except (OSError, subprocess.SubprocessError, StateError) as exc:
            message = f"SSH remote cleanup failed: {exc}"
            record["cleanup_error"] = message
            self._write_record(run_id, record)
            with (self._run_dir(run_id) / "stderr.log").open("a") as stderr:
                stderr.write(f"{message}\n")
            log.warning("%s", message)
            if raise_on_error:
                raise StateError(message) from exc
            return
        record["remote_cleaned"] = True
        record.pop("cleanup_error", None)
        self._write_record(run_id, record)

    def _ssh_command(self) -> list[str]:
        return [
            "ssh",
            *self._common_options(port_flag="-p"),
            f"{self.user}@{self.host}",
        ]

    def _ssh_run(self, script: str, **kwargs) -> subprocess.CompletedProcess:
        """Run a POSIX script on the remote host whatever its login shell is.

        ``ssh host 'script'`` hands the string to the account's *login* shell,
        and university clusters commonly issue csh/tcsh -- which rejects
        ``if ...; then ...; fi``, ``cmd || { ...; }`` and ``var=$(...)`` as
        syntax errors. Staging still succeeds, so the failure surfaces later as
        a baffling "if: Expression Syntax" on the first status poll.

        Naming ``/bin/sh`` as the remote command and feeding the script on stdin
        sidesteps the login shell *and* its quoting rules: passing the script as
        an argument instead would still have to survive csh's own parser, which
        mangles the quoting. stdin is otherwise unused, so this leaves stdout
        free for the output tar stream.
        """
        return self._run_command(
            [*self._ssh_command(), "/bin/sh"], input=script.encode(), **kwargs
        )

    def _common_options(self, *, port_flag: str) -> list[str]:
        options = [
            port_flag,
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts_file}",
            "-o",
            f"ConnectTimeout={self.connect_timeout_seconds}",
        ]
        if self.identity_file is not None:
            options.extend(["-i", str(self.identity_file)])
        return options

    def _run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def _record_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def _read_record(self, run_id: str) -> dict:
        path = self._record_path(run_id)
        if not path.is_file():
            raise NotFoundError(f"run not found: {run_id}")
        return json.loads(path.read_text())

    def _write_record(self, run_id: str, record: dict) -> None:
        self._record_path(run_id).write_text(json.dumps(record, indent=2, sort_keys=True))

    def _require_project_path(self, path: Path, label: str) -> None:
        if path != self.project_dir and not path.is_relative_to(self.project_dir):
            raise StateError(f"{label} outside project: {path}")

    def _required_string(self, key: str) -> str:
        value = self._string(key, "")
        if not value:
            raise StateError(f"SSH {key} is required")
        return value

    def _string(self, key: str, default: str) -> str:
        value = self.config.get(key, default)
        if not isinstance(value, str):
            raise StateError(f"SSH {key} must be a string")
        return value.strip()

    def _integer(self, key: str, default: int) -> int:
        value = self.config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise StateError(f"SSH {key} must be an integer")
        return value

    def _number(self, key: str, default: float) -> float:
        value = self.config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StateError(f"SSH {key} must be a number")
        return float(value)

    def _runtime_minutes(self, spec: ExecutionSpec) -> float:
        requested = spec.spec.limits.max_runtime_minutes
        return (
            requested
            if requested is not None
            else min(DEFAULT_RUNTIME_MINUTES, self.max_runtime_minutes)
        )

    @staticmethod
    def _output_bytes(value: str | bytes | None) -> bytes:
        if value is None:
            return b""
        return value if isinstance(value, bytes) else value.encode()

    @classmethod
    def _require_success(cls, completed: subprocess.CompletedProcess, operation: str) -> None:
        if completed.returncode == 0:
            return
        stderr = cls._output_bytes(completed.stderr).decode(errors="replace").strip()
        raise StateError(f"{operation} failed: {stderr or completed.returncode}")
