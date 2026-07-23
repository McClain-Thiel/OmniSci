# Science server API contract

This is the contract between
`omnigent/server/routes/science.py` and `web/src/lib/scienceApi.ts`.

## Conventions

- Routes are mounted under `/v1/science`.
- A project-owned route receives the absolute workspace folder as
  `?project=...`.
- The folder is the project. It does not need a manifest or registered ID.
- Domain objects use the snake-case JSON produced by Pydantic
  `model_dump(mode="json")`.
- Errors use
  `{"error": {"type": "<ErrorClass>", "message": "<message>"}}`.
- Not-found errors return 404, invalid state returns 409, and invalid input
  returns 400.
- Authentication uses the same server dependency as the rest of OmniGent.

## Workspace and infrastructure

```text
POST  /projects
      body: {"directory": str, "research_goal"?: str}
      -> status
GET   /project/status?project=...
      -> {"project": {"name": str, "directory": str}, "counts": {...}}
POST  /project:export?project=...
      -> {"export_dir": str}
POST  /projects:import
      body: {"export_dir": str, "directory": str}
      -> status

GET   /tools
GET   /infrastructure
PATCH /infrastructure
```

`research_goal` is used only when creating a visible workspace README. It is not
hidden project identity.

## Tasks and research log

```text
POST  /tasks?project=...                         -> Task
GET   /tasks?project=...&status=...              -> [Task]
PATCH /tasks/{task_id}?project=...               -> Task

POST  /research-log?project=...                  -> ResearchLogEntry
GET   /research-log?project=...&task_id=...      -> [ResearchLogEntry]
GET   /research-log/{entry_id}?project=...       -> ResearchLogEntry
```

Research-log entries are append-only. A task cannot move to `done` until it has
at least one linked entry.

## Advisory review and issue checklist

```text
POST  /reviews?project=...                       -> Review
GET   /reviews?project=...&session_id=...        -> [Review]

POST  /issues?project=...                        -> Issue
GET   /issues?project=...&status=...&session_id=...
                                                   -> [Issue]
PATCH /issues/{issue_id}?project=...             -> Issue
```

A review records a completed background scan and the issue IDs it raised. It
does not contain a verdict and does not mutate tasks or approvals.

Issue status is `open`, `resolved`, or `dismissed`. Resolving or dismissing an
issue requires a resolution note.

## Managed compute

```text
GET   /compute/providers?project=...             -> [ProviderCapabilities]
POST  /jobs:validate?project=...                 -> ExecutionPlan
POST  /jobs:submit?project=...                   -> {"run": Run, "deduplicated": bool}
GET   /jobs?project=...                          -> [Run]
GET   /jobs/{run_id}?project=...                 -> Run
GET   /jobs/{run_id}/logs?project=...&cursor=... -> log page
GET   /jobs/{run_id}/outputs?project=...         -> [Artifact]
POST  /jobs/{run_id}:cancel?project=...          -> Run
```

These endpoints are for explicit durable scientific jobs. Ordinary OmniGent
tool calls do not pass through this API.

## Artifacts, approvals, storage, and skills

```text
GET   /artifacts?project=...                     -> [Artifact]
POST  /artifacts?project=...                     -> Artifact
GET   /artifacts/{artifact_id}/content?project=... -> file bytes

GET   /approvals?project=...                     -> [Approval]
POST  /approvals/{approval_id}:resolve?project=... -> Approval
POST  /approvals/{approval_id}:revoke?project=...  -> Approval

GET   /storage:list?project=...&uri=...          -> ObjectPage
POST  /storage:stage?project=...                 -> ObjectMetadata

GET   /skills?project=...                        -> installed skills
POST  /skills:sync?project=...                   -> resolved revisions
POST  /skills/{skill_id}:install?project=...     -> installed skill
POST  /skills/{skill_id}:enable?project=...      -> enablement
```

## Capability

`GET /v1/info` exposes `science_enabled`. When false, the web application hides
the science workbench and normal OmniGent behavior remains available.
