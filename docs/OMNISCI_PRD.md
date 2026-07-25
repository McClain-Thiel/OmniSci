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

A run's **provenance envelope** is the set of facts required to explain how its
outputs came to exist. Every field the envelope claims must be one the system
actually resolved and recorded; a field that is declared but never enforced is
worse than an absent one, because it invites false confidence.

The envelope is:

| Fact | Source | Status |
| --- | --- | --- |
| command and resolved working directory | execution spec | required |
| provider and provider run id | submission | required |
| source revision | VCS commit *observed* at submission, with a dirty-tree flag, or recorded as unversioned | required |
| environment | image reference or digest, interpreter version, lockfile checksum when declared | required |
| input identity | checksum or storage ETag of each declared input | required |
| outputs | checksummed artifacts | required |

Cost is deliberately **not** in the envelope for this release. The primary
deployment target is an institutional cluster, where per-job billing is not
something the scheduler reports and the researcher does not pay per second. See
*Deferred*.

**Re-running is a first-class operation.** Submitting the same analysis again
after correcting an input, a script, or the environment must produce a new run,
not silently return the previous one. De-duplication is a protection against
double submission of an identical job, not a cache: it applies only within a
short submission window, or when the caller supplies an explicit idempotency
key, and it must consider input identity — not only the text of the spec.

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

An approval authorizes an **envelope**, not merely an action name. For compute
that envelope is the provider, the accelerator class and count, the runtime
ceiling, and the cost ceiling; for storage it is the scheme and prefix, and the
operation. A later request that exceeds any dimension of a granted envelope
requires a new decision, even when the action string matches.

The failure mode this exists to prevent is a safety layer that users disable.
An approval prompt that fires on every remote submission, for a condition the
provider can never satisfy, trains people to set the bypass flag — after which
the layer protects nothing. Approval conditions must therefore be reachable:
OmniSci must not require approval for a property a correctly configured
provider is structurally incapable of offering. Where a provider cannot enforce
an isolation property, that limitation belongs in the run record and in the UI,
not in a prompt that cannot be satisfied.

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

The reviewer may resolve and dismiss issues, including ones it raised itself.
This is deliberate: a critic that can only open issues and never close them
produces a checklist that grows monotonically, and the cost of a wrongly closed
issue is lower than the cost of a checklist nobody reads. Closing is still an
evidence-bearing act — the resolution note must record what evidence answered
the verification question, and the issue history preserves the close so a human
can reopen it.

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

## Compute and storage connectors

Connectors are how a folder-scoped record reaches real compute. They are the
part of OmniSci a researcher notices first and trusts last, so the contract is
stated here rather than left to each adapter.

### Connector contract

Every compute provider must satisfy all of the following. A provider that
cannot is not shipped as a connector.

1. **Submission returns promptly.** `submit` registers the job and returns a
   reference. It does not block for the duration of the work. Long-running
   science is the normal case, and a request path that blocks for the length of
   an analysis is not supervision.
2. **State is durable and external.** `status`, `logs`, `cancel`, and `collect`
   work from a different process and after an application restart, using
   persisted provider state rather than an in-memory handle.
3. **Terminal states release resources.** When a run reaches any terminal state
   — including failure, timeout, and cancellation — the provider releases the
   remote resources it allocated. Metered resources are never left running as a
   side effect of an unsuccessful outcome.
4. **Capabilities are declared, and validation rejects what cannot be
   enforced.** A provider that cannot honor a requested isolation, resource, or
   environment property fails validation with a specific message instead of
   accepting the spec and quietly ignoring the property.
5. **Logs are pageable.** `logs` accepts a cursor and returns bounded pages.
   Reconciling a run does not transfer its entire log.
6. **Cost is reported when the provider reports it**, and the declared cost
   ceiling is enforced before submission rather than described in a prompt.

### Staging

Remote providers stage the working tree by default. Staging must succeed on an
ordinary research folder, which means it must tolerate what those folders
actually contain.

