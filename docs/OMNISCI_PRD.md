# OmniSci product requirements

Status: implementation target
Audience: product and engineering

## Problem

General-purpose coding agents can search, write code, and run tools, but they do
not maintain a durable scientific record. Important results, assumptions,
artifacts, compute jobs, and unresolved concerns are easily lost in chat history.

OmniGent already solves the agent-runtime problem: it runs different harnesses,
tools, subagents, policies, approvals, and sandboxes. OmniSci should not replace
that runtime. It should add the smallest useful scientific workbench around it.

## Product

OmniSci is OmniGent's meta-agent harness with a local-first scientific workflow:

- a folder is the research project;
- tasks describe planned work;
- research-log entries record what happened and what was learned;
- runs and artifacts preserve computational provenance;
- approvals remain deterministic human or policy decisions;
- a quiet background reviewer raises evidence-backed issues without blocking the
  main agent.

The practical shorthand is “OmniGent plus a Claude Science-style research
workspace”: any supported agent harness can do the work, while OmniSci preserves
the scientific process around it.

## Goals

1. Let a researcher open any folder and begin work without creating a separate
   project identity or manifest.
2. Preserve enough state to reconstruct tasks, research decisions, runs,
   artifacts, approvals, reviews, and unresolved issues across sessions.
3. Add independent criticism without adding latency to the main conversation.
4. Keep ordinary agent and tool execution entirely inside OmniGent.
5. Keep the fork small: science-specific behavior belongs in the isolated
   `omnisci` package, science agent bundle, API seam, and workbench UI.

## Non-goals

- A new model SDK, agent loop, tool router, or policy engine.
- Continuous access to private model reasoning.
- A synchronous reviewer gate on every task or response.
- A hidden project registry or required project configuration file.
- A scientific knowledge graph or autonomous publication system in v1.
- Making remote compute, object storage, or a particular model vendor required.

## Product boundary

### OmniGent owns

- conversations, models, harnesses, and subagents;
- ordinary tool calls and MCP connections;
- streaming, context management, sandboxes, and policies;
- permission prompts and human interaction.

### OmniSci owns

- folder-scoped scientific state;
- tasks and append-only research-log entries;
- durable run and artifact provenance;
- science-specific approvals where a durable semantic record is useful;
- background review records and issue lifecycle;
- compute and storage connectors used for explicit, durable scientific jobs.

`ScienceService` is an application layer over the science database and provider
adapters. It may validate and record a managed job submission, artifact, review,
or issue. It must not intercept or proxy ordinary OmniGent tool calls.

## Workspace model

A project is a folder. Its name and location are derived from the filesystem;
there is no project ID and no required `project.yaml`.

OmniSci may create `.omnisci/` inside the folder for implementation state such
as SQLite, run logs, and exports. That directory is analogous to `.git`: it is
not the project definition and researchers should not need to edit it.

Opening an existing folder initializes missing internal state lazily. Creating a
new workspace may add conventional folders such as `data/`, `analyses/`,
`figures/`, `reports/`, and `results/`, but those folders are not required for a
workspace to be valid.

## Domain model

### Task

A bounded unit of planned work with dependencies, assignment, expected outputs,
and lifecycle. Completing a task requires at least one linked research-log
entry, so “done” has a durable account of what happened.

### Research log

An append-only scientific progress record linked to a task when applicable. It
captures the summary, files changed, runs, artifacts, sources, assumptions,
limitations, uncertainties, and next step.

Research logs replace checkpoints. They are records of work and interpretation,
not gates that require a `PASS` verdict.

### Run

A durable record of a local or remote scientific job: immutable execution
specification, provider identity, status, logs, cost when known, and produced
artifacts. A run is distinct from an ordinary shell or tool call because it is
explicitly submitted for durable supervision and provenance.

### Artifact

A stable reference to a scientific output with path or URI, media type, size,
checksum, and links to the producing run, task, and research-log entry.

### Review

A record that an independent reviewer examined a bounded slice of work. A review
records who reviewed, what conversation/item boundary was covered, a short
summary, and the issues it raised. It has no `PASS`, `REVISE`, or `ESCALATE`
authority.

### Issue

An evidence-backed concern raised by a reviewer, the main agent, or a human.
Issues form the reviewer checklist and have a small lifecycle:

