# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import shutil
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from omnisci.compute.base import ExecutionSpec
from omnisci.compute.modal import ModalComputeProvider
from omnisci.domain.schemas import RunState
from omnisci.errors import StateError
from omnisci.storage.base import ObjectMetadata
from omnisci.storage.local import LocalStorageProvider


class RemoteNotFoundError(Exception):
    pass


class FakeFilesystem:
    def __init__(self, sandbox, root: Path):
        self.sandbox = sandbox
        self.root = root

    def _local(self, remote: str) -> Path:
        # A terminated sandbox takes its filesystem with it. Modelling that is
        # what makes "save the logs before releasing it" a real assertion --
        # a fake that stays readable after terminate() cannot catch losing them.
        if self.sandbox.terminated:
            raise RemoteNotFoundError(remote)
        return self.root / remote.removeprefix("/")

    def copy_from_local(self, local_path, remote_path: str) -> None:
        destination = self._local(remote_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, destination)

    def copy_to_local(self, remote_path: str, local_path) -> None:
        source = self._local(remote_path)
        if not source.is_file():
            raise RemoteNotFoundError(remote_path)
        shutil.copyfile(source, local_path)

    def write_text(self, data: str, remote_path: str) -> None:
        path = self._local(remote_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        if remote_path == "/tmp/omnisci-ready":
            self.sandbox.on_ready()

    def read_text(self, remote_path: str) -> str:
        path = self._local(remote_path)
        if not path.is_file():
            raise RemoteNotFoundError(remote_path)
        return path.read_text()

    def stat(self, remote_path: str):
        path = self._local(remote_path)
        if not path.exists():
            raise RemoteNotFoundError(remote_path)
        kind = "directory" if path.is_dir() else "file"
        return SimpleNamespace(type=SimpleNamespace(value=kind), size=path.stat().st_size)

    def list_files(self, remote_path: str):
        path = self._local(remote_path)
        if not path.is_dir():
            raise RemoteNotFoundError(remote_path)
        return [
            SimpleNamespace(
                name=f"{remote_path.rstrip('/')}/{child.name}",
                type=SimpleNamespace(value="directory" if child.is_dir() else "file"),
                size=child.stat().st_size,
            )
            for child in sorted(path.iterdir())
        ]


class FakeSandbox:
    def __init__(self, module, object_id: str, root: Path, args: tuple, kwargs: dict):
        self.module = module
        self.object_id = object_id
        self.args = args
        self.kwargs = kwargs
        self.filesystem = FakeFilesystem(self, root)
        self.terminated = False
        self.detach_count = 0

    def on_ready(self) -> None:
        if not self.module.auto_finish:
            return
        self.filesystem.write_text("modal analysis complete\n", "/tmp/omnisci-stdout.log")
        self.filesystem.write_text("", "/tmp/omnisci-stderr.log")
        self.filesystem.write_text('{"difference": 2.5}\n', "/workspace/results/out.json")
        self.filesystem.write_text(str(self.module.exit_code), "/tmp/omnisci-exit-code")

    def poll(self):
        return 137 if self.terminated else None

    def terminate(self, *, wait: bool = False):
        self.terminated = True
        return 137 if wait else None

    def detach(self) -> None:
        self.detach_count += 1


class FakeModal:
    def __init__(self, root: Path, *, auto_finish: bool = True, exit_code: int = 0):
        self.root = root
        self.auto_finish = auto_finish
        self.exit_code = exit_code
        self.sandboxes: dict[str, FakeSandbox] = {}
        outer = self

        class App:
            @staticmethod
            def lookup(name: str, *, create_if_missing: bool):
                return {"name": name, "create_if_missing": create_if_missing}

        class Image:
            @staticmethod
            def from_registry(reference: str):
                return {"reference": reference}

        class Sandbox:
            @staticmethod
            def create(*args, **kwargs):
                object_id = f"sb-{len(outer.sandboxes) + 1}"
                sandbox = FakeSandbox(
                    outer,
                    object_id,
                    outer.root / object_id,
                    args,
                    kwargs,
                )
                outer.sandboxes[object_id] = sandbox
                return sandbox

            @staticmethod
            def from_id(object_id: str):
                try:
                    return outer.sandboxes[object_id]
                except KeyError as exc:
                    raise RemoteNotFoundError(object_id) from exc

        self.App = App
        self.Image = Image
        self.Sandbox = Sandbox


def modal_spec(
    *,
    image: str = "python:3.12-slim",
    input_uri: str = "data/input.txt",
    destination: str | None = None,
) -> ExecutionSpec:
    return ExecutionSpec.model_validate(
        {
            "apiVersion": "science.omnigent.ai/v1alpha1",
            "kind": "Execution",
            "metadata": {"name": "modal-analysis"},
            "spec": {
                "provider": "modal",
                "mode": "sandbox",
                "source": {"workingDirectory": "."},
                "command": ["python", "analyses/job.py"],
                "environment": {"image": image},
                "resources": {
                    "cpu": 2,
                    "memoryGiB": 4,
                    "gpu": {"type": "L4", "count": 2},
                },
                "inputs": [{"uri": input_uri, "path": "inputs/input.txt", "readOnly": True}],
                "outputs": {
                    "files": ["results/out.json"],
                    "destination": destination,
                },
                "limits": {"maxRuntimeMinutes": 5},
                "network": {"mode": "deny"},
            },
        }
    )


@pytest.fixture
def modal_project(tmp_path):
    project = tmp_path / "project"
    (project / "analyses").mkdir(parents=True)
    (project / "data").mkdir()
    (project / ".omnisci").mkdir()
    (project / "analyses" / "job.py").write_text("print('work')\n")
    (project / "data" / "input.txt").write_text("declared input\n")
    (project / ".omnisci" / "credential").write_text("must-not-stage\n")
    (project / ".env").write_text("TOKEN=must-not-stage\n")
    return project


def test_modal_submit_reconcile_logs_collect_and_restart(modal_project, tmp_path):
    fake_modal = FakeModal(tmp_path / "remote")
    storage = LocalStorageProvider(modal_project)
    provider = ModalComputeProvider(
        project_dir=modal_project,
        runs_dir=modal_project / ".omnisci" / "runs",
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: storage,
    )

    plan = provider.validate(modal_spec())
    reference = provider.submit(plan)
    sandbox = fake_modal.sandboxes[reference.provider_run_id]

    assert sandbox.kwargs["block_network"] is True
    assert sandbox.kwargs["cpu"] == 2
    assert sandbox.kwargs["memory"] == 4096
    assert sandbox.kwargs["gpu"] == "L4:2"
    assert sandbox.kwargs["timeout"] == 360
    assert sandbox.filesystem._local("/workspace/analyses/job.py").is_file()
    staged_input = sandbox.filesystem._local("/workspace/inputs/input.txt")
    assert staged_input.read_text() == "declared input\n"
    assert not sandbox.filesystem._local("/workspace/.omnisci/credential").exists()
    assert not sandbox.filesystem._local("/workspace/.env").exists()

    status = provider.status(reference)
    assert status.status == RunState.SUCCEEDED
    assert status.exit_code == 0
    assert "modal analysis complete" in provider.logs(reference).content

    restarted = ModalComputeProvider(
        project_dir=modal_project,
        runs_dir=modal_project / ".omnisci" / "runs",
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: storage,
    )
    assert restarted.status(reference).status == RunState.SUCCEEDED

    artifacts = restarted.collect(reference)
    assert len(artifacts) == 1
    assert artifacts[0].path == "results/out.json"
    assert (modal_project / "results" / "out.json").read_text() == '{"difference": 2.5}\n'
    assert sandbox.terminated
    assert restarted.collect(reference)[0].checksum_sha256 == artifacts[0].checksum_sha256


def test_modal_cancel_persists_terminal_state(modal_project, tmp_path):
    fake_modal = FakeModal(tmp_path / "remote", auto_finish=False)
    provider = ModalComputeProvider(
        project_dir=modal_project,
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: LocalStorageProvider(modal_project),
    )
    reference = provider.submit(provider.validate(modal_spec()))

    assert provider.status(reference).status == RunState.RUNNING
    provider.cancel(reference)
    assert provider.status(reference).status == RunState.CANCELLED
    with pytest.raises(StateError, match="already terminal"):
        provider.cancel(reference)


def test_modal_stages_and_uploads_through_storage_broker(modal_project, tmp_path):
    class ObjectStorage:
        def __init__(self):
            self.uploads = {}

        def open_read(self, uri: str):
            assert uri == "s3://science/input.txt"
            return BytesIO(b"remote declared input\n")

        def put(self, uri: str, stream, checksum: str):
            self.uploads[uri] = (stream.read(), checksum)
            return ObjectMetadata(uri=uri, checksum_sha256=checksum)

    fake_modal = FakeModal(tmp_path / "remote")
    object_storage = ObjectStorage()
    provider = ModalComputeProvider(
        project_dir=modal_project,
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda uri: (
            object_storage if uri.startswith("s3://") else LocalStorageProvider(modal_project)
        ),
    )
    spec = modal_spec(
        input_uri="s3://science/input.txt",
        destination="s3://science/results",
    )
    reference = provider.submit(provider.validate(spec))
    assert provider.status(reference).status == RunState.SUCCEEDED

    # Staging is observable only while the sandbox is alive; collect() releases it.
    sandbox = fake_modal.sandboxes[reference.provider_run_id]
    assert sandbox.filesystem._local("/workspace/inputs/input.txt").read_bytes() == (
        b"remote declared input\n"
    )

    artifact = provider.collect(reference)[0]

    assert artifact.uri == "s3://science/results/results/out.json"
    uploaded, checksum = object_storage.uploads[artifact.uri]
    assert uploaded == b'{"difference": 2.5}\n'
    assert checksum == artifact.checksum_sha256


def test_modal_timeout_exit_code_reconciles(modal_project, tmp_path):
    fake_modal = FakeModal(tmp_path / "remote", exit_code=124)
    provider = ModalComputeProvider(
        project_dir=modal_project,
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: LocalStorageProvider(modal_project),
    )
    reference = provider.submit(provider.validate(modal_spec()))

    assert provider.status(reference).status == RunState.TIMEOUT


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(1, RunState.FAILED), (124, RunState.TIMEOUT)],
)
def test_modal_releases_the_sandbox_on_a_terminal_failure(
    modal_project, tmp_path, exit_code, expected
):
    """A run nothing will collect must not keep billing.

    The wrapper parks on a sleep loop so ``collect`` can read the filesystem,
    and only a succeeded run is ever collected -- so any other terminal state
    used to idle until Modal's own runtime+grace timeout.
    """
    fake_modal = FakeModal(tmp_path / "remote", exit_code=exit_code)
    provider = ModalComputeProvider(
        project_dir=modal_project,
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: LocalStorageProvider(modal_project),
    )
    reference = provider.submit(provider.validate(modal_spec()))
    sandbox = fake_modal.sandboxes[reference.provider_run_id]

    assert provider.status(reference).status == expected
    assert sandbox.terminated

    # The logs live on the sandbox filesystem, so they must be pulled down
    # before it goes away -- a released run still has to be debuggable.
    assert provider.logs(reference).content == "modal analysis complete\n"


