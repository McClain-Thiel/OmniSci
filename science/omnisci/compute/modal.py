# SPDX-License-Identifier: Apache-2.0
"""Optional Modal Sandbox compute provider (spec §12.4).

The SDK is imported lazily. A submitted Sandbox stages only the declared
working tree and inputs, runs the command behind a ready-file gate, and keeps
the filesystem alive for a bounded collection grace period. Provider state is
persisted locally so status, logs, cancellation, and output collection survive
server restarts.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shlex
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from omnisci.compute.base import (
    ExecutionPlan,
    ExecutionSpec,
    LogPage,
    ProviderCapabilities,
    RunReference,
    RunStatus,
)
from omnisci.domain.schemas import Artifact, RunState, new_id, utcnow
from omnisci.errors import NotFoundError, StateError
from omnisci.storage.local import sha256_file

DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_RUNTIME_MINUTES = 60.0
DEFAULT_COLLECTION_GRACE_SECONDS = 3600
DEFAULT_LOG_PAGE_CHARS = 100_000
REMOTE_ROOT = PurePosixPath("/workspace")
READY_PATH = "/tmp/omnisci-ready"
EXIT_CODE_PATH = "/tmp/omnisci-exit-code"
STDOUT_PATH = "/tmp/omnisci-stdout.log"
STDERR_PATH = "/tmp/omnisci-stderr.log"
_TERMINAL = {
    RunState.SUCCEEDED.value,
    RunState.FAILED.value,
    RunState.CANCELLED.value,
    RunState.TIMEOUT.value,
}
_DEFAULT_SOURCE_EXCLUDES = {".git", ".omnisci", ".science", ".env", ".env.local"}


class ModalComputeProvider:
    name = "modal"

    def __init__(
        self,
        *,
        project_dir: Path,
        runs_dir: Path | None = None,
        config: dict | None = None,
        modal_module=None,
        storage_resolver: Callable[[str], object] | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.runs_dir = Path(runs_dir) if runs_dir else self.project_dir / ".omnisci" / "runs"
        self.config = dict(config or {})
        self._modal_module = modal_module
        self._storage_resolver = storage_resolver

    def bind_storage(self, resolver: Callable[[str], object]) -> None:
        """Bind the project service's policy-scoped storage broker."""
        self._storage_resolver = resolver

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            modes=["sandbox"],
            supports_cancel=True,
            supports_logs=True,
            resources={
                "cpu": True,
                "memory_gib": True,
                "gpu": self.config.get("gpu_types", []),
            },
            extensions={
                "omnisci.modal": {
                    "asynchronous": True,
                    "restart_reconciliation": True,
                    "filesystem_staging": True,
                }
            },
        )

    def validate(self, spec: ExecutionSpec) -> ExecutionPlan:
        details = spec.spec
        if details.provider != self.name:
            raise StateError(
                f"modal provider cannot execute spec for provider '{details.provider}'"
            )
        if details.mode != "sandbox":
            raise StateError("modal provider supports only mode=sandbox")
        if not details.command:
            raise StateError("execution spec has an empty command")

        image = details.environment.image or self.config.get("default_image") or DEFAULT_IMAGE
        if image == "local":
            raise StateError("modal provider requires a registry image")

        working_directory = (self.project_dir / details.source.working_directory).resolve()
        self._require_project_path(working_directory, "working directory")
        if not working_directory.is_dir():
            raise StateError(f"working directory does not exist: {working_directory}")
        if any(part in {".omnisci", ".science", ".git"} for part in working_directory.parts):
            raise StateError("working directory cannot be inside internal state or .git")

        runtime = details.limits.max_runtime_minutes or DEFAULT_RUNTIME_MINUTES
        max_runtime = float(self.config.get("max_runtime_minutes", 23 * 60))
        if runtime <= 0 or runtime > max_runtime:
            raise StateError(
                f"Modal runtime must be positive and no more than {max_runtime:g} minutes"
            )
        resources = details.resources
        if resources is not None:
            if resources.cpu is not None and resources.cpu <= 0:
                raise StateError("Modal CPU request must be positive")
            if resources.memory_gib is not None and resources.memory_gib <= 0:
                raise StateError("Modal memory request must be positive")
            if resources.gpu is not None and resources.gpu.count <= 0:
                raise StateError("Modal GPU count must be positive")

        for input_spec in details.inputs:
            self._relative_remote_path(input_spec.path or self._default_input_path(input_spec.uri))

        resolved_outputs: list[str] = []
        if details.outputs is not None:
            candidates = [*details.outputs.files]
            if details.outputs.path:
                candidates.append(details.outputs.path)
            for rel in candidates:
                path = (self.project_dir / rel).resolve()
                self._require_project_path(path, "declared output")
                self._relative_remote_path(rel)
                resolved_outputs.append(rel)

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
        modal = self._modal()
        details = plan.spec.spec
        run_id = new_id("run")
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "execution_spec.json").write_text(plan.spec.canonical_json())

        image_ref = details.environment.image or self.config.get("default_image") or DEFAULT_IMAGE
        app_name = str(self.config.get("app_name", "omnigent-science"))
        app = modal.App.lookup(app_name, create_if_missing=True)
        image = modal.Image.from_registry(image_ref)
        runtime_seconds = int((details.limits.max_runtime_minutes or DEFAULT_RUNTIME_MINUTES) * 60)
        grace_seconds = int(
            self.config.get("collection_grace_seconds", DEFAULT_COLLECTION_GRACE_SECONDS)
        )
        if grace_seconds < 60:
            raise StateError("Modal collection_grace_seconds must be at least 60")

        create_kwargs = {
            "app": app,
            "image": image,
            "timeout": runtime_seconds + grace_seconds,
            "block_network": details.network.mode == "deny",
            "tags": {
                "omnigent-science-run": run_id,
                "omnigent-science-project": self.project_dir.name,
            },
        }
        if self.config.get("region") is not None:
            create_kwargs["region"] = self.config["region"]
        if self.config.get("cloud") is not None:
            create_kwargs["cloud"] = self.config["cloud"]
        if details.resources is not None:
            if details.resources.cpu is not None:
                create_kwargs["cpu"] = details.resources.cpu
            if details.resources.memory_gib is not None:
                create_kwargs["memory"] = int(details.resources.memory_gib * 1024)
            if details.resources.gpu is not None:
                gpu = details.resources.gpu
                create_kwargs["gpu"] = gpu.type if gpu.count == 1 else f"{gpu.type}:{gpu.count}"

        remote_working_directory = self._remote_working_directory(plan)
        wrapper = self._wrapper_script(
            plan.command,
            remote_working_directory,
            runtime_seconds,
        )
        sandbox = modal.Sandbox.create("/bin/sh", "-lc", wrapper, **create_kwargs)
        record = {
            "run_id": run_id,
            "provider": self.name,
            "provider_run_id": sandbox.object_id,
            "spec_hash": plan.spec_hash,
            "idempotency_key": plan.idempotency_key,
            "status": RunState.RUNNING.value,
            "queued_at": utcnow(),
            "started_at": utcnow(),
            "finished_at": None,
            "exit_code": None,
            "collected": False,
        }
        self._write_record(run_id, record)

        try:
            self._stage_source(sandbox, Path(plan.working_directory))
            for input_spec in details.inputs:
                self._stage_input(sandbox, input_spec.uri, input_spec.path)
            sandbox.filesystem.write_text("ready\n", READY_PATH)
        except BaseException:
            sandbox.terminate(wait=True)
            raise
        finally:
            sandbox.detach()

        return RunReference(
            provider=self.name,
            provider_run_id=sandbox.object_id,
            run_id=run_id,
        )

    def status(self, run: RunReference) -> RunStatus:
        record = self._read_record(run.run_id)
        if record["status"] in _TERMINAL:
            return self._status_from_record(record)

        try:
            sandbox = self._lookup(run.provider_run_id)
        except NotFoundError:
            record.update(
                status=RunState.FAILED.value,
                finished_at=utcnow(),
            )
            self._write_record(run.run_id, record)
            return self._status_from_record(record)
        try:
            try:
                raw_exit_code = sandbox.filesystem.read_text(EXIT_CODE_PATH).strip()
            except Exception as exc:
                if not self._is_remote_not_found(exc):
                    raise
                sandbox_exit = sandbox.poll()
                if sandbox_exit is None:
                    return self._status_from_record(record)
                record.update(
                    status=RunState.FAILED.value,
                    exit_code=sandbox_exit,
                    finished_at=utcnow(),
                )
            else:
                try:
                    exit_code = int(raw_exit_code)
                except ValueError as exc:
                    raise StateError(
                        f"Modal run {run.run_id} wrote an invalid exit code: {raw_exit_code!r}"
                    ) from exc
                state = (
                    RunState.SUCCEEDED
                    if exit_code == 0
                    else RunState.TIMEOUT
                    if exit_code == 124
                    else RunState.FAILED
                )
                record.update(status=state.value, exit_code=exit_code, finished_at=utcnow())
            self._write_record(run.run_id, record)
            return self._status_from_record(record)
        finally:
            sandbox.detach()

    def logs(self, run: RunReference, cursor: str | None = None) -> LogPage:
        record = self._read_record(run.run_id)
        run_dir = self._run_dir(run.run_id)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"

        if record.get("collected") and stdout_path.exists() and stderr_path.exists():
            stdout = stdout_path.read_text(errors="replace")
            stderr = stderr_path.read_text(errors="replace")
        else:
            sandbox = self._lookup(run.provider_run_id)
            try:
                stdout = self._read_remote_log(sandbox, STDOUT_PATH)
                stderr = self._read_remote_log(sandbox, STDERR_PATH)
            finally:
                sandbox.detach()
            stdout_path.write_text(stdout)
            stderr_path.write_text(stderr)

        try:
            offset = int(cursor or "0")
        except ValueError as exc:
            raise StateError(f"invalid Modal log cursor: {cursor}") from exc
        if offset < 0:
            raise StateError(f"invalid Modal log cursor: {cursor}")
        page_size = int(self.config.get("log_page_chars", DEFAULT_LOG_PAGE_CHARS))
        end = offset + page_size
        next_cursor = str(end) if end < max(len(stdout), len(stderr)) else None
        return LogPage(
            content=stdout[offset:end],
            stderr=stderr[offset:end],
            next_cursor=next_cursor,
        )

    def cancel(self, run: RunReference) -> None:
        record = self._read_record(run.run_id)
        if record["status"] in _TERMINAL:
            raise StateError(
                f"run {run.run_id} already terminal ({record['status']}); cannot cancel"
            )
        sandbox = self._lookup(run.provider_run_id)
        try:
            sandbox.terminate(wait=True)
        finally:
            sandbox.detach()
        record.update(
            status=RunState.CANCELLED.value,
            exit_code=137,
            finished_at=utcnow(),
        )
        self._write_record(run.run_id, record)

    def collect(self, run: RunReference) -> list[Artifact]:
        record = self._read_record(run.run_id)
        if record["status"] != RunState.SUCCEEDED.value:
            raise StateError(f"cannot collect Modal run {run.run_id} in status {record['status']}")
        if record.get("collected"):
            return self._artifacts_from_manifest(run.run_id)

        spec = ExecutionSpec.model_validate(
            json.loads((self._run_dir(run.run_id) / "execution_spec.json").read_text())
        )
        sandbox = self._lookup(run.provider_run_id)
        artifacts: list[Artifact] = []
        try:
            stdout = self._read_remote_log(sandbox, STDOUT_PATH)
            stderr = self._read_remote_log(sandbox, STDERR_PATH)
            (self._run_dir(run.run_id) / "stdout.log").write_text(stdout)
            (self._run_dir(run.run_id) / "stderr.log").write_text(stderr)

            for remote_file in self._declared_remote_files(sandbox, spec):
                rel = PurePosixPath(remote_file).relative_to(REMOTE_ROOT).as_posix()
                local_path = (self.project_dir / rel).resolve()
                self._require_project_path(local_path, "collected output")
                local_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(prefix=".modal-", dir=local_path.parent)
                os.close(fd)
                try:
                    sandbox.filesystem.copy_to_local(remote_file, temp_name)
                    os.replace(temp_name, local_path)
                except BaseException:
                    Path(temp_name).unlink(missing_ok=True)
                    raise

                artifact_uri = f"file://{local_path}"
                destination = spec.spec.outputs.destination if spec.spec.outputs else None
                if destination is not None:
                    provider = self._storage_for(destination)
                    destination_uri = f"{destination.rstrip('/')}/{rel}"
                    with open(local_path, "rb") as stream:
                        stored = provider.put(destination_uri, stream, sha256_file(local_path))
                    artifact_uri = stored.uri

                artifacts.append(
                    Artifact(
                        path=rel,
                        uri=artifact_uri,
                        type="result",
                        mime=mimetypes.guess_type(str(local_path))[0],
                        size_bytes=local_path.stat().st_size,
                        checksum_sha256=sha256_file(local_path),
                        run_id=run.run_id,
                    )
                )

            self._write_manifest(run.run_id, artifacts)
            sandbox.terminate(wait=True)
            record["collected"] = True
            self._write_record(run.run_id, record)
            return artifacts
        finally:
            sandbox.detach()

    def _modal(self):
        if self._modal_module is not None:
            return self._modal_module
        try:
            import modal
        except ImportError as exc:
            raise StateError(
                "Modal compute requires the optional dependency; install omnigent[modal]"
            ) from exc
        self._modal_module = modal
        return modal

    def _lookup(self, provider_run_id: str):
        if not provider_run_id:
            raise StateError("Modal run is missing its sandbox ID")
        modal = self._modal()
        try:
            return modal.Sandbox.from_id(provider_run_id)
        except Exception as exc:
            if self._is_remote_not_found(exc):
                raise NotFoundError(f"Modal sandbox not found: {provider_run_id}") from exc
            raise

    @staticmethod
    def _is_remote_not_found(exc: Exception) -> bool:
        return "NotFound" in type(exc).__name__

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

    @staticmethod
    def _status_from_record(record: dict) -> RunStatus:
        return RunStatus(
            run_id=record["run_id"],
            status=RunState(record["status"]),
            exit_code=record.get("exit_code"),
            queued_at=record.get("queued_at"),
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
        )

    def _require_project_path(self, path: Path, label: str) -> None:
        if not (path == self.project_dir or path.is_relative_to(self.project_dir)):
            raise StateError(f"{label} outside project: {path}")

    @staticmethod
    def _relative_remote_path(value: str) -> PurePosixPath:
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise StateError(f"path must be project-relative: {value}")
        return path

    @staticmethod
    def _default_input_path(uri: str) -> str:
        name = PurePosixPath(uri.split("?", 1)[0]).name
        if not name:
            raise StateError(f"input URI has no filename; declare input.path: {uri}")
        return f"inputs/{name}"

    def _remote_working_directory(self, plan: ExecutionPlan) -> str:
        relative = Path(plan.working_directory).relative_to(self.project_dir).as_posix()
        return str(REMOTE_ROOT if relative == "." else REMOTE_ROOT / relative)

    @staticmethod
    def _wrapper_script(command: list[str], working_directory: str, runtime: int) -> str:
        command_text = shlex.join(command)
        return "\n".join(
            [
                "set +e",
                f": > {STDOUT_PATH}",
                f": > {STDERR_PATH}",
                f"while [ ! -f {READY_PATH} ]; do sleep 0.1; done",
                f"cd {shlex.quote(working_directory)}",
                (
                    f"timeout --signal=TERM --kill-after=5 {runtime}s {command_text} "
                    f"> {STDOUT_PATH} 2> {STDERR_PATH}"
                ),
                "rc=$?",
                f"printf '%s\\n' \"$rc\" > {EXIT_CODE_PATH}",
                "while :; do sleep 3600; done",
            ]
        )

    def _stage_source(self, sandbox, source_root: Path) -> None:
        excluded = _DEFAULT_SOURCE_EXCLUDES | set(self.config.get("source_excludes", []))
        for current, directory_names, file_names in os.walk(source_root, followlinks=False):
            current_path = Path(current)
            retained_directories = []
            for name in directory_names:
                path = current_path / name
                if path.is_symlink():
                    raise StateError(f"refusing to stage symlinked source directory: {path}")
                if name not in excluded:
                    retained_directories.append(name)
            directory_names[:] = retained_directories
            for name in file_names:
                path = current_path / name
                if name in excluded:
                    continue
                if path.is_symlink():
                    raise StateError(f"refusing to stage symlinked source file: {path}")
                rel = path.relative_to(self.project_dir).as_posix()
                sandbox.filesystem.copy_from_local(path, str(REMOTE_ROOT / rel))

    def _stage_input(self, sandbox, uri: str, declared_path: str | None) -> None:
        target = declared_path or self._default_input_path(uri)
        remote_path = str(REMOTE_ROOT / self._relative_remote_path(target))
        provider = self._storage_for(uri)
        reader = provider.open_read(uri)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp:
                temp_path = Path(temp.name)
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    temp.write(chunk)
            sandbox.filesystem.copy_from_local(temp_path, remote_path)
        finally:
            reader.close()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _storage_for(self, uri: str):
        if self._storage_resolver is None:
            raise StateError(f"Modal provider has no storage broker for URI: {uri}")
        return self._storage_resolver(uri)

    @staticmethod
    def _read_remote_log(sandbox, path: str) -> str:
        try:
            return sandbox.filesystem.read_text(path)
        except Exception as exc:
            if ModalComputeProvider._is_remote_not_found(exc):
                return ""
            raise

    def _declared_remote_files(self, sandbox, spec: ExecutionSpec) -> list[str]:
        outputs = spec.spec.outputs
        if outputs is None:
            return []
        candidates = [*outputs.files]
        if outputs.path:
            candidates.append(outputs.path)
        found: list[str] = []
        for rel in candidates:
            remote = str(REMOTE_ROOT / self._relative_remote_path(rel))
            found.extend(self._walk_remote_output(sandbox, remote))
        return list(dict.fromkeys(found))

    def _walk_remote_output(self, sandbox, remote_path: str) -> list[str]:
        try:
            info = sandbox.filesystem.stat(remote_path)
        except Exception as exc:
            if self._is_remote_not_found(exc):
                raise StateError(f"declared Modal output was not produced: {remote_path}") from exc
            raise
        kind = getattr(info.type, "value", info.type)
        if "dir" not in str(kind).lower():
            return [remote_path]
        files = []
        for entry in sandbox.filesystem.list_files(remote_path):
            child = entry.name
            if not str(child).startswith("/"):
                child = f"{remote_path.rstrip('/')}/{child}"
            files.extend(self._walk_remote_output(sandbox, str(child)))
        return files

    def _write_manifest(self, run_id: str, artifacts: list[Artifact]) -> None:
        payload = [artifact.model_dump(mode="json") for artifact in artifacts]
        (self._run_dir(run_id) / "outputs_manifest.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True)
        )

    def _artifacts_from_manifest(self, run_id: str) -> list[Artifact]:
        path = self._run_dir(run_id) / "outputs_manifest.json"
        if not path.exists():
            raise StateError(f"Modal run {run_id} is marked collected but has no manifest")
        return [Artifact.model_validate(item) for item in json.loads(path.read_text())]
