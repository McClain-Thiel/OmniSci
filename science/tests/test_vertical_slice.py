# SPDX-License-Identifier: Apache-2.0
"""End-to-end folder, research-log, issue, run, artifact, and export workflow."""

from __future__ import annotations

import hashlib
import json
import sys

import pytest
from omnisci.domain.schemas import IssueStatus, RunState
from omnisci.errors import StateError
from omnisci.service import ScienceService

from tests.conftest import allow_unapproved_local_compute, make_spec

CSV_DATA = "group,value\ncontrol,1.1\ncontrol,1.3\ntreated,2.1\ntreated,2.4\n"

# Plain stdlib analysis: reads data/experiment.csv, writes figures/result.csv
# and results/summary.json. No matplotlib — tests stay dependency-free.
ANALYZE_PY = """\
import csv
import json
from pathlib import Path

root = Path.cwd()
rows = list(csv.DictReader((root / "data" / "experiment.csv").open()))
groups = {}
for row in rows:
    groups.setdefault(row["group"], []).append(float(row["value"]))
means = {g: sum(v) / len(v) for g, v in sorted(groups.items())}

with (root / "figures" / "result.csv").open("w") as fh:
    fh.write("group,mean\\n")
    for g, m in means.items():
        fh.write(f"{g},{m:.4f}\\n")

summary = {
    "hypothesis": "treatment increases value relative to control",
    "hypothesis_supported": means.get("treated", 0.0) > means.get("control", 0.0),
    "means": means,
    "n_rows": len(rows),
}
(root / "results" / "summary.json").write_text(json.dumps(summary, indent=2))
print("analysis complete:", json.dumps(summary["hypothesis_supported"]))
"""