`open -> resolved` or `open -> dismissed`

Each issue records severity, title, description, evidence references, a concrete
verification question, confidence, source review, and resolution notes.

### Approval

A durable authorization decision for an action that requires policy or human
authority. Reviewer issues never grant approval and never become implicit
approval gates.

## Background reviewer

The Science agent periodically dispatches a reviewer as an asynchronous
OmniGent subagent. The main conversation continues immediately.

The reviewer receives a compact review packet rather than the full conversation:

- the current research question or deliverable;
- recent claims and conclusions;
- relevant tool results, runs, artifacts, or research-log entries;
- currently open issues.

It has read-only workspace access and a restricted science interface that can
only list relevant records, create or update issues, and record the review. It
cannot submit jobs, install software, modify research outputs, approve access, or
block the main loop.

The reviewer should raise an issue only when it can identify:

1. the claim or observation that looks suspect;
2. why it matters;
3. the evidence or record that triggered the concern;
4. a concrete check that could resolve it.

Issue results appear in a quiet workbench panel. The main agent receives a short
digest at a natural boundary and may investigate or resolve them without being
forced into a synchronous revision loop.

### Design rationale from prior work

- OpenAI's CriticGPT work found that model critiques can help humans catch more
  problems, but also reports hallucinated critiques. That supports an
  issue-assistance model with human or main-agent verification, not an
  authoritative reviewer verdict:
  <https://openai.com/index/finding-gpt4s-mistakes-with-gpt-4/>
- Anthropic recommends separate parallel calls when multiple perspectives are
  useful, and evaluator/optimizer loops only when evaluation criteria are clear
  and iteration has measurable value. OmniSci therefore runs a focused critic
  beside the main loop and does not force every turn through it:
  <https://www.anthropic.com/engineering/building-effective-agents>
- Anthropic's multi-agent research write-up argues that separate context windows
  provide separation of concerns and reduce path dependence. We treat that as a
  design hypothesis for the compact review packet, not proof that the reviewer
  is independent:
  <https://www.anthropic.com/engineering/multi-agent-research-system>
- Research on intrinsic self-correction finds that a model can fail or degrade
  when asked to correct itself without external feedback. The reviewer must
  therefore inspect external evidence and state a falsifiable verification
  question rather than merely reconsidering the main agent's prose:
  <https://arxiv.org/abs/2310.01798>

Less context alone does not guarantee diversity. The reviewer should be
evaluated for precision, recall, duplicate rate, and resolution utility, and the
runtime should permit a different model or harness when the added cost is
justified.

## P0 requirements

- Any existing folder can be opened as an OmniSci workspace.
- No `.science/project.yaml` or equivalent manifest is required.
- Existing pre-release `.science/` workspaces remain readable.
- All checkpoint-facing APIs, CLI commands, schemas, and UI copy use
  “research log”.
- Research-log entries validate referenced tasks, runs, and artifacts.
- Task completion requires a linked research-log entry.
- Reviews are advisory records and never mutate task state or create approvals.
- Issues can be listed, created, resolved, and dismissed through the service,
  HTTP API, CLI/MCP façade, and UI.
- The reviewer has a restricted issue-recording interface and less context than
  the main agent.
- Ordinary tools continue to run through OmniGent without passing through
  `ScienceService`.

## P1 requirements

- Reviewer dispatch cadence can be configured per Science agent.
- The UI can filter issues by session, severity, and status.
- Duplicate reviewer findings are merged using a stable fingerprint.
- Research logs can be rendered as a chronological notebook-like timeline.

## Success criteria

- A researcher can open a previously uninitialized folder and see the workbench
  without creating metadata first.
- An analysis can produce a task, run, artifact, and research-log entry whose
  provenance survives restart and export.
- A background reviewer can raise an issue while the main session continues.
- Resolving or dismissing an issue never changes an approval or interrupts an
  in-flight agent turn.
- Removing the `omnisci` package leaves normal OmniGent agent and tool behavior
  unchanged.

## Open questions

- Whether research logs should later have a visible Markdown mirror in addition
  to SQLite.
- Whether unresolved high-severity issues should require explicit
  acknowledgement only at export or publication boundaries.
- Whether claims and evidence should become first-class records after the issue
  workflow has been evaluated.