A job may instead declare a **remote working directory** that already exists on
the execution host, in which case OmniSci stages nothing and runs in place. This
is the normal shape of cluster work: the scheduler's nodes share a filesystem
and the data is already on it, so copying the tree per submission is waste.
Staging remains the default because a bare SSH host has no such guarantee, but
the no-stage path is a first-class mode, not a workaround — and a run that used
it records the remote path as part of its provenance, since the tree was not
captured.

- Virtual environments, dependency caches, and build outputs are excluded by
  default, and the exclusion set is user-extensible through an ignore file
  rather than a hard-coded constant.
- Symbolic links are skipped with a recorded warning, not treated as fatal.
  Refusing to stage a tree because it contains a symlink rejects most real
  projects, including the layout OmniSci itself creates.
- Large inputs are referenced through a storage connector rather than copied on
  every submission, and repeated submissions do not re-transfer unchanged data.

### Degradation

Connector configuration is application-scoped and independent of any one
project. A connector that is missing, misconfigured, or unreachable degrades to
an unavailable connector with a diagnosable reason. It must not prevent the
project record from loading: tasks, research logs, issues, and artifacts remain
readable when compute is broken, because the record is the durable asset and
compute is not.

Provider construction is therefore lazy, and its failure is reported per
connector in the infrastructure view rather than raised from the project API.

## Verification

The scientific record is an auditability claim, and an auditability claim that
is not itself tested is a marketing claim. Verification is a product
requirement, not an engineering detail.

- Every connector has an end-to-end test that submits, reconciles across a
  simulated restart, collects checksummed outputs, and asserts that a terminal
  failure released remote resources.
- The reviewer dispatch loop is covered end to end: the main agent dispatches
  asynchronously, the main turn is not blocked, the reviewer records a review
  and raises an issue, and the issue reaches the workbench.
- The science UI surfaces are covered by the same end-to-end UI suite as the
  rest of the application.
- Provenance survives a round trip: export and re-import reproduce every
  durable record and its links.

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
- Every shipped compute connector satisfies the connector contract above.
- Re-submitting a corrected analysis produces a new run.
- A run records its full provenance envelope, or omits a fact it could not
  resolve rather than recording an unenforced one.
- A misconfigured connector never prevents a project's records from loading.
- Product documentation claims no capability the code does not implement.

## P1 requirements

- Reviewer dispatch cadence can be configured per Science agent.
- The UI can filter issues by session, severity, and status.
- Research logs can be rendered as a chronological notebook-like timeline.
- Approvals bind a compute or storage envelope rather than an action name.
- A job can declare an existing remote working directory and skip staging.
- Incremental staging avoids re-transferring unchanged inputs.
- Reviewer quality is measured — precision, duplicate rate, and resolution
  utility — against a fixed sample of recorded reviews.

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
- A researcher can submit a job to a cluster or a cloud sandbox from an ordinary
  project folder — one containing a virtual environment, symlinks, and a large
  `data/` directory — without hand-preparing the tree.
- A failed remote job leaves no metered resource running.
- A second researcher can reconstruct a result from the exported record alone.

## Deferred

Recorded so they are not silently dropped, and explicitly not committed for this
release.

- **Cost tracking.** Provider-reported cost on the run record, and enforcement
  of a declared cost ceiling before submission. Deferred because the primary
  deployment target is an institutional Slurm cluster, where the scheduler does
  not report per-job cost and the researcher is not billed per second. This
  becomes P1 the moment metered cloud compute (Modal, cloud GPU) is a mainstream
  path rather than an option. Until then no product surface may claim it.
- **Requested source revisions.** Asking a provider to check out a specific
  commit before running. The *observed* revision is part of the provenance
  envelope and is required; asking the provider to enforce a requested one is
  not. A field that requests a checkout nobody performs is the exact
  unenforced-fact failure this document forbids.

## Open questions

- Whether research logs should later have a visible Markdown mirror in addition
  to SQLite.
- Whether unresolved high-severity issues should require explicit
  acknowledgement only at export or publication boundaries.
- Whether claims and evidence should become first-class records after the issue
  workflow has been evaluated.