def test_vertical_slice(tmp_path):
    project_dir = tmp_path / "hypothesis-check"

    # Any folder is the workspace; init only creates conventional content/state.
    svc = ScienceService.init_project(
        project_dir,
        research_goal="Determine whether the supplied dataset supports the "
        "hypothesis that treatment increases value relative to control.",
    )
    allow_unapproved_local_compute(svc)
    for sub in ("papers", "data", "analyses", "notebooks", "figures", "reports"):
        assert (project_dir / sub).is_dir()
    (project_dir / "data" / "experiment.csv").write_text(CSV_DATA)
    assert svc.workspace.name == "hypothesis-check"
    assert svc.workspace.directory == str(project_dir)
    assert not (svc.state_dir / "project.yaml").exists()
    (svc.state_dir / "sources.yaml").write_text(
        "sources:\n  example:\n    kind: git\n    url: https://example.invalid/skills.git\n"
        "    ref: 0123456789abcdef\n    layout: agent-skills\n"
    )
    (svc.state_dir / "sources.lock.yaml").write_text("skills: {}\n")

    # -- step 2: coordinator creates three tasks
    t_inspect = svc.create_task(title="Inspect data", instructions="Look at the CSV.")
    t_analyze = svc.create_task(
        title="Run analysis",
        instructions="Write and run analyses/analyze.py.",
        depends_on=[t_inspect.id],
        expected_outputs=["figures/result.csv", "results/summary.json"],
    )
    t_summary = svc.create_task(
        title="Write summary",
        instructions="Summarize findings in reports/.",
        depends_on=[t_analyze.id],
    )
    assert len(svc.list_tasks()) == 3

    plan_review = svc.record_review(
        summary="Plan checked; no evidence-backed issues found.",
        session_id="sess-1",
        reviewer_agent="reviewer",
    )
    assert plan_review.issue_ids == []

    svc.create_research_log(
        task_id=t_inspect.id,
        summary="Input CSV inspected and columns confirmed.",
    )
    svc.update_task(t_inspect.id, status="done")
    svc.update_task(t_analyze.id, status="in_progress")
    (project_dir / "analyses" / "analyze.py").write_text(ANALYZE_PY)

    # -- step 5: local provider executes the script (figures/result.csv +
    #    results/summary.json; the spec's figure is a CSV to stay dependency-free)
    spec = make_spec(
        [sys.executable, "analyses/analyze.py"],
        output_files=["figures/result.csv", "results/summary.json"],
        name="mean-comparison",
    )
    plan = svc.validate_job(spec)
    assert plan.spec_hash == spec.spec_hash()
    run, dedup = svc.submit_job(spec)
    assert not dedup
    assert run.status == RunState.SUCCEEDED, svc.run_logs(run.id).stderr

    outputs = svc.run_outputs(run.id)
    assert {a.path for a in outputs} == {"figures/result.csv", "results/summary.json"}
    for artifact in outputs:
        digest = hashlib.sha256((project_dir / artifact.path).read_bytes()).hexdigest()
        assert artifact.checksum_sha256 == digest
        assert artifact.size_bytes == (project_dir / artifact.path).stat().st_size
    summary = json.loads((project_dir / "results" / "summary.json").read_text())
    assert summary["hypothesis_supported"] is True

    log1 = svc.create_research_log(
        task_id=t_analyze.id,
        summary="Analysis run; treated mean exceeds control mean.",
        agent="worker",
        session="sess-1",
        files_changed=["analyses/analyze.py"],
        run_ids=[run.id],
        artifact_ids=[a.id for a in outputs],
        assumptions=["Groups are comparable."],
        limitations=["Tiny sample size."],
        uncertainties=["No significance test performed."],
        requested_next_step="Write summary report.",
    )

    issue = svc.create_issue(
        session_id="sess-1",
        task_id=t_analyze.id,
        research_log_id=log1.id,
        title="Figure may be stale",
        description="The figure was generated before the filtering code changed.",
        verification_question="Was figures/result.csv regenerated by the current script?",
        severity="major",
        evidence_refs=["figures/result.csv", log1.id],
        raised_by="reviewer",
    )
    review = svc.record_review(
        summary="One stale-output concern raised.",
        session_id="sess-1",
        reviewer_agent="reviewer",
        issue_ids=[issue.id],
    )
    assert review.issue_ids == [issue.id]
    assert svc.get_task(t_analyze.id).status.value == "in_progress"

    (project_dir / "figures" / "result.csv").write_text("group,mean\nstale,0.0\n")
    rerun_spec = make_spec(
        [sys.executable, "analyses/analyze.py"],
        output_files=["figures/result.csv", "results/summary.json"],
        name="mean-comparison-rerun",  # new spec hash -> new run
    )
    run2, dedup2 = svc.submit_job(rerun_spec)
    assert not dedup2 and run2.id != run.id
    assert run2.status == RunState.SUCCEEDED
    outputs2 = svc.run_outputs(run2.id)
    log2 = svc.create_research_log(
        task_id=t_analyze.id,
        summary="Regenerated figure from the current script.",
        agent="worker",
        session="sess-1",
        files_changed=["figures/result.csv"],
        run_ids=[run2.id],
        artifact_ids=[a.id for a in outputs2],
    )

    resolved = svc.update_issue(
        issue.id,
        status="resolved",
        resolution=f"Regenerated by run {run2.id}; recorded in {log2.id}.",
        resolved_by="reviewer",
    )
    assert resolved.status == IssueStatus.RESOLVED
    svc.record_review(
        summary="The stale-output issue is resolved by the rerun.",
        session_id="sess-1",
        reviewer_agent="reviewer",
        issue_ids=[issue.id],
    )

    svc.update_task(t_analyze.id, status="done")
    svc.create_research_log(
        task_id=t_summary.id,
        summary="Summary task completed.",
    )
    svc.update_task(t_summary.id, status="done")

    # -- step 11: export contains records, run manifests, artifacts, checksums
    export_dir = svc.export()
    assert not (export_dir / "project.yaml").exists()
    assert (export_dir / "configuration" / "sources.yaml").exists()
    assert (export_dir / "configuration" / "sources.lock.yaml").exists()

    schemas = {p.name for p in (export_dir / "schemas").iterdir()}
    assert {
        "Task.schema.json",
        "ResearchLogEntry.schema.json",
        "Run.schema.json",
        "Artifact.schema.json",
        "Review.schema.json",
        "Issue.schema.json",
        "Approval.schema.json",
        "ExecutionSpec.schema.json",
    } <= schemas

    records = export_dir / "records"
    tasks = json.loads((records / "tasks.json").read_text())
    assert {t["title"] for t in tasks} == {"Inspect data", "Run analysis", "Write summary"}
    assert len(json.loads((records / "research_log.json").read_text())) == 4
    reviews = json.loads((records / "reviews.json").read_text())
    assert [r["summary"] for r in reviews] == [
        "Plan checked; no evidence-backed issues found.",
        "One stale-output concern raised.",
        "The stale-output issue is resolved by the rerun.",
    ]
    issues = json.loads((records / "issues.json").read_text())
    assert issues[0]["status"] == "resolved"
    assert len(json.loads((records / "runs.json").read_text())) == 2

    for rid in (run.id, run2.id):
        manifest = json.loads((export_dir / "runs" / rid / "manifest.json").read_text())
        assert manifest["command"][0] == sys.executable
        assert manifest["execution_spec"]["kind"] == "Execution"
        assert len(manifest["spec_hash"]) == 64

    export_manifest = json.loads((export_dir / "export_manifest.json").read_text())
    assert export_manifest["configuration"] == [
        "sources.yaml",
        "sources.lock.yaml",
        "settings.json",
    ]
    copied = [a for a in export_manifest["artifacts"] if a["copied"]]
    assert len(copied) == 4  # 2 runs x 2 outputs
    assert all(a["verified"] for a in copied)
    for entry in copied:
        copied_file = export_dir / "artifacts" / entry["path"]
        digest = hashlib.sha256(copied_file.read_bytes()).hexdigest()
        assert digest == entry["checksum_sha256"]

    restored_dir = tmp_path / "restored-hypothesis-check"
    restored = ScienceService.import_project(export_dir, restored_dir)
    assert restored.workspace.name == "restored-hypothesis-check"
    assert restored.workspace.directory == str(restored_dir)
    assert len(restored.list_tasks()) == 3
    assert len(restored.list_runs()) == 2
    assert len(restored.list_artifacts()) == 4
    assert len(restored.list_research_log()) == 4
    assert restored.list_issues()[0].status == IssueStatus.RESOLVED
    assert (restored_dir / "results" / "summary.json").read_bytes() == (
        project_dir / "results" / "summary.json"
    ).read_bytes()
    assert "analysis complete" in restored.run_logs(run2.id).content

    (export_dir / "artifacts" / copied[0]["path"]).write_text("tampered")
    rejected_dir = tmp_path / "rejected-import"
    with pytest.raises(StateError, match="checksum mismatch"):
        ScienceService.import_project(export_dir, rejected_dir)
    assert not rejected_dir.exists()
