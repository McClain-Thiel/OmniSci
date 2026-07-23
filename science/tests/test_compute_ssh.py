# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from omnisci.compute.base import ExecutionSpec
from omnisci.compute.ssh import SshComputeProvider
from omnisci.domain.schemas import RunState
from omnisci.errors import StateError


class FakeSshRunner:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        archive_name: str = "results/out.json",
        cleanup_failures: int = 0,
    ):
        self.exit_code = exit_code
        self.archive_name = archive_name
        self.cleanup_failures = cleanup_failures
        self.commands: list[list[str]] = []
        self.staged_names: list[str] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        remote_command = command[-1] if command[0] == "ssh" else ""
        if command[0] == "scp":
            with tarfile.open(command[-2], "r:gz") as archive:
                self.staged_names = archive.getnames()
        if "tar --create --file=-" in remote_command:
            payload = b'{"host": "ssh-test"}\n'
            info = tarfile.TarInfo(self.archive_name)
            info.size = len(payload)
            with tarfile.open(fileobj=kwargs["stdout"], mode="w|") as archive:
                archive.addfile(info, io.BytesIO(payload))
        if remote_command.startswith("rm -rf --") and self.cleanup_failures:
            self.cleanup_failures -= 1
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"cleanup unavailable")
        if "timeout --signal=TERM" in remote_command:
            return SimpleNamespace(
                returncode=self.exit_code,
                stdout=b"ssh analysis complete\n",
                stderr=b"remote warning\n" if self.exit_code else b"",
            )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


@pytest.fixture
def ssh_project(tmp_path):
    project = tmp_path / "project"
    (project / "analyses").mkdir(parents=True)
    (project / "results").mkdir()
    (project / ".omnisci").mkdir()
    (project / "analyses" / "job.py").write_text("print('work')\n")
    (project / ".omnisci" / "credential").write_text("must-not-stage\n")
    (project / ".env").write_text("TOKEN=must-not-stage\n")
    (project / ".env.production").write_text("TOKEN=also-must-not-stage\n")
    return project


@pytest.fixture
def ssh_config(tmp_path):
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("test fixture, not a real key\n")
    known_hosts.write_text("host ssh-ed25519 fixture\n")
    return {
        "host": "compute.example.edu",
        "user": "researcher",
        "port": 2222,
        "identity_file": str(identity),
        "known_hosts_file": str(known_hosts),
        "remote_root": "/tmp/omnisci-tests",
        "max_runtime_minutes": 30,
    }


def ssh_spec(**updates) -> ExecutionSpec:
    payload = {
        "apiVersion": "science.omnigent.ai/v1alpha1",
        "kind": "Execution",
        "metadata": {"name": "ssh-analysis"},
        "spec": {
            "provider": "ssh",
            "mode": "ssh",
            "source": {"workingDirectory": "."},
            "command": ["python3", "analyses/job.py"],
            "environment": {"image": "host"},
            "outputs": {"files": ["results/out.json"]},
            "limits": {"maxRuntimeMinutes": 5},
            "network": {"mode": "allow"},
        },
    }
    payload["spec"].update(updates)
    return ExecutionSpec.model_validate(payload)


def test_ssh_submit_logs_collect_and_restart(ssh_project, ssh_config):
    runner = FakeSshRunner()
    provider = SshComputeProvider(
        project_dir=ssh_project,
        runs_dir=ssh_project / ".omnisci" / "runs",
        config=ssh_config,
        command_runner=runner,
    )

    reference = provider.submit(provider.validate(ssh_spec()))

    assert provider.status(reference).status == RunState.SUCCEEDED
    assert provider.logs(reference).content == "ssh analysis complete\n"
    assert "analyses/job.py" in runner.staged_names
    assert not any(name.startswith(".omnisci") for name in runner.staged_names)
    assert ".env" not in runner.staged_names
    assert ".env.production" not in runner.staged_names
    ssh_command = next(command for command in runner.commands if command[0] == "ssh")
    assert "StrictHostKeyChecking=yes" in ssh_command
    assert f"UserKnownHostsFile={ssh_config['known_hosts_file']}" in ssh_command

    artifacts = provider.collect(reference)
    assert len(artifacts) == 1
    assert artifacts[0].path == "results/out.json"
    assert (ssh_project / "results" / "out.json").read_text() == '{"host": "ssh-test"}\n'
    assert any("rm -rf -- /tmp/omnisci-tests/run_" in command[-1] for command in runner.commands)

    restarted = SshComputeProvider(
        project_dir=ssh_project,
        runs_dir=ssh_project / ".omnisci" / "runs",
        config=ssh_config,
        command_runner=runner,
    )
    assert restarted.collect(reference)[0].checksum_sha256 == artifacts[0].checksum_sha256


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(17, RunState.FAILED), (124, RunState.TIMEOUT)],
)
def test_ssh_terminal_exit_states(ssh_project, ssh_config, exit_code, expected):
    runner = FakeSshRunner(exit_code=exit_code)
    provider = SshComputeProvider(
        project_dir=ssh_project,
        config=ssh_config,
        command_runner=runner,
    )
    reference = provider.submit(provider.validate(ssh_spec()))

    assert provider.status(reference).status == expected
    assert provider.status(reference).exit_code == exit_code


