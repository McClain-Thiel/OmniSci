# OmniSci service

This package is the deterministic scientific-state layer used by the OmniSci
fork. OmniGent still runs the agent loop, ordinary tools, subagents, policies,
and sandboxes.

`ScienceService` exists for state that should outlive a conversation:

- tasks and append-only research-log entries;
- supervised compute runs and checksummed artifacts;
- reviewer scans and their issue checklist;
- approvals for durable science-specific operations;
- configured compute, storage, and skill providers.

It is not a general tool router. Shell, web, file, and domain tools continue to
run directly through OmniGent. The service is involved only when an operation
creates scientific state or is explicitly submitted as a durable managed job.

## Workspace

A project is a folder. Its name and path come from the filesystem; no project ID
or project manifest is required.

```text
project/
├── data/ analyses/ notebooks/ figures/ reports/ results/
└── .omnisci/
    ├── state.db
    ├── runs/
    └── exports/
```

Opening an existing folder initializes `.omnisci/state.db` lazily. Pre-release
folders containing `.science/project.db` remain readable.

## Interfaces

All interfaces are thin adapters over the same `ScienceService`:

- `science` — human- and agent-readable CLI;
- `science-mcp` — structured tools for an OmniGent agent;
- `/v1/science/*` — HTTP seam for the workbench UI.

The full MCP profile can manage records, jobs, storage, artifacts, and skills.
The `reviewer` profile is deliberately smaller: it can inspect tasks, research
logs, run/artifact metadata, and issues; report or update issues; and record that
a review occurred. It cannot submit jobs, install software, edit research
files, or grant approval.

## Reviewer model

The main Science agent dispatches a low-context reviewer asynchronously and
continues the conversation. The reviewer raises only evidence-backed,
individually resolvable issues; it does not return a verdict or block progress.
The main agent or researcher investigates the issue checklist and records a
resolution or dismissal note.

## Development

From the repository root:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -p no:rerunfailures science/tests -q
UV_CACHE_DIR=.uv-cache uv run pytest -p no:rerunfailures tests/science_server -q
UV_CACHE_DIR=.uv-cache uv run science --help
UV_CACHE_DIR=.uv-cache uv run science-mcp --help
```

See [`../docs/OMNISCI_PRD.md`](../docs/OMNISCI_PRD.md) for the product boundary
and [`docs/server-api-contract.md`](docs/server-api-contract.md) for the HTTP
contract.
