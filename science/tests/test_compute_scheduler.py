# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from omnisci.compute.base import ExecutionSpec
from omnisci.compute.scheduler import QsubComputeProvider, SlurmComputeProvider
from omnisci.domain.schemas import RunState
from omnisci.errors import StateError


def _remote_script(command: list[str], **kwargs) -> str:
    """Recover the script an ssh call carries, asserting the POSIX shape holds.

    The provider names ``/bin/sh`` as the remote command and pipes the script on
    stdin, so the account's login shell never parses it -- csh/tcsh accounts,
    common on university clusters, reject POSIX syntax outright and even mangle
    the quoting of a ``-c`` argument. Asserting the shape here makes this
    harness a regression guard for that.
    """
    if command[0] != "ssh":
        return ""
    assert command[-1] == "/bin/sh", f"remote command not run under /bin/sh: {command}"
    return (kwargs.get("input") or b"").decode()


class FakeSchedulerRunner:
    def __init__(self, scheduler: str):
        self.scheduler = scheduler
        self.commands: list[list[str]] = []
        self.scripts: list[str] = []
        self.job_script = ""
        self.scheduler_state = "PENDING" if scheduler == "slurm" else "Q"
        self.exit_code: int | None = None
        self.stdout = b"cluster output\n"
        self.stderr = b""

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        remote_command = _remote_script(command, **kwargs)
        self.scripts.append(remote_command)
        if command[0] == "scp":
            source = Path(command[-2])
            if source.name == "job.sh":
                self.job_script = source.read_text()
            return self._completed()
        if remote_command.startswith("sbatch --parsable"):
            return self._completed(stdout=b"4815;cluster\n")
        if remote_command.startswith("qsub "):
            output = (
                b"8123.server\n"
                if self.scheduler == "pbs"
                else b'Your job 8123 ("run") has been submitted\n'
            )
            return self._completed(stdout=output)
        if ".omnisci-exit-code" in remote_command and "test -f" in remote_command:
            output = b"" if self.exit_code is None else f"{self.exit_code}\n".encode()
            return self._completed(stdout=output)
        if "squeue --noheader" in remote_command:
            return self._completed(stdout=f"SQUEUE|{self.scheduler_state}\n".encode())
        if "qstat -xf" in remote_command:
            return self._completed(
                stdout=(f"Job Id: 8123.server\n    job_state = {self.scheduler_state}\n").encode()
            )
        if "omnisci_state=$(qstat" in remote_command:
            return self._completed(stdout=f"QSTAT|{self.scheduler_state}\n".encode())
        if "stdout.log" in remote_command and "test -f" in remote_command:
            return self._completed(stdout=self.stdout)
        if "stderr.log" in remote_command and "test -f" in remote_command:
            return self._completed(stdout=self.stderr)
        if "tar --create --file=-" in remote_command:
            payload = b'{"scheduler": "test"}\n'
            info = tarfile.TarInfo("results/out.json")
            info.size = len(payload)
            with tarfile.open(fileobj=kwargs["stdout"], mode="w|") as archive:
                archive.addfile(info, io.BytesIO(payload))
            return self._completed()
        return self._completed()

    @staticmethod
    def _completed(*, stdout=b"", stderr=b"", returncode=0):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def scheduler_project(tmp_path):
    project = tmp_path / "project"
    (project / "analyses").mkdir(parents=True)
    (project / "results").mkdir()
    (project / "analyses" / "job.py").write_text("print('work')\n")
    return project


@pytest.fixture
def scheduler_config(tmp_path):
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("test fixture\n")
    known_hosts.write_text("login.example.edu ssh-ed25519 fixture\n")
    return {
        "host": "login.example.edu",
        "user": "researcher",
        "identity_file": str(identity),
        "known_hosts_file": str(known_hosts),
        "remote_root": "/scratch/researcher/omnisci",
        "max_runtime_minutes": 240,
    }


def scheduler_spec(provider: str, **updates) -> ExecutionSpec:
    payload = {
        "apiVersion": "science.omnigent.ai/v1alpha1",
        "kind": "Execution",
        "metadata": {"name": "genome scan"},
        "spec": {
            "provider": provider,
            "mode": "batch",
            "source": {"workingDirectory": "."},
            "command": ["python3", "analyses/job.py", "--sample", "A 1"],
            "environment": {"image": "host"},
            "resources": {
                "cpu": 8,
                "memoryGiB": 32,
                "gpu": {"type": "a100", "count": 2},
            },
            "outputs": {"files": ["results/out.json"]},
            "limits": {"maxRuntimeMinutes": 90},
        },
    }
    payload["spec"].update(updates)
    return ExecutionSpec.model_validate(payload)


