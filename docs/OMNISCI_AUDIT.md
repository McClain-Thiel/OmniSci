# OmniSci audit and hardening checklist

Status: working checklist
Scope: gap between `docs/OMNISCI_PRD.md` (revised) and the implementation on
`claude/open-claude-science-review-65b03b`.

Each item records where the evidence is. Items marked **verified** were confirmed
by reading the code at the cited location. Items marked **audit** are checks we
have not yet run — they are work, not findings.

Priorities: **P0** blocks a defensible v1 · **P1** needed before the connectors
are trustworthy on real projects · **P2** quality and follow-through.

---

## 1. Truthfulness of product claims

The README and PRD currently promise capabilities the code does not implement.
Until each line here is resolved, either the code or the claim is wrong.

- [x] **Decided — cost tracking is deferred.** Primary target is an
      institutional Slurm cluster, which does not report per-job cost and does
      not bill the researcher per second. Recorded under *Deferred* in the PRD;
      becomes P1 if metered cloud compute becomes a mainstream path. Follow-up
      items below carry out the decision.
- [x] **Done — struck "cost provenance" from `README.md:25`** (now "execution
      provenance"), so no product surface claims the deferred capability.
- [ ] **P1 — Remove the dead cost surface.** `Run.cost_usd`
      (`science/omnisci/domain/schemas.py:112`) is never written by any
      provider, and `LimitSpec.max_estimated_cost_usd`
      (`science/omnisci/compute/base.py:71`) is read exactly once — to
      interpolate an approval *message string*
      (`science/omnisci/service.py:980`) — and never enforced as a ceiling.
      **verified** — either delete both fields, or keep them nullable and
      unreferenced with a comment naming the deferral. Do not leave a cap that
      exists only in prose.
- [ ] **P1 — Drop `SourceSpec.git_commit`.**
      (`science/omnisci/compute/base.py:34`) It has zero consumers: nothing
      checks out a revision, so the field perturbs the spec hash and records a
      commit that was never enforced. **verified** — decided: remove the
      requested-checkout field. Note this is *not* the same as §4's observed
      revision, which stays required.
- [ ] **P1 — 51 code comments cite a numbered spec (`spec §12.2`, `§9.4`, `§20`,
      …) that does not exist in this repository.** **verified** — `grep -rn
      "spec §" science/`. This also violates the repo's own comment rule in
      `CLAUDE.md` ("don't reference PR numbers, issue numbers, or ticket IDs").
      Either add the spec, or rewrite the references to describe the scenario.
- [ ] **P2 — Sweep the remaining docs and UI copy** for claims the code does not
      implement, now that the README line is fixed. `science/README.md`,
      `FORK_DELTA.md`, and the workbench tab copy have not been checked.
      **audit**

---

## 2. Connector contract — compute

Measured against *Connector contract* in the revised PRD.

### Asynchrony and supervision

- [ ] **P0 — `local` and `ssh` submit synchronously, inside the HTTP handler.**
      `LocalComputeProvider.submit` blocks on `proc.wait(timeout=…)` for up to
      `DEFAULT_TIMEOUT_MINUTES` (60);
      `science/omnisci/compute/ssh.py:264` blocks on the full
      remote command. `submit_job` calls `provider.submit(plan)` inline
      (`science/omnisci/service.py:950`) and
      `POST /science/jobs:submit` calls it directly. **verified** — contract
      item 1. Background the process and return `QUEUED`; the existing
      `_sync_run` reconciliation already handles the async case.
- [ ] **P1 — `ssh.cancel()` always raises**
      (`science/omnisci/compute/ssh.py:328`) and capabilities
      correctly advertise `supports_cancel: False`. Resolved automatically by
      the async fix above; verify the UI hides cancel for providers that
      declare it unsupported.

### Resource release

- [ ] **P0 — Modal leaks billed sandboxes on every failed run.** The wrapper
      ends `while :; do sleep 3600; done`
      (`science/omnisci/compute/modal.py:488`) to keep the
      filesystem alive for collection, and the sandbox is created with
      `timeout = runtime + grace` (grace default 3600s). `terminate()` is called
      only on staging failure (`modal.py:225`), explicit `cancel` (`:326`), and
      *successful* `collect` (`:390`) — and `collect` refuses any status that is
      not `SUCCEEDED` (`modal.py:338`). A run that exits nonzero idles, metered,
      for the full grace period. **verified** — contract item 3.
- [ ] **P0 — Add a Modal failure-path test.** `test_compute_modal.py` covers
      cancel and a vanished sandbox, but no test asserts termination after a
      nonzero exit. **verified** — the absent test is why the leak shipped.
- [ ] **P1 — Audit scheduler and SSH remote cleanup on every terminal path.**
      `_cleanup_remote` is called on failure and on `collect`, but a run left in
      `RUNNING` because the app died mid-flight has no reaper. **audit**

### Logs

- [ ] **P1 — Only Modal paginates logs.** `local`, `ssh`, and the schedulers all
      return the entire log with `next_cursor=None`
      (`science/omnisci/compute/local.py:340`,
      `science/omnisci/compute/ssh.py:316`,
      `science/omnisci/compute/scheduler.py:246`).
      **verified** — contract item 5.
- [ ] **P1 — Scheduler reconciliation `cat`s both logs in full on every status
      call** (`science/omnisci/compute/scheduler.py:296`).
      Combined with `list_runs()` calling `_sync_run` per run
      (`science/omnisci/service.py:1089`) and a 3s UI poll
      (`web/src/hooks/useScience.ts:196`), a handful of
      Slurm runs produces a continuous stream of SSH connections and full log
      transfers. **verified**

---

## 3. Staging

- [ ] **P0 — Staging hard-fails on any symlink anywhere in the tree.**
      `science/omnisci/compute/ssh.py:384` raises `StateError`;
      Modal does the same at `modal.py:499-509`. Most real project trees contain
      symlinks, including the `.venv` layout OmniSci's own agent bundle declares
      under `cwd_allow_hidden`. **verified** — skip with a warning instead.
- [ ] **P0 — `.venv`, `node_modules`, and build caches are not excluded.**
      `_SOURCE_EXCLUDES` (`science/omnisci/compute/ssh.py:37`) and
      `_DEFAULT_SOURCE_EXCLUDES`
      (`science/omnisci/compute/modal.py:48`) cover only
      `.git`, `.omnisci`, `.science`, `.env*`. **verified**
- [ ] **P1 — Make the exclusion set user-extensible via an ignore file** rather
      than a module constant. Modal accepts `source_excludes` in config; SSH and
      the schedulers accept nothing.
- [ ] **P1 — SSH re-tars the whole working tree on every submit**, including
      `data/`. Modal is worse: one RPC per file
      (`science/omnisci/compute/modal.py:511`). **verified** —
      needs incremental or content-addressed staging.
- [ ] **P1 — Add an opt-in no-stage mode** for SSH and the schedulers: a job may
      declare an existing remote working directory, and OmniSci runs in place
      and stages nothing. On a cluster the compute nodes share a filesystem and
      the data is already there, so per-submit tree copying is pure waste.
      Decided: opt-in per job spec; tree staging stays the default for bare SSH
      hosts with no shared filesystem. A run that used this mode records the
      remote path in its provenance, because the tree was not captured.
- [ ] **P2 — Audit staging against a realistic tree**: 5 GB `data/`, a `.venv`,
      a symlinked reference dataset, and a git worktree. Record wall-clock and
      bytes transferred per provider, with and without the no-stage mode.
      **audit**

---

## 4. Provenance and re-runs

- [ ] **P0 — Idempotency defaults to the spec hash, silently blocking legitimate
      re-runs.** `idempotency_key = spec.metadata.idempotency_key or spec_hash`
      (`science/omnisci/compute/base.py:123`), and `submit_job`
      de-duplicates *before* validation or approval, returning
      `(existing, True)` (`science/omnisci/service.py:932`).
      Re-running the same command after correcting the input CSV returns the
      stale run and its stale artifacts. **verified** — this is the most common
      action in research and it is currently a correctness bug.
- [ ] **P0 — Implement the provenance envelope** from the revised PRD. Today a
      run records command, spec hash, and — only if the spec names one — a
      lockfile checksum (`_environment_hashes`,
      `science/omnisci/service.py:1522`). Missing: observed source revision,
      image digest, interpreter version, and input checksums. **verified**
- [ ] **P0 — Record the *observed* git revision at submission**, with a
      dirty-tree flag, or record the tree as unversioned. This is cheap
      (`git rev-parse HEAD` plus a status check) and is what makes a re-run
      auditable. Distinct from the removed `git_commit` request field in §1: we
      record what was true, we do not ask the provider to enforce a checkout.
- [ ] **P1 — Inputs are not checksummed.** `InputSpec` carries a URI
      (`science/omnisci/compute/base.py:53`) with no recorded
      identity, so an export cannot prove which version of an input produced a
      result.
- [ ] **P2 — Audit the export/import round trip** for completeness against every
      link in the domain model. Export covers all seven record sets, run
      manifests, and checksum-verified local artifact copies
      (`science/omnisci/service.py:1247`) and re-verifies on
      import — it looks sound, but remote (`s3://`) artifacts are recorded
      `copied: false`, so "a second researcher can reconstruct the result from
      the export alone" does not hold for them. **audit**

---

## 5. Approvals

- [ ] **P1 — Approval scope does not bind an envelope.** `scope_kind ==
      "project"` blanket-approves any future request with a matching action
      string (`science/omnisci/service.py:1019`), so
      approving one cheap Modal CPU job authorizes an 8×H100 run later.
      **verified** — bind provider, accelerator class/count, runtime, and cost.
- [ ] **P0 — Stop prompting for isolation a provider structurally cannot
      offer.** SSH and the schedulers reject `network.mode=deny` at validation,
      so every remote submit permanently trips "unrestricted network access"
      (`science/omnisci/service.py:975-992`) with no configuration that can ever
      satisfy it. The rational user response is `allow_unapproved_network: true`
      — which then also silences the prompt for local runs, where sandbox mode
      *can* enforce `deny` and the prompt was meaningful. **verified** —
      decided: drop the network condition for providers whose declared
      capabilities offer no isolation, and stamp `network: unrestricted` on the
      run record and in the run detail UI so it stays auditable.
- [ ] **P1 — Keep the network prompt for `local` subprocess mode**, where it is
      satisfiable by switching to `mode: sandbox`. The point of the change above
      is to make the prompt mean something, not to remove it.
- [ ] **P2 — Audit approval expiry.** `Approval.expires_at` exists on the schema
      but no code path appears to check it. **audit**

---

## 6. Operability and degradation

- [ ] **P0 — One misconfigured connector 500s the entire science API for a
      project.** `_service()` constructs a fresh `ScienceService` per request
      (`omnigent/server/routes/science.py:44`), and
      `ScienceService.__init__` eagerly constructs *every* configured provider;
      `SshComputeProvider.__init__` raises on a missing known-hosts or identity
      file (`science/omnisci/compute/ssh.py:87`). A stale
      `identity_file` path breaks `GET /science/tasks`. **verified** — contract:
      *Degradation*.
- [ ] **P0 — Construct providers lazily** and surface per-connector failure in
      the infrastructure view with a diagnosable reason.
- [ ] **P1 — Cache `ScienceService` per project directory.** Every request
      currently re-reads the infrastructure YAML, reopens SQLite, re-runs
      `repo.migrate()`, and re-scans `entry_points()` twice — against five UI
      queries polling at 5s and runs at 3s. **verified**
- [ ] **P2 — `local_sandbox_available()` has convoluted platform branching**
      (`science/omnisci/compute/local.py:51`) where `command` is
      assigned across three non-exclusive branches. It is correct today on
      darwin/linux/win32 but is a trap for the next editor. **verified**

---

## 7. Reviewer

- [x] **Decided — the reviewer may close issues, including its own.** A critic
      that can only open issues produces a monotonically growing checklist
      nobody reads, and a wrongly closed issue costs less than an ignored list.
      `science_issue_update` stays in the `reviewer` MCP profile
      (`science/omnisci/mcp_server.py:249`). Rationale recorded in the PRD.
- [x] **Already enforced — closing an issue requires a resolution note.**
      `update_issue` rejects any non-open status without one
      (`science/omnisci/service.py:716`). **verified** — this is the
      evidence-bearing half of the decision above and it already holds.
- [ ] **P1 — No test covers the reviewer dispatch loop end to end.** **verified**
      — see §8.
- [ ] **P2 — Reviewer quality is unmeasured.** The PRD's own design rationale
      calls for precision, recall, duplicate rate, and resolution utility, and
      cites the CriticGPT hallucinated-critique result as the reason. Nothing
      measures any of it. Issue fingerprint de-duplication *is* implemented
      (`science/omnisci/domain/repository.py:130`) — that
      P1 line in the PRD is already satisfied.

---

## 8. Test infrastructure

Current coverage: 153 tests in `science/tests` plus 31 route tests in
`tests/science_server`. Unit coverage of the domain and providers is decent. The
gaps are all at the seams.

- [ ] **P0 — No end-to-end UI coverage for any science surface.** `tests/e2e_ui`
      contains nothing science-related, despite six workbench tabs, a Project
      page, and the infrastructure settings screens. **verified**
- [ ] **P0 — No test of the reviewer dispatch loop**: async dispatch, main turn
      unblocked, review recorded, issue surfaced in the workbench. **verified**
- [ ] **P0 — Build a `science-e2e-dev` skill** in the style of the existing
      `cli-setup-verify` and `polly-e2e-dev` skills: boot a throwaway project in
      an isolated `OMNISCI_INFRASTRUCTURE_CONFIG` sandbox, drive a real job
      through submit → reconcile → collect, and assert the record.
- [ ] **P1 — Per-connector contract test suite**, run against every provider:
      submit returns promptly; state survives a simulated restart; a terminal
      failure releases remote resources; outputs are checksummed; logs paginate.
      This is the executable form of the PRD's connector contract. It
      generalizes — but does not replace — the Modal-specific failure-path test
      in §2, which is P0 and must land first.
- [ ] **P1 — Restart-reconciliation test for `local` and `ssh`.** Modal and the
      schedulers advertise `restart_reconciliation: true` in capabilities and
      have coverage; the synchronous providers have none. **audit**
- [ ] **P2 — Export/import round-trip test** asserting every record set and
      every cross-link survives.

---

## 9. Skills installability

Underneath is good infrastructure nobody can reach: a git-backed registry
already pointed at `K-Dense-AI/scientific-agent-skills`
(`science/omnisci/skills/registry.py:10`), a license gate, content hashing, a
lockfile, and rollback. Only the CLI can drive it.

- [ ] **P0 — Build browse-and-install UI over the endpoints that already
      exist.** `POST /science/skills/{id}:install`, `:enable`, and `:sync` are
      live (`omnigent/server/routes/science.py:566`), but
      `web/src/components/science/ScienceSkillsTab.tsx` is 45 lines that render
      a name and an enabled badge — no install, browse, or search. **verified**
- [ ] **P0 — Close the approval-to-install loop.** `request_skill` records a
      pending approval and returns; its own docstring says "Approval-driven
      auto-install is not wired up yet"
      (`science/omnisci/service.py:818`). Resolving the approval must actually
      install the pinned skill. Today a human must read the approval and run the
      CLI by hand. **verified**
- [ ] **P1 — Surface skill discovery.** `list_source_skills`
      (`science/omnisci/skills/registry.py:123`) can enumerate what a source
      offers, but no HTTP route or UI exposes it, so there is no way to find a
      skill you have not already installed.
- [ ] **P1 — Decide whether skill sources are per-project or app-level.**
      `sources.yaml` lives in `.omnisci/` per project
      (`science/omnisci/skills/registry.py:34`), so every new folder starts with
      no sources and re-clones. Connectors are already app-scoped; sources
      probably should be too. **verified**
- [ ] **P2 — Seed more than one default source** once discovery exists.

---

## 10. Scheduler connectors on a real cluster

Findings from the first run against a real scheduler (UCL cs-hpc, Grid Engine
8.1.9). Everything here was invisible to the unit suite and to the AWS host the
earlier "qsub" runs used, because that host runs bash and had a stubbed `qsub`
that returned a `.fixture` job id.

**Validated against the real scheduler:** staging over SSH, SGE directive
generation, submission and job-id parsing, `_query_sge` mapping real `qstat`
output, restart reconciliation from disk, `qdel` cancellation with terminal-state
persistence, and remote cleanup.

**Still unvalidated**, because a 1-slot test job sat in `qw` for over two hours
behind ~424 pending jobs: the `qw -> r` transition, the exit-code protocol
executing on a compute node, `qacct` accounting fallback, log retrieval and
output collection from a compute node, and walltime/memory enforcement.

- [x] **Fixed — remote commands ran under the login shell.** `ssh host '<script>'`
      hands the string to the login shell; the cluster gateway issues `/bin/csh`,
      which rejects `if ...; then ...; fi`, `cmd || { ...; }`, and `var=$(...)`.
      Staging succeeded and every status poll then died with `if: Expression
      Syntax`. Fixed by naming `/bin/sh` as the remote command and piping the
      script on stdin, plus `-S /bin/sh` on generated job scripts. **verified**
- [ ] **P0 — `remote_root` defaults to `/tmp/omnisci`, which is node-local.**
      (`science/omnisci/compute/ssh.py:36`) Correct for the ssh provider, where
      submission and execution are the same machine. Wrong for anything going
      through a scheduler: the job lands on a compute node whose `/tmp` is not
      the login node's, so the working directory is missing and outputs cannot
      be collected. A user following the docs hits a confusing failure. The
      scheduler providers should require an explicit shared-filesystem
      `remote_root` in `validate()` rather than silently inheriting a node-local
      default. **verified** — had to hand-pick a home-directory path to get a
      job to run at all.
- [ ] **P1 — Nothing reports whether a job can ever be scheduled.** A first
      submission sat unschedulable indefinitely because the chosen queue is
      `qtype INTERACTIVE` on all but one host, so a batch job could never run
      there. Grid Engine answers this in one call — `qalter -w v` returns
      "found possible assignment" or the reason it cannot — and Slurm and PBS
      have equivalents. Surfacing that verdict is worth more than any progress
      indicator: it converts an indefinite wait into an immediate, actionable
      error.
- [ ] **P2 — Optionally surface queue position.** `qstat -u "*" -s p` gives the
      pending list, and position is the line index; measured live at 430 pending
      / #11 / 460 running. Two caveats. It must be **one query per cluster**, not
      per run — the per-run `_sync_run` fan-out in section 2 would multiply it.
      And position is **not** a predictor: observed drifting *backwards* (10 ->
      12 -> 13 jobs ahead) within minutes as higher-priority work was inserted,
      because SGE schedules on priority and fit rather than FIFO. Show position
      if useful, but deriving an ETA from it would be exactly the fabricated
      number the PRD forbids.
- [ ] **P2 — The `dialect` default is `pbs`, but the cluster to hand is SGE.**
      Nothing detects which is present. `qconf`/`pbsnodes` presence distinguishes
      them in one call; guessing wrong produces directives the scheduler silently
      ignores.

---

## 11. New connectors

- [ ] **P1 — Add an Anyscale / Ray connector.** Judged against the connector
      contract, Ray's job submission API is a better fit than what already
      ships: submit returns a job id, status polls, logs tail incrementally, and
      stop cancels — satisfying contract items 1, 2, and 5 natively, which
      neither `local` nor `ssh` do. Ray's `working_dir` upload has its own
      exclusion handling, so it sidesteps §3's staging problem rather than
      inheriting it.
- [ ] **P1 — Anyscale credentials must be a reference, not an inline token.**
      Same treatment as the other connectors; `_reject_inline_credentials`
      (`science/omnisci/infrastructure.py:104`) will enforce it once the config
      shape is right.
- [ ] **P2 — Determine whether Anyscale reports per-job cost** attributable to a
      run. Relevant only if cost comes off the deferred list. **audit**
- [ ] **P2 — Decide whether Anyscale and Slurm are one user-facing concept.** To
      a researcher both are "my cluster"; to the code they are unrelated
      providers. The UI should not necessarily mirror the code's split.

---

## 12. Project identity

A project is a folder with `.omnisci/` inside it, and there is no manifest —
`project.yaml` was deliberately removed and the PRD forbids requiring one. That
part is right. The problem is that a project has no *identity*: it is a path
string and nothing else.

- [ ] **P0 — Give a project a durable identity.** `Workspace` is
      `{name, directory}` (`science/omnisci/domain/schemas.py:60`) — no id, no
      goal, no created timestamp. Add a stable uuid, a display name independent
      of the folder basename, and a created timestamp in `.omnisci/state.db`.
      Renaming or moving the folder currently orphans the record, and two
      machines have nothing to agree on. **verified**
- [ ] **P0 — Persist `research_goal`.** It is accepted by the API, formatted
      into `README.md` at init (`science/omnisci/service.py:294`), and never
      stored. The one thing the user is asked to type about their project is
      discarded. **verified**
- [ ] **P1 — Keep an app-level registry of known project paths.** Not a project
      database — the "recent repositories" list. Today `ProjectPage.tsx:14-27`
      reconstructs the project list by enumerating distinct `session.workspace`
      values, so a project with no recent sessions is invisible and one with no
      sessions does not exist. **verified**
- [ ] **P1 — On import, mint a new id and record `derived_from`.** Otherwise two
      copies of the same export on one machine collide and a restored project is
      indistinguishable from the original. Cheap now, painful later.
- [ ] **P2 — Delete `web/src/lib/sciencePreferences.ts` and its test.** It keeps
      a project path in localStorage; every live call site now derives the
      project from `session.workspace`. Dead code. **verified**

---

## 13. Layer boundary

The line: the **tool layer** (OmniGent) is capability and knows nothing about
science; the **science layer** (OmniSci) is record and authorization and never
sits in the path of an ordinary tool call. A tool call that produces a durable
scientific fact should *result in* a science record, not be proxied by one.

- [x] **Decided — compute connectors stay in the science layer.** A run *is* a
      durable record: immutable spec, provenance, artifacts, approval. Not a
      slow tool call. Correct as built, no action.
- [ ] **P1 — Move `_CLI_TOOL_CATALOG` out of `ScienceService`.** Nine hardcoded
      CLI names behind a `shutil.which` check
      (`science/omnisci/service.py:69`) is tool-layer readiness living in the
      science layer. Readiness is a property of an execution host, not of a
      research project — which `designs/OMNISCI_APP_RESOURCES_AND_AGENT_BUILDER.md`
      already states. **verified**
- [ ] **P2 — Revisit the `science storage` verbs.** `ls/get/put/copy/stat/presign`
      duplicate what an object-storage MCP tool would provide; the only
      science-specific content is the approval gate. Thin justification for
      owning six verbs, and the piece most likely to be superseded once a tool
      catalog exists. **audit**

---

## 14. Onboarding for non-programmers

Every OmniGent concept that reaches a bench scientist unchanged is a bug in the
science layer. The science layer is where programmer concepts get compiled away,
not where they get exposed.

Note the systemic pattern behind several items here and in §9: **capability
exists at the service layer with a read-only or absent UI**. Skills have
endpoints and no UI; harness install has functions and no route. Much of what
looks like a missing feature is missing wiring.

- [ ] **P0 — Add a harness setup seam.** `install_harness_cli`,
      `harness_login`, `harness_cli_logged_in`, and `harness_logout` all exist
      and cover claude, codex, gemini/antigravity, kimi, cursor, kiro, goose,
      hermes, qwen, opencode, and pi
      (`omnigent/onboarding/harness_install.py`). `GET /harnesses`
      (`omnigent/server/routes/harnesses.py:18`) is the only endpoint and is
      read-only; `web/src/pages/SetupPage.tsx` is 158 lines of admin-account
      bootstrap with no harness content. This is a route plus a UI over
      functions that already work. **verified** — upstream gap too (see
      omnigent-ai/omnigent#3227); prefer contributing upstream over forking.
- [ ] **P0 — Stop showing the word "harness" to users.** A scientist is choosing
      *which AI*, not a harness. Audit all user-visible copy. **audit**
- [ ] **P1 — Interactive login is the real work, not install.** Every harness
      login is an OAuth or device flow. The web path must either stream the
      CLI's prompts through the existing device-grant machinery or hand off
      explicitly. Design this before building the install button.
- [ ] **P1 — Explicit consent for remote installers.** `install_harness_cli`
      shells out to `curl … | bash` for cursor, kimi, kiro, antigravity, and
      hermes. A browser button must show the exact command and require
      confirmation; it must never run silently because someone clicked "set up
      Codex." **verified**
- [ ] **P1 — Give the agent proposal rights over its own configuration.** The
      most promising onboarding surface is the agent itself — a scientist types
      "run this on my lab's cluster" rather than filling in a
      `known_hosts_file` field. This is currently blocked, not merely unbuilt:
      the agent cannot install a skill (§9), cannot install or authenticate a
      harness (no route), and cannot configure a connector
      (`update_app_infrastructure` has no science tool). Use the existing
      approval system — agent proposes, human sees the exact change, one click.
      Sequence by blast radius: **skills first** (install machinery already has
      a license gate), then connectors, then harnesses last.
- [ ] **P2 — Connector templates and a "test connection" action.** The
      infrastructure form is already real (`Select` for provider, typed
      `Input`s), which makes it the one place the UX is not the problem — but
      "my university's Slurm" is a better starting point than an empty form, and
      nothing currently verifies a connector before first use.

---

## 15. Decisions taken

Recorded so the reasoning survives the people who were in the room.

1. **Cost tracking is deferred.** The primary target is an institutional Slurm
   cluster, which reports no per-job cost and does not bill the researcher per
   second. Struck from the README; recorded under *Deferred* in the PRD. Becomes
   P1 if metered cloud compute becomes a mainstream path. (§1)
2. **`SourceSpec.git_commit` is dropped.** A requested checkout nobody performs
   is an unenforced provenance fact. The *observed* revision at submission stays
   required — that is §4, and it is not the same thing. (§1, §4)
3. **The reviewer may close issues, including its own.** A checklist that only
   grows is a checklist nobody reads; a wrongly closed issue costs less than an
   ignored list, and closes already require a resolution note. (§7)
4. **The network approval prompt stops firing where no provider can satisfy
   it.** SSH and the schedulers record `network: unrestricted` on the run
   instead. The prompt is kept for `local` subprocess mode, where switching to
   `mode: sandbox` actually resolves it. (§5)
5. **No-stage mode is opt-in; tree staging remains the default.** A job may
   declare an existing remote working directory and run in place. Default stays
   staging, because a bare SSH host has no shared filesystem to rely on. (§3)

### Still open

- What tree size should staging refuse outright, once the no-stage path exists?
  Deferring a hard ceiling until §3's measurement task has numbers. (§3)
- How much write access does the agent get to its own configuration? The
  sequencing recommendation is skills → connectors → harnesses, mediated by the
  existing approval system, but the policy call has not been made. (§14)
- Are skill sources per-project or app-level? Connectors are app-level; sources
  currently are not. (§9)
- Do Anyscale and Slurm present as one "cluster" concept to a researcher, or
  two providers? (§11)
- Should the scheduler providers auto-detect PBS vs SGE, or require the
  dialect to be declared? Guessing wrong emits directives the scheduler
  silently ignores. (§10)
