# SPDX-License-Identifier: Apache-2.0
"""CLI tests: --json output, stable exit codes (spec §9.1)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from omnisci.cli.main import main
from omnisci.service import ScienceService, _resolve_project_dir

from tests.conftest import allow_unapproved_local_compute


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "cli-proj"
    rc = main(["--project", str(d), "project", "init", "--json"])
    assert rc == 0
    allow_unapproved_local_compute(ScienceService(d))
    return d


def read_json(capsys):
    return json.loads(capsys.readouterr().out)


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "science" in capsys.readouterr().out


def test_relative_project_path_is_absolute_before_resolve(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    original_resolve = Path.resolve

    def assert_absolute(path, *args, **kwargs):
        assert path.is_absolute()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", assert_absolute)
    assert _resolve_project_dir(".") == tmp_path


def test_init_status_json(proj, capsys):
    rc = main(["--project", str(proj), "project", "status", "--json"])
    assert rc == 0
    out = read_json(capsys)
    assert out["project"]["name"] == "cli-proj"
    assert out["counts"]["tasks"] == 0


def test_tasks_roundtrip_and_not_found(proj, capsys):
    rc = main(["--project", str(proj), "tasks", "create", "--title", "T1", "--json"])
    assert rc == 0
    task = read_json(capsys)
    assert task["id"].startswith("task_")

    rc = main(["--project", str(proj), "tasks", "list", "--json"])
    assert rc == 0
    assert len(read_json(capsys)) == 1

    rc = main(
        ["--project", str(proj), "tasks", "update", task["id"], "--status", "done", "--json"]
    )
    assert rc == 1
    assert "without a research-log entry" in read_json(capsys)["error"]

    # not found -> exit 3 with JSON error object
    rc = main(["--project", str(proj), "research-log", "show", "log_nope", "--json"])
    assert rc == 3
    err = read_json(capsys)
    assert err["exit_code"] == 3


def test_uninitialized_project_is_not_found(tmp_path, capsys):
    rc = main(["--project", str(tmp_path / "empty"), "project", "status", "--json"])
    assert rc == 3
    read_json(capsys)


def test_usage_error_exit_2(proj):
    # missing required --title: argparse exits with code 2
    with pytest.raises(SystemExit) as exc:
        main(["--project", str(proj), "tasks", "create"])
    assert exc.value.code == 2


def test_no_command_prints_help():
    assert main([]) == 2


def test_full_cli_loop(proj, capsys):
    # task -> research log -> reviewer issue -> resolution -> export
    rc = main(["--project", str(proj), "tasks", "create", "--title", "A", "--json"])
    task = read_json(capsys)

    rc = main(
        [
            "--project",
            str(proj),
            "research-log",
            "add",
            "--task",
            task["id"],
            "--summary",
            "done",
            "--json",
        ]
    )
    assert rc == 0
    entry = read_json(capsys)

    rc = main(
        [
            "--project",
            str(proj),
            "issues",
            "create",
            "--title",
            "Missing uncertainty",
            "--description",
            "The result does not report uncertainty.",
            "--verification-question",
            "What interval supports the result?",
            "--research-log",
            entry["id"],
            "--severity",
            "major",
            "--json",
        ]
    )
    assert rc == 0
    issue = read_json(capsys)

    rc = main(
        [
            "--project",
            str(proj),
            "review",
            "record",
            "--summary",
            "One issue raised.",
            "--issue",
            issue["id"],
            "--json",
        ]
    )
    assert rc == 0
    assert read_json(capsys)["issue_ids"] == [issue["id"]]

    rc = main(
        [
            "--project",
            str(proj),
            "issues",
            "update",
            issue["id"],
            "--status",
            "resolved",
            "--resolution",
            "Interval added to the report.",
            "--resolved-by",
            "main-agent",
            "--json",
        ]
    )
    assert rc == 0
    assert read_json(capsys)["status"] == "resolved"

    rc = main(
        ["--project", str(proj), "tasks", "update", task["id"], "--status", "done", "--json"]
    )
    assert rc == 0
    assert read_json(capsys)["status"] == "done"

    rc = main(["--project", str(proj), "project", "export", "--json"])
    assert rc == 0
    export_dir = read_json(capsys)["export_dir"]
    assert (proj / ".omnisci" / "exports").exists()
    assert export_dir

    # a second export in the same second gets a distinct directory
    rc = main(["--project", str(proj), "project", "export", "--json"])
    assert rc == 0
    export_dir2 = read_json(capsys)["export_dir"]
    assert export_dir2 != export_dir

    imported = proj.parent / "cli-imported"
    rc = main(["project", "import", export_dir, "--to", str(imported), "--json"])
    assert rc == 0
    payload = read_json(capsys)
    assert payload["project"]["name"] == "cli-imported"
    assert ScienceService(imported).list_tasks()[0].title == "A"


def test_jobs_and_storage_cli(proj, capsys):
    # storage put/get round-trip through the CLI
    src = proj / "data" / "in.txt"
    src.write_text("payload")
    rc = main(["--project", str(proj), "storage", "put", "data/in.txt", "data/copy.txt", "--json"])
    assert rc == 0
    assert read_json(capsys)["size_bytes"] == 7
    assert (proj / "data" / "copy.txt").read_text() == "payload"

    rc = main(
        ["--project", str(proj), "storage", "cp", "data/in.txt", "data/copied-again.txt", "--json"]
    )
    assert rc == 0
    assert read_json(capsys)["size_bytes"] == 7

    rc = main(
        [
            "--project",
            str(proj),
            "storage",
            "stage",
            "data/in.txt",
            "--to",
            "data/staged.txt",
            "--json",
        ]
    )
    assert rc == 0
    assert read_json(capsys)["size_bytes"] == 7

    rc = main(
        ["--project", str(proj), "storage", "presign", "data/in.txt", "--ttl", "60", "--json"]
    )
    assert rc == 0
    assert read_json(capsys)["url"].startswith("file://")

    rc = main(["--project", str(proj), "storage", "stat", "data/copy.txt", "--json"])
    assert rc == 0
    assert read_json(capsys)["size_bytes"] == 7

    # job submit via execution spec YAML
    (proj / "analyses").mkdir(exist_ok=True)
    spec_path = proj / "execution.yaml"
    command = (
        "from pathlib import Path; Path('figures/cli.txt').write_text('ok'); print('cli-marker')"
    )
    spec_path.write_text(
        f"""\