def test_slurm_submit_reconcile_logs_collect_and_restart(scheduler_project, scheduler_config):
    runner = FakeSchedulerRunner("slurm")
    config = {**scheduler_config, "partition": "gpu", "account": "lab", "qos": "normal"}
    provider = SlurmComputeProvider(
        project_dir=scheduler_project,
        runs_dir=scheduler_project / ".omnisci" / "runs",
        config=config,
        command_runner=runner,
    )

    reference = provider.submit(provider.validate(scheduler_spec("slurm")))

    assert reference.provider_run_id == "4815"
    assert provider.status(reference).status == RunState.QUEUED
    assert "#SBATCH --time=01:30:00" in runner.job_script
    assert "#SBATCH --cpus-per-task=8" in runner.job_script
    assert "#SBATCH --mem=32768M" in runner.job_script
    assert "#SBATCH --gres=gpu:a100:2" in runner.job_script
    assert "#SBATCH --partition=gpu" in runner.job_script
    assert "python3 analyses/job.py --sample 'A 1'" in runner.job_script

    runner.scheduler_state = "RUNNING"
    assert provider.status(reference).status == RunState.RUNNING
    assert provider.logs(reference).content == "cluster output\n"

    restarted = SlurmComputeProvider(
        project_dir=scheduler_project,
        runs_dir=scheduler_project / ".omnisci" / "runs",
        config=config,
        command_runner=runner,
    )
    runner.exit_code = 0
    assert restarted.status(reference).status == RunState.SUCCEEDED
    artifacts = restarted.collect(reference)
    assert artifacts[0].path == "results/out.json"
    assert (scheduler_project / "results" / "out.json").is_file()


@pytest.mark.parametrize("dialect", ["pbs", "sge"])
def test_qsub_renders_dialect_and_reconciles(scheduler_project, scheduler_config, dialect):
    runner = FakeSchedulerRunner(dialect)
    provider = QsubComputeProvider(
        project_dir=scheduler_project,
        config={
            **scheduler_config,
            "dialect": dialect,
            "queue": "long.q",
            "account": "barnes",
            "parallel_environment": "smp",
            "gpu_resource": "ngpus" if dialect == "pbs" else "gpu",
        },
        command_runner=runner,
    )

    reference = provider.submit(provider.validate(scheduler_spec("qsub")))

    assert reference.provider_run_id in {"8123.server", "8123"}
    if dialect == "pbs":
        assert "#PBS -l walltime=01:30:00" in runner.job_script
        assert "#PBS -l select=1:ncpus=8:mem=32768mb:ngpus=2:gpu_type=a100" in runner.job_script
        assert "#PBS -q long.q" in runner.job_script
        runner.scheduler_state = "R"
    else:
        assert "#$ -l h_rt=01:30:00" in runner.job_script
        assert "#$ -pe smp 8" in runner.job_script
        assert "#$ -l h_vmem=4096M" in runner.job_script
        assert "#$ -l gpu=2" in runner.job_script
        assert "#$ -q long.q" in runner.job_script
        runner.scheduler_state = "r"
    assert provider.status(reference).status == RunState.RUNNING

    runner.exit_code = 17
    terminal = provider.status(reference)
    assert terminal.status == RunState.FAILED
    assert terminal.exit_code == 17


@pytest.mark.parametrize(
    ("provider_type", "scheduler"),
    [(SlurmComputeProvider, "slurm"), (QsubComputeProvider, "pbs")],
)
def test_scheduler_cancel_uses_native_command(
    scheduler_project, scheduler_config, provider_type, scheduler
):
    runner = FakeSchedulerRunner(scheduler)
    config = {**scheduler_config, **({"dialect": "pbs"} if scheduler == "pbs" else {})}
    provider = provider_type(
        project_dir=scheduler_project,
        config=config,
        command_runner=runner,
    )
    name = "slurm" if scheduler == "slurm" else "qsub"
    reference = provider.submit(provider.validate(scheduler_spec(name)))

    provider.cancel(reference)

    assert provider.status(reference).status == RunState.CANCELLED
    expected = "scancel 4815" if scheduler == "slurm" else "qdel 8123.server"
    assert any(script == expected for script in runner.scripts)