def test_modal_keeps_the_sandbox_alive_until_a_success_is_collected(modal_project, tmp_path):
    """The mirror of the release test: success must NOT be torn down early,
    because ``collect`` still has to read the declared outputs off it."""
    fake_modal = FakeModal(tmp_path / "remote")
    provider = ModalComputeProvider(
        project_dir=modal_project,
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: LocalStorageProvider(modal_project),
    )
    reference = provider.submit(provider.validate(modal_spec()))
    sandbox = fake_modal.sandboxes[reference.provider_run_id]

    assert provider.status(reference).status == RunState.SUCCEEDED
    assert not sandbox.terminated

    provider.collect(reference)
    assert sandbox.terminated


def test_modal_cancel_saves_logs_before_terminating(modal_project, tmp_path):
    """Cancel used to terminate without pulling the logs down, so a cancelled
    run lost them: ``logs`` would then read a filesystem that no longer exists."""
    fake_modal = FakeModal(tmp_path / "remote", auto_finish=False)
    provider = ModalComputeProvider(
        project_dir=modal_project,
        config={"collection_grace_seconds": 60},
        modal_module=fake_modal,
        storage_resolver=lambda _uri: LocalStorageProvider(modal_project),
    )
    reference = provider.submit(provider.validate(modal_spec()))
    sandbox = fake_modal.sandboxes[reference.provider_run_id]
    sandbox.filesystem.write_text("partial progress\n", "/tmp/omnisci-stdout.log")

    provider.cancel(reference)
    assert sandbox.terminated
    assert provider.logs(reference).content == "partial progress\n"


def test_modal_missing_remote_sandbox_reconciles_as_failed(modal_project, tmp_path):
    fake_modal = FakeModal(tmp_path / "remote", auto_finish=False)
    provider = ModalComputeProvider(
        project_dir=modal_project,
        modal_module=fake_modal,
        storage_resolver=lambda _uri: LocalStorageProvider(modal_project),
    )
    reference = provider.submit(provider.validate(modal_spec()))
    del fake_modal.sandboxes[reference.provider_run_id]

    assert provider.status(reference).status == RunState.FAILED
    assert provider.status(reference).status == RunState.FAILED


def test_modal_validation_rejects_local_image_and_path_escape(modal_project, tmp_path):
    provider = ModalComputeProvider(
        project_dir=modal_project,
        modal_module=FakeModal(tmp_path / "remote"),
    )
    with pytest.raises(StateError, match="registry image"):
        provider.validate(modal_spec(image="local"))

    payload = modal_spec().model_dump(by_alias=True, mode="json")
    payload["spec"]["outputs"] = {"files": ["../escape.txt"]}
    with pytest.raises(StateError, match="outside project"):
        provider.validate(ExecutionSpec.model_validate(payload))