apiVersion: science.omnigent.ai/v1alpha1
kind: Execution
metadata:
  name: cli-job
spec:
  provider: local
  mode: subprocess
  source:
    workingDirectory: .
  command: [{sys.executable}, -c, "{command}"]
  outputs:
    files: [figures/cli.txt]
  limits:
    maxRuntimeMinutes: 5
  network:
    mode: allow
"""
    )
    rc = main(["--project", str(proj), "jobs", "validate", str(spec_path), "--json"])
    assert rc == 0
    plan = read_json(capsys)
    assert len(plan["spec_hash"]) == 64

    rc = main(["--project", str(proj), "jobs", "submit", str(spec_path), "--json"])
    assert rc == 0
    payload = read_json(capsys)
    run = payload["run"]
    assert run["status"] == "succeeded"
    assert payload["deduplicated"] is False

    # idempotent resubmit
    rc = main(["--project", str(proj), "jobs", "submit", str(spec_path), "--json"])
    assert rc == 0
    payload2 = read_json(capsys)
    assert payload2["deduplicated"] is True
    assert payload2["run"]["id"] == run["id"]

    rc = main(["--project", str(proj), "jobs", "logs", run["id"], "--json"])
    assert rc == 0
    assert "cli-marker" in read_json(capsys)["content"]

    rc = main(["--project", str(proj), "jobs", "outputs", run["id"], "--json"])
    assert rc == 0
    outputs = read_json(capsys)
    assert outputs[0]["path"] == "figures/cli.txt"
    assert outputs[0]["checksum_sha256"]

    rc = main(["--project", str(proj), "jobs", "status", run["id"], "--json"])
    assert rc == 0
    assert read_json(capsys)["status"] == "succeeded"

    # cancel on a terminal run -> runtime failure
    rc = main(["--project", str(proj), "jobs", "cancel", run["id"], "--json"])
    assert rc == 1
    read_json(capsys)

    rc = main(["--project", str(proj), "jobs", "providers", "--json"])
    assert rc == 0
    providers = read_json(capsys)
    assert providers[0]["provider"] == "local"


def test_tools_discovery_cli(proj, capsys, monkeypatch, tmp_path):
    executable = tmp_path / "rclone"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python"
    fake_python.write_text("#!/bin/sh\n")
    fake_python.chmod(0o755)
    modal = venv_bin / "modal"
    modal.write_text("#!/bin/sh\n")
    modal.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(sys, "executable", str(fake_python))

    rc = main(["--project", str(proj), "tools", "list", "--json"])
    assert rc == 0
    tools = {tool["id"]: tool for tool in read_json(capsys)}
    assert tools["science"]["available"] is True
    assert tools["python"]["installed"] is True
    assert tools["modal"]["installed"] is True
    assert tools["rclone"]["path"] == str(executable)

    rc = main(["--project", str(proj), "tools", "search", "object-storage", "--json"])
    assert rc == 0
    assert [tool["id"] for tool in read_json(capsys)] == ["rclone"]

    rc = main(["--project", str(proj), "tools", "doctor", "--json"])
    assert rc == 0
    doctor = read_json(capsys)
    assert doctor["ok"] is True
    assert doctor["compute_providers"] == ["local"]
    assert "file" in doctor["storage_schemes"]


def test_review_issue_is_advisory(proj, capsys):
    rc = main(["--project", str(proj), "tasks", "create", "--title", "Needs human", "--json"])
    assert rc == 0
    task = read_json(capsys)

    rc = main(
        [
            "--project",
            str(proj),
            "issues",
            "create",
            "--title",
            "Dataset license unclear",
            "--description",
            "The dataset record does not include a license.",
            "--verification-question",
            "Which license governs reuse?",
            "--task",
            task["id"],
            "--severity",
            "critical",
            "--json",
        ]
    )
    assert rc == 0
    issue = read_json(capsys)
    assert issue["status"] == "open"

    rc = main(["--project", str(proj), "tasks", "list", "--json"])
    assert rc == 0
    assert read_json(capsys)[0]["status"] == "pending"

    rc = main(["--project", str(proj), "approvals", "list", "--decision", "pending", "--json"])
    assert rc == 0
    assert read_json(capsys) == []


def test_approval_revoke_cli(proj, capsys):
    service = ScienceService(proj)
    approval = service.request_skill("scanpy")["approval"]
    service.resolve_approval(
        approval.id,
        "approved",
        actor="researcher",
        scope_kind="project",
    )

    rc = main(
        [
            "--project",
            str(proj),
            "approvals",
            "revoke",
            approval.id,
            "--actor",
            "researcher",
            "--reason",
            "Permission withdrawn",
            "--json",
        ]
    )

    assert rc == 0
    revoked = read_json(capsys)
    assert revoked["decision"] == "revoked"
    assert revoked["revoked_by"] == "researcher"
    assert revoked["revocation_reason"] == "Permission withdrawn"