def test_scheduler_rejects_unsafe_directives_and_job_ids(scheduler_project, scheduler_config):
    with pytest.raises(StateError, match="invalid scheduler partition"):
        SlurmComputeProvider(
            project_dir=scheduler_project,
            config={**scheduler_config, "partition": "gpu\n#SBATCH --wrap=bad"},
        )

    runner = FakeSchedulerRunner("slurm")
    original = runner.__call__

    def malformed_job_id(command, **kwargs):
        if _remote_script(command, **kwargs).startswith("sbatch --parsable"):
            return SimpleNamespace(returncode=0, stdout=b"4815; rm -rf /\n", stderr=b"")
        return original(command, **kwargs)

    provider = SlurmComputeProvider(
        project_dir=scheduler_project,
        config=scheduler_config,
        command_runner=malformed_job_id,
    )
    with pytest.raises(StateError, match="invalid Slurm job id"):
        provider.submit(provider.validate(scheduler_spec("slurm")))


def test_scheduler_validation_rejects_unsupported_specs(scheduler_project, scheduler_config):
    provider = SlurmComputeProvider(project_dir=scheduler_project, config=scheduler_config)

    with pytest.raises(StateError, match="mode=batch"):
        provider.validate(scheduler_spec("slurm", mode="ssh"))
    with pytest.raises(StateError, match=r"network\.mode=deny"):
        provider.validate(scheduler_spec("slurm", network={"mode": "deny"}))
    with pytest.raises(StateError, match="CPU request must be positive"):
        provider.validate(scheduler_spec("slurm", resources={"cpu": 0}))


def _check_runner(*, connect_rc=0, connect_stderr=b"", found=None):
    """Runner that answers the connectivity probe, then the binary probe."""

    def run(command, **kwargs):
        script = _remote_script(command, **kwargs)
        if "omnisci-ok" in script:
            return SimpleNamespace(
                returncode=connect_rc,
                stdout=b"omnisci-ok" if connect_rc == 0 else b"",
                stderr=connect_stderr,
            )
        lines = "".join(f"{name}={path}\n" for name, path in (found or {}).items())
        return SimpleNamespace(returncode=0, stdout=lines.encode(), stderr=b"")

    return run


def test_check_reports_auth_failure_with_an_interactive_login_remedy(
    scheduler_project, scheduler_config
):
    """A rejected key must name the cause and the way out, not echo raw stderr.

    OmniSci connects with BatchMode, so a host behind a password or MFA prompt
    can only be reached through a session the operator opens themselves.
    """
    provider = QsubComputeProvider(
        project_dir=scheduler_project,
        config=scheduler_config,
        command_runner=_check_runner(
            connect_rc=255,
            connect_stderr=b"athiel@login: Permission denied (publickey,password).",
        ),
    )
    check = provider.check()
    assert check.status == "auth_failed"
    assert not check.ok
    assert "ssh login.example.edu" in check.remedy
    assert "ControlMaster" in check.remedy


def test_check_reports_a_missing_scheduler_rather_than_queueing_forever(
    scheduler_project, scheduler_config
):
    """A reachable host with no scheduler is the classic jump-host mistake."""
    provider = SlurmComputeProvider(
        project_dir=scheduler_project,
        config=scheduler_config,
        command_runner=_check_runner(
            found={"sbatch": "-", "squeue": "-", "scancel": "-", "qconf": "-", "pbsnodes": "-"}
        ),
    )
    check = provider.check()
    assert check.status == "missing_dependency"
    assert "sbatch" in check.detail
    assert "jump host" in check.remedy


def test_check_flags_a_dialect_that_disagrees_with_the_cluster(
    scheduler_project, scheduler_config
):
    """`pbs` is the default, so pointing it at a Grid Engine site is the likely
    first mistake -- and the wrong directives are silently ignored."""
    sge_host = _check_runner(
        found={
            "qsub": "/opt/sge/bin/qsub",
            "qstat": "/opt/sge/bin/qstat",
            "qdel": "/opt/sge/bin/qdel",
            "qconf": "/opt/sge/bin/qconf",
            "pbsnodes": "-",
        }
    )
    wrong = QsubComputeProvider(
        project_dir=scheduler_project,
        config={**scheduler_config, "dialect": "pbs"},
        command_runner=sge_host,
    )
    check = wrong.check()
    assert check.status == "misconfigured"
    assert "runs SGE" in check.detail
    assert "dialect: sge" in check.remedy
    assert check.observed["detected_dialect"] == "sge"

    right = QsubComputeProvider(
        project_dir=scheduler_project,
        config={**scheduler_config, "dialect": "sge"},
        command_runner=sge_host,
    )
    assert right.check().ok