def test_ssh_validation_rejects_unenforceable_specs(ssh_project, ssh_config):
    provider = SshComputeProvider(project_dir=ssh_project, config=ssh_config)

    with pytest.raises(StateError, match="mode=ssh"):
        provider.validate(ssh_spec(mode="sandbox"))
    with pytest.raises(StateError, match=r"network\.mode=deny"):
        provider.validate(ssh_spec(network={"mode": "deny"}))
    with pytest.raises(StateError, match="resource requests"):
        provider.validate(ssh_spec(resources={"cpu": 2}))
    with pytest.raises(StateError, match="does not stage execution-spec inputs"):
        provider.validate(ssh_spec(inputs=[{"uri": "s3://bucket/input"}]))
    with pytest.raises(StateError, match="outside project"):
        provider.validate(ssh_spec(source={"workingDirectory": "../escape"}))


def test_ssh_rejects_unsafe_collected_archive(ssh_project, ssh_config):
    runner = FakeSshRunner(archive_name="../escape.txt")
    provider = SshComputeProvider(
        project_dir=ssh_project,
        config=ssh_config,
        command_runner=runner,
    )
    reference = provider.submit(provider.validate(ssh_spec()))

    with pytest.raises(StateError, match="unsafe path"):
        provider.collect(reference)
    assert not (ssh_project.parent / "escape.txt").exists()


def test_ssh_retries_remote_cleanup_after_collection(ssh_project, ssh_config):
    runner = FakeSshRunner(cleanup_failures=1)
    provider = SshComputeProvider(
        project_dir=ssh_project,
        config=ssh_config,
        command_runner=runner,
    )
    reference = provider.submit(provider.validate(ssh_spec()))

    with pytest.raises(StateError, match="remote cleanup failed"):
        provider.collect(reference)
    assert provider._read_record(reference.run_id)["remote_cleaned"] is False

    artifacts = provider.collect(reference)
    assert artifacts[0].path == "results/out.json"
    assert provider._read_record(reference.run_id)["remote_cleaned"] is True


def test_ssh_uses_configured_runtime_as_implicit_limit(ssh_project, ssh_config):
    config = dict(ssh_config)
    config["max_runtime_minutes"] = 15
    runner = FakeSshRunner()
    provider = SshComputeProvider(
        project_dir=ssh_project,
        config=config,
        command_runner=runner,
    )

    reference = provider.submit(provider.validate(ssh_spec(limits={})))

    assert provider.status(reference).status == RunState.SUCCEEDED
    assert any("timeout --signal=TERM --kill-after=5s 900s" in cmd[-1] for cmd in runner.commands)


def test_ssh_configuration_requires_known_host_file(ssh_project, ssh_config):
    bad = dict(ssh_config)
    bad["known_hosts_file"] = str(Path(ssh_config["known_hosts_file"]).with_name("missing"))
    with pytest.raises(StateError, match="known-hosts file does not exist"):
        SshComputeProvider(project_dir=ssh_project, config=bad)

    bad = dict(ssh_config)
    bad["host"] = "-oProxyCommand=bad"
    with pytest.raises(StateError, match="invalid SSH host"):
        SshComputeProvider(project_dir=ssh_project, config=bad)

    bad = dict(ssh_config)
    bad["remote_root"] = "/tmp/omnisci; touch /tmp/bad"
    with pytest.raises(StateError, match="unsupported characters"):
        SshComputeProvider(project_dir=ssh_project, config=bad)
