# SPDX-License-Identifier: Apache-2.0
"""``science`` command — argparse-based JSON CLI (spec §9.1, §17.2).

The CLI is a thin shell: it parses args, calls ScienceService, and prints
results. No business logic lives here.

Exit codes: 0 ok · 1 runtime failure · 2 usage error · 3 not found.
With ``--json``, machine-readable JSON goes to stdout; without it, output
is human-readable YAML (logs print raw).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml
from pydantic import BaseModel

from omnisci import __version__
from omnisci.compute.base import ExecutionSpec
from omnisci.errors import ApprovalRequiredError, NotFoundError, ScienceError
from omnisci.service import ScienceService

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


def _jsonable(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _emit(args, payload, raw: str | None = None) -> None:
    if getattr(args, "json", False):
        json.dump(_jsonable(payload), sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif raw is not None:
        sys.stdout.write(raw)
        if raw and not raw.endswith("\n"):
            sys.stdout.write("\n")
    else:
        yaml.safe_dump(_jsonable(payload), sys.stdout, sort_keys=False, allow_unicode=True)


def _error(args, code: int, message: str) -> int:
    if getattr(args, "json", False):
        json.dump({"error": message, "exit_code": code}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


# ---------------------------------------------------------------------------
# handlers (each returns a payload; may raise ScienceError)
# ---------------------------------------------------------------------------


def _service(args) -> ScienceService:
    return ScienceService(args.project)


def cmd_project_init(args):
    svc = ScienceService.init_project(
        args.project,
        research_goal=args.goal or "",
    )
    return {"project": svc.workspace, "directory": str(svc.project_dir)}


def cmd_project_status(args):
    return _service(args).status()


def cmd_project_export(args):
    out = _service(args).export()
    return {"export_dir": str(out)}


def cmd_project_import(args):
    service = ScienceService.import_project(args.export_dir, args.to)
    return {"project": service.workspace, "directory": str(service.project_dir)}


def cmd_tasks_list(args):
    return _service(args).list_tasks(status=args.status)


def cmd_tasks_create(args):
    return _service(args).create_task(
        title=args.title,
        instructions=args.instructions or "",
        parent_id=args.parent,
        depends_on=_split_csv(args.depends_on),
        expected_outputs=args.expected_output or [],
        assigned_agent=args.agent,
        assigned_session=args.session,
    )


def cmd_tasks_update(args):
    return _service(args).update_task(
        args.id,
        status=args.status,
        title=args.title,
        instructions=args.instructions,
        assigned_agent=args.agent,
        assigned_session=args.session,
    )


def _research_log_kwargs(args) -> dict:
    data: dict = {}
    if args.file:
        loaded = yaml.safe_load(Path(args.file).read_text())
        if not isinstance(loaded, dict):
            raise ScienceError(f"research-log file must contain a YAML mapping: {args.file}")
        data = loaded
    overrides = {
        "task_id": args.task,
        "summary": args.summary,
        "agent": args.agent,
        "session": args.session,
        "requested_next_step": args.requested_next_step,
    }
    for key, value in overrides.items():
        if value is not None:
            data[key] = value
    if not data.get("summary"):
        raise ScienceError("research-log add requires --summary (or a YAML file providing it)")
    return data


def cmd_research_log_add(args):
    return _service(args).create_research_log(**_research_log_kwargs(args))


def cmd_research_log_list(args):
    return _service(args).list_research_log(task_id=args.task, session=args.session)


def cmd_research_log_show(args):
    return _service(args).get_research_log(args.id)


def cmd_review_record(args):
    return _service(args).record_review(
        summary=args.summary,
        session_id=args.session,
        reviewer_agent=args.reviewer_agent,
        reviewer_session=args.reviewer_session,
        reviewer_model=args.reviewer_model,
        reviewer_harness=args.reviewer_harness,
        reviewed_through=args.reviewed_through,
        issue_ids=args.issue or [],
    )


def cmd_review_show(args):
    return _service(args).get_review(args.id)


def cmd_issues_list(args):
    return _service(args).list_issues(status=args.status, session_id=args.session)


def cmd_issues_show(args):
    return _service(args).get_issue(args.id)


def cmd_issues_create(args):
    return _service(args).create_issue(
        title=args.title,
        description=args.description,
        verification_question=args.verification_question,
        session_id=args.session,
        task_id=args.task,
        research_log_id=args.research_log,
        category=args.category,
        severity=args.severity,
        evidence_refs=args.evidence_ref or [],
        confidence=args.confidence,
        fingerprint=args.fingerprint,
        raised_by=args.raised_by,
    )


def cmd_issues_update(args):
    return _service(args).update_issue(
        args.id,
        status=args.status,
        resolution=args.resolution,
        resolved_by=args.resolved_by,
    )


def _load_spec(args) -> ExecutionSpec:
    return ExecutionSpec.from_yaml(Path(args.file).read_text())


def cmd_jobs_providers(args):
    return _service(args).job_providers()


def cmd_jobs_validate(args):
    return _service(args).validate_job(_load_spec(args))


def cmd_jobs_submit(args):
    run, deduplicated = _service(args).submit_job(_load_spec(args))
    return {"run": run, "deduplicated": deduplicated}


def cmd_jobs_status(args):
    return _service(args).get_run(args.run_id)


def cmd_jobs_logs(args):
    page = _service(args).run_logs(args.run_id)
    if getattr(args, "json", False):
        return page
    raw = page.content
    if page.stderr:
        raw += ("\n" if raw and not raw.endswith("\n") else "") + page.stderr
    return _RawText(raw)


def cmd_jobs_outputs(args):
    return _service(args).run_outputs(args.run_id)


def cmd_jobs_cancel(args):
    return _service(args).cancel_run(args.run_id)


def cmd_tools_list(_args):
    return ScienceService.app_tools_list()


def cmd_tools_search(args):
    return ScienceService.app_tools_search(args.query)


def cmd_tools_doctor(_args):
    return ScienceService.app_tools_doctor()


def cmd_storage_ls(args):
    return _service(args).storage_ls(args.uri)


def cmd_storage_stat(args):
    return _service(args).storage_stat(args.uri)


def cmd_storage_get(args):
    return _service(args).storage_get(args.uri, args.dest)


def cmd_storage_put(args):
    return _service(args).storage_put(args.src, args.uri)


def cmd_storage_copy(args):
    return _service(args).storage_copy(args.source, args.destination)


def cmd_storage_stage(args):
    return _service(args).storage_get(args.uri, args.dest)


def cmd_storage_presign(args):
    return {
        "uri": args.uri,
        "url": _service(args).storage_presign(args.uri, ttl_seconds=args.ttl, write=args.write),
        "expires_in": args.ttl,
        "operation": "write" if args.write else "read",
    }


def cmd_artifacts_add(args):
    return _service(args).add_artifact(
        args.path,
        type=args.type,
        task_id=args.task,
        run_id=args.run,
        research_log_id=args.research_log,
    )


def cmd_artifacts_show(args):
    return _service(args).get_artifact(args.id)


def cmd_artifacts_list(args):
    return _service(args).list_artifacts()


def cmd_skills_list(args):
    return _service(args).skills_list()


def cmd_skills_search(args):
    return _service(args).skills_search(args.query or "")


def cmd_skills_sync(args):
    return {"synced": _service(args).skills_sync(source=args.source)}


def cmd_skills_install(args):
    return _service(args).skill_install(
        args.name,
        source=args.source,
        allow_unknown_license=args.allow_unknown_license,
    )


def cmd_skills_enable(args):
    return _service(args).skill_enable(args.name)


def cmd_skills_disable(args):
    return _service(args).skill_disable(args.name)


def cmd_skills_upgrade(args):
    return _service(args).skill_upgrade(
        args.name, allow_unknown_license=args.allow_unknown_license
    )


def cmd_skills_rollback(args):
    return _service(args).skill_rollback(args.name)


def cmd_approvals_list(args):
    return _service(args).list_approvals(decision=args.decision)


def cmd_approvals_wait(args):
    if args.timeout < 0:
        raise ScienceError("approval wait timeout cannot be negative")
    if args.poll_interval <= 0:
        raise ScienceError("approval poll interval must be positive")
    service = _service(args)
    deadline = time.monotonic() + args.timeout
    while True:
        approval = service.get_approval(args.id)
        if approval.decision.value != "pending":
            return approval
        if time.monotonic() >= deadline:
            raise ScienceError(f"timed out waiting for approval {args.id} after {args.timeout:g}s")
        time.sleep(min(args.poll_interval, max(0.0, deadline - time.monotonic())))


def cmd_approvals_resolve(args):
    return _service(args).resolve_approval(
        args.id,
        decision=args.decision,
        actor=args.actor,
        reason=args.reason,
        scope_kind=args.scope_kind,
    )


def cmd_approvals_revoke(args):
    return _service(args).revoke_approval(
        args.id,
        actor=args.actor,
        reason=args.reason,
    )


class _RawText:
    """Marker for payloads that should print as-is in non-JSON mode."""

    def __init__(self, text: str):
        self.text = text


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="science",
        description="omnisci science project CLI (local-first scientific workbench). "
        "Exit codes: 0 ok, 1 runtime failure, 2 usage error, 3 not found.",
    )
    parser.add_argument(
        "--version", action="version", version=f"science {__version__} (omnisci {__version__})"
    )
    parser.add_argument(
        "--project",
        default=".",
        help="project directory (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def group(name, help_text):
        p = sub.add_parser(name, help=help_text)
        return p.add_subparsers(dest="subcommand", metavar="ACTION", required=True)

    def add_json(p):
        p.add_argument("--json", action="store_true", help="machine-readable JSON output")

    # -- project
    g = group("project", "project init/status/export/import")
    p = g.add_parser("init", help="create conventional folders and state in --project")
    p.add_argument("--goal", help="research goal written to a new README")
    add_json(p)
    p.set_defaults(func=cmd_project_init)
    p = g.add_parser("status", help="project status and record counts")
    add_json(p)
    p.set_defaults(func=cmd_project_status)
    p = g.add_parser("export", help="export workspace to .omnisci/exports/<timestamp>/")
    add_json(p)
    p.set_defaults(func=cmd_project_export)
    p = g.add_parser("import", help="restore a project export into a new directory")
    p.add_argument("export_dir", help="export directory containing export_manifest.json")
    p.add_argument("--to", required=True, help="new project directory")
    add_json(p)
    p.set_defaults(func=cmd_project_import)

    # -- tasks
    g = group("tasks", "task list/create/update")
    p = g.add_parser("list", help="list tasks")
    p.add_argument("--status", choices=["pending", "in_progress", "blocked", "done", "cancelled"])
    add_json(p)
    p.set_defaults(func=cmd_tasks_list)
    p = g.add_parser("create", help="create a task")
    p.add_argument("--title", required=True)
    p.add_argument("--instructions")
    p.add_argument("--parent", help="parent task id")
    p.add_argument("--depends-on", help="comma-separated task ids")
    p.add_argument("--expected-output", action="append", help="expected output path (repeatable)")
    p.add_argument("--agent")
    p.add_argument("--session")
    add_json(p)
    p.set_defaults(func=cmd_tasks_create)
    p = g.add_parser("update", help="update a task")
    p.add_argument("id")
    p.add_argument("--status", choices=["pending", "in_progress", "blocked", "done", "cancelled"])
    p.add_argument("--title")
    p.add_argument("--instructions")
    p.add_argument("--agent")
    p.add_argument("--session")
    add_json(p)
    p.set_defaults(func=cmd_tasks_update)

    # -- research log
    g = group("research-log", "append and inspect research-log entries")
    p = g.add_parser("add", help="append an entry, optionally from a YAML file")
    p.add_argument("file", nargs="?", help="YAML file with research-log fields")
    p.add_argument("--task", help="optional task id")
    p.add_argument("--summary")
    p.add_argument("--agent")
    p.add_argument("--session")
    p.add_argument("--requested-next-step")
    add_json(p)
    p.set_defaults(func=cmd_research_log_add)
    p = g.add_parser("list", help="list research-log entries")
    p.add_argument("--task")
    p.add_argument("--session")
    add_json(p)
    p.set_defaults(func=cmd_research_log_list)
    p = g.add_parser("show", help="show one research-log entry")
    p.add_argument("id")
    add_json(p)
    p.set_defaults(func=cmd_research_log_show)

    # -- review
    g = group("review", "record/show an advisory background review")
    p = g.add_parser("record", help="record one completed background review")
    p.add_argument("--summary", required=True)
    p.add_argument("--session")
    p.add_argument("--reviewed-through")
    p.add_argument("--issue", action="append", help="issue id raised by this review")
    p.add_argument("--reviewer-agent")
    p.add_argument("--reviewer-session")
    p.add_argument("--reviewer-model")
    p.add_argument("--reviewer-harness")
    add_json(p)
    p.set_defaults(func=cmd_review_record)
    p = g.add_parser("show", help="show a review")
    p.add_argument("id")
    add_json(p)
    p.set_defaults(func=cmd_review_show)

    # -- issues
    g = group("issues", "review issue checklist: list/show/create/update")
    p = g.add_parser("list", help="list reviewer issues")
    p.add_argument("--status", choices=["open", "resolved", "dismissed"])
    p.add_argument("--session")
    add_json(p)
    p.set_defaults(func=cmd_issues_list)
    p = g.add_parser("show", help="show one issue")
    p.add_argument("id")
    add_json(p)
    p.set_defaults(func=cmd_issues_show)
    p = g.add_parser("create", help="raise or refresh an evidence-backed issue")
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--verification-question", required=True)
    p.add_argument("--session")
    p.add_argument("--task")
    p.add_argument("--research-log")
    p.add_argument("--category", default="scientific")
    p.add_argument(
        "--severity",
        choices=["info", "concern", "major", "critical"],
        default="concern",
    )
    p.add_argument("--evidence-ref", action="append")
    p.add_argument("--confidence", type=float, default=0.5)
    p.add_argument("--fingerprint")
    p.add_argument("--raised-by")
    add_json(p)
    p.set_defaults(func=cmd_issues_create)
    p = g.add_parser("update", help="resolve or dismiss an issue")
    p.add_argument("id")
    p.add_argument("--status", choices=["open", "resolved", "dismissed"], required=True)
    p.add_argument("--resolution")
    p.add_argument("--resolved-by")
    add_json(p)
    p.set_defaults(func=cmd_issues_update)

    # -- jobs
    g = group("jobs", "compute jobs: providers/validate/submit/status/logs/outputs/cancel")
    p = g.add_parser("providers", help="list compute providers")
    add_json(p)
    p.set_defaults(func=cmd_jobs_providers)
    p = g.add_parser("validate", help="validate an execution spec YAML")
    p.add_argument("file", help="execution spec YAML (spec §12.1)")
    add_json(p)
    p.set_defaults(func=cmd_jobs_validate)
    p = g.add_parser("submit", help="submit an execution spec YAML (idempotent)")
    p.add_argument("file", help="execution spec YAML")
    add_json(p)
    p.set_defaults(func=cmd_jobs_submit)
    p = g.add_parser("status", help="run status")
    p.add_argument("run_id")
    add_json(p)
    p.set_defaults(func=cmd_jobs_status)
    p = g.add_parser("logs", help="run logs (stdout + stderr)")
    p.add_argument("run_id")
    add_json(p)
    p.set_defaults(func=cmd_jobs_logs)
    p = g.add_parser("outputs", help="artifacts produced by a run")
    p.add_argument("run_id")
    add_json(p)
    p.set_defaults(func=cmd_jobs_outputs)
    p = g.add_parser("cancel", help="cancel a run")
    p.add_argument("run_id")
    add_json(p)
    p.set_defaults(func=cmd_jobs_cancel)

    # -- tools
    g = group("tools", "command-line tool discovery: list/search/doctor")
    p = g.add_parser("list", help="list CLIs installed on the OmniSci application host")
    add_json(p)
    p.set_defaults(func=cmd_tools_list)
    p = g.add_parser("search", help="search known command-line tools")
    p.add_argument("query", help="substring of tool id, command or description")
    add_json(p)
    p.set_defaults(func=cmd_tools_search)
    p = g.add_parser("doctor", help="check app tool, compute and storage availability")
    add_json(p)
    p.set_defaults(func=cmd_tools_doctor)

    # -- storage
    g = group("storage", "storage ls/stat/get/put/cp/stage/presign")
    p = g.add_parser("ls", help="list a file:// URI or project-relative path")
    p.add_argument("uri")
    add_json(p)
    p.set_defaults(func=cmd_storage_ls)
    p = g.add_parser("stat", help="stat a URI (size, sha256, mime)")
    p.add_argument("uri")
    add_json(p)
    p.set_defaults(func=cmd_storage_stat)
    p = g.add_parser("get", help="copy a URI to a project-relative destination")
    p.add_argument("uri")
    p.add_argument("dest")
    add_json(p)
    p.set_defaults(func=cmd_storage_get)
    p = g.add_parser("put", help="write a local file to a URI")
    p.add_argument("src")
    p.add_argument("uri")
    add_json(p)
    p.set_defaults(func=cmd_storage_put)
    p = g.add_parser("cp", help="copy between configured storage URIs")
    p.add_argument("source")
    p.add_argument("destination")
    add_json(p)
    p.set_defaults(func=cmd_storage_copy)
    p = g.add_parser("stage", help="stage a storage URI into the project")
    p.add_argument("uri")
    p.add_argument("--to", dest="dest", required=True, help="project-relative destination")
    add_json(p)
    p.set_defaults(func=cmd_storage_stage)
    p = g.add_parser("presign", help="create a scoped read or write URL")
    p.add_argument("uri")
    p.add_argument("--ttl", type=int, default=900, help="expiry in seconds (default: 900)")
    p.add_argument("--write", action="store_true", help="presign a write instead of a read")
    add_json(p)
    p.set_defaults(func=cmd_storage_presign)

    # -- artifacts
    g = group("artifacts", "artifacts add/show/list")
    p = g.add_parser("add", help="register a file as an artifact (checksum recorded)")
    p.add_argument("path", help="project-relative path or file:// URI")
    p.add_argument("--type", default="file", help="data | figure | result | log | code | other")
    p.add_argument("--task")
    p.add_argument("--run")
    p.add_argument("--research-log")
    add_json(p)
    p.set_defaults(func=cmd_artifacts_add)
    p = g.add_parser("show", help="show an artifact")
    p.add_argument("id")
    add_json(p)
    p.set_defaults(func=cmd_artifacts_show)
    p = g.add_parser("list", help="list artifacts")
    add_json(p)
    p.set_defaults(func=cmd_artifacts_list)

    # -- skills
    g = group("skills", "skill sources: list/search/sync/install/enable/disable/upgrade/rollback")
    p = g.add_parser("list", help="configured sources and installed skills (from the lockfile)")
    add_json(p)
    p.set_defaults(func=cmd_skills_list)
    p = g.add_parser("search", help="search skills available in synced sources")
    p.add_argument("query", nargs="?", help="substring filter on skill name/path")
    add_json(p)
    p.set_defaults(func=cmd_skills_search)
    p = g.add_parser(
        "sync", help="fetch source refs into the cache (never touches installed skills)"
    )
    p.add_argument("--source", help="sync only this source")
    add_json(p)
    p.set_defaults(func=cmd_skills_sync)
    p = g.add_parser(
        "install",
        help="install a skill at the source's pinned ref "
        "(UNKNOWN/disallowed license blocks installation, spec §20)",
    )
    p.add_argument("name")
    p.add_argument("--source", help="source to install from (required if ambiguous)")
    p.add_argument(
        "--allow-unknown-license",
        action="store_true",
        help="override an UNKNOWN license (recorded in the lockfile)",
    )
    add_json(p)
    p.set_defaults(func=cmd_skills_install)
    p = g.add_parser("enable", help="enable an installed skill for this project")
    p.add_argument("name")
    add_json(p)
    p.set_defaults(func=cmd_skills_enable)
    p = g.add_parser("disable", help="disable a skill for this project")
    p.add_argument("name")
    add_json(p)
    p.set_defaults(func=cmd_skills_disable)
    p = g.add_parser(
        "upgrade",
        help="move a skill to the source ref's current revision (previous pin kept for rollback)",
    )
    p.add_argument("name")
    p.add_argument("--allow-unknown-license", action="store_true")
    add_json(p)
    p.set_defaults(func=cmd_skills_upgrade)
    p = g.add_parser("rollback", help="restore the previous pinned revision")
    p.add_argument("name")
    add_json(p)
    p.set_defaults(func=cmd_skills_rollback)

    # -- approvals
    g = group("approvals", "semantic approvals: list/wait/resolve/revoke")
    p = g.add_parser("list", help="list approval requests")
    p.add_argument("--decision", choices=["pending", "approved", "denied", "revoked"])
    add_json(p)
    p.set_defaults(func=cmd_approvals_list)
    p = g.add_parser("wait", help="wait for an approval to be decided")
    p.add_argument("id")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--poll-interval", type=float, default=1.0)
    add_json(p)
    p.set_defaults(func=cmd_approvals_wait)
    p = g.add_parser("resolve", help="approve or deny a pending approval")
    p.add_argument("id")
    p.add_argument("--decision", choices=["approved", "denied"], required=True)
    p.add_argument("--actor", default="local-user")
    p.add_argument("--reason")
    p.add_argument("--scope-kind", choices=["one_time", "prefix", "project"])
    add_json(p)
    p.set_defaults(func=cmd_approvals_resolve)
    p = g.add_parser("revoke", help="revoke an approved permission")
    p.add_argument("id")
    p.add_argument("--actor", default="local-user")
    p.add_argument("--reason")
    add_json(p)
    p.set_defaults(func=cmd_approvals_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        payload = func(args)
    except ApprovalRequiredError as exc:
        _emit(args, exc.payload())
        return EXIT_OK
    except NotFoundError as exc:
        return _error(args, EXIT_NOT_FOUND, str(exc))
    except ScienceError as exc:
        return _error(args, EXIT_RUNTIME, str(exc))
    except FileNotFoundError as exc:
        return _error(args, EXIT_NOT_FOUND, str(exc))
    if isinstance(payload, _RawText):
        _emit(args, None, raw=payload.text)
    else:
        _emit(args, payload)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
