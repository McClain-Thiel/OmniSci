// Typed client for the `/v1/science` endpoints pinned in
// `science/docs/server-api-contract.md`. Mirrors the conventions of
// `sessionsApi.ts`: TS surface is camelCase, the wire is snake_case
// (Pydantic `model_dump(mode="json")` of the schemas in
// `science/omnisci/domain/schemas.py`), and conversion happens at the
// parse boundary so callers never see raw wire fields.
//
// Workspace addressing: every endpoint takes a `project` query parameter whose
// value is an absolute folder. The folder itself is the project identity.

import { authenticatedFetch } from "./identity";

// ── camelCase TS types ───────────────────────────────────────────────────────

export type ScienceTaskStatus = "pending" | "in_progress" | "blocked" | "done" | "cancelled";

export type ScienceRunState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timeout";

export type ScienceApprovalDecision = "pending" | "approved" | "denied" | "revoked";
export type ScienceApprovalScopeKind = "one_time" | "prefix" | "project";

export interface ScienceProject {
  name: string;
  directory: string;
}

export interface ScienceComputeConfig {
  defaultProvider: string;
  providers: Record<string, Record<string, unknown>>;
}

export interface ScienceStorageConfig {
  defaultProvider: string;
  allowedRoots: string[];
  providers: Record<string, Record<string, unknown>>;
}

export interface ScienceProviderStatus {
  id: string;
  registered: boolean;
  dependencyAvailable: boolean;
  configured: boolean;
  default: boolean;
}

export interface ScienceToolStatus {
  id: string;
  kind: string;
  command: string;
  description: string;
  installed: boolean;
  enabled: boolean;
  available: boolean;
  path: string | null;
}

export interface ScienceToolsStatus {
  ok: boolean;
  scope: "app";
  computeProviders: string[];
  storageSchemes: string[];
  tools: ScienceToolStatus[];
}

export interface ScienceInfrastructureStatus {
  scope: "app";
  configPath: string;
  computeConfig: ScienceComputeConfig;
  storageConfig: ScienceStorageConfig;
  compute: ScienceProviderStatus[];
  storage: ScienceProviderStatus[];
}

export interface ScienceConfigurationPatch {
  computeConfig?: ScienceComputeConfig;
  storageConfig?: ScienceStorageConfig;
}

export interface ScienceTask {
  id: string;
  title: string;
  instructions: string;
  status: ScienceTaskStatus;
  parentId: string | null;
  dependsOn: string[];
  assignedAgent: string | null;
  assignedSession: string | null;
  expectedOutputs: string[];
  createdAt: string;
  updatedAt: string;
}

export interface ScienceRun {
  id: string;
  provider: string;
  status: ScienceRunState;
  command: string[];
  queuedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  exitCode: number | null;
  costUsd: number | null;
  outputArtifactIds: string[];
  createdAt: string;
}

export interface ScienceArtifact {
  id: string;
  /** Project-relative path; null for URI-only artifacts. */
  path: string | null;
  /** Storage URI (e.g. file://, s3://); null for path-only artifacts. */
  uri: string | null;
  /** data | figure | result | log | code | other */
  type: string;
  mime: string | null;
  sizeBytes: number | null;
  runId: string | null;
  taskId: string | null;
  createdAt: string;
}

export interface ScienceResearchLogEntry {
  id: string;
  taskId: string | null;
  agent: string | null;
  session: string | null;
  summary: string;
  filesChanged: string[];
  runIds: string[];
  artifactIds: string[];
  sources: string[];
  assumptions: string[];
  limitations: string[];
  uncertainties: string[];
  requestedNextStep: string | null;
  createdAt: string;
}

export interface ScienceReview {
  id: string;
  sessionId: string | null;
  reviewerAgent: string | null;
  reviewerSession: string | null;
  reviewerModel: string | null;
  reviewerHarness: string | null;
  reviewedThrough: string | null;
  summary: string;
  issueIds: string[];
  createdAt: string;
}

export type ScienceIssueStatus = "open" | "resolved" | "dismissed";
export type ScienceIssueSeverity = "info" | "concern" | "major" | "critical";

export interface ScienceIssue {
  id: string;
  sessionId: string | null;
  taskId: string | null;
  researchLogId: string | null;
  category: string;
  severity: ScienceIssueSeverity;
  status: ScienceIssueStatus;
  title: string;
  description: string;
  evidenceRefs: string[];
  verificationQuestion: string;
  confidence: number;
  raisedBy: string | null;
  resolution: string | null;
  resolvedBy: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ScienceApproval {
  id: string;
  /** Semantic action, e.g. ``compute.submit:local``. */
  action: string;
  scope: string | null;
  requestingSession: string | null;
  requestingAgent: string | null;
  decision: ScienceApprovalDecision;
  actor: string | null;
  decidedAt: string | null;
  reason: string | null;
  expiresAt: string | null;
  consumedAt: string | null;
  revokedAt: string | null;
  revokedBy: string | null;
  revocationReason: string | null;
  scopeKind: ScienceApprovalScopeKind;
  createdAt: string;
}

/** Return value of `GET /v1/science/project/status` (`ScienceService.status()`). */
export interface ScienceProjectStatus {
  project: ScienceProject;
  counts: {
    tasks: number;
    tasksByStatus: Record<string, number>;
    researchLog: number;
    reviews: number;
    issues: number;
    openIssues: number;
    runs: number;
    artifacts: number;
    approvals: number;
  };
}

export interface ScienceRunLogs {
  stdout: string;
  stderr: string;
  cursor: string | null;
}

/**
 * One installed science skill. The skills endpoints are Phase 2 and their
 * exact shape is not pinned in the contract, so this is parsed loosely —
 * every field beyond `name` is optional.
 */
export interface ScienceSkill {
  name: string;
  description: string | null;
  version: string | null;
  enabled: boolean | null;
}

// ── wire shapes (snake_case) ─────────────────────────────────────────────────

interface ScienceProjectWire {
  name: string;
  directory: string;
}

interface ScienceComputeConfigWire {
  default_provider?: string;
  providers?: Record<string, Record<string, unknown>>;
}

interface ScienceStorageConfigWire {
  default_provider?: string;
  allowed_roots?: string[];
  providers?: Record<string, Record<string, unknown>>;
}

interface ScienceProviderStatusWire {
  id: string;
  registered: boolean;
  dependency_available: boolean;
  configured: boolean;
  default: boolean;
}

interface ScienceToolStatusWire {
  id: string;
  kind: string;
  command: string;
  description: string;
  installed: boolean;
  enabled: boolean;
  available: boolean;
  path?: string | null;
}

interface ScienceToolsStatusWire {
  ok: boolean;
  scope: "app";
  compute_providers?: string[];
  storage_schemes?: string[];
  tools?: ScienceToolStatusWire[];
}

interface ScienceInfrastructureStatusWire {
  scope: "app";
  config_path: string;
  compute_config?: ScienceComputeConfigWire;
  storage_config?: ScienceStorageConfigWire;
  compute?: ScienceProviderStatusWire[];
  storage?: ScienceProviderStatusWire[];
}

interface ScienceTaskWire {
  id: string;
  title: string;
  instructions?: string;
  status: ScienceTaskStatus;
  parent_id?: string | null;
  depends_on?: string[];
  assigned_agent?: string | null;
  assigned_session?: string | null;
  expected_outputs?: string[];
  created_at: string;
  updated_at: string;
}

interface ScienceRunWire {
  id: string;
  provider?: string;
  status: ScienceRunState;
  command?: string[];
  queued_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  cost_usd?: number | null;
  output_artifact_ids?: string[];
  created_at: string;
}

interface ScienceArtifactWire {
  id: string;
  path?: string | null;
  uri?: string | null;
  type?: string;
  mime?: string | null;
  size_bytes?: number | null;
  run_id?: string | null;
  task_id?: string | null;
  created_at: string;
}

interface ScienceResearchLogEntryWire {
  id: string;
  task_id?: string | null;
  agent?: string | null;
  session?: string | null;
  summary?: string;
  files_changed?: string[];
  run_ids?: string[];
  artifact_ids?: string[];
  sources?: string[];
  assumptions?: string[];
  limitations?: string[];
  uncertainties?: string[];
  requested_next_step?: string | null;
  created_at: string;
}

interface ScienceReviewWire {
  id: string;
  session_id?: string | null;
  reviewer_agent?: string | null;
  reviewer_session?: string | null;
  reviewer_model?: string | null;
  reviewer_harness?: string | null;
  reviewed_through?: string | null;
  summary?: string;
  issue_ids?: string[];
  created_at: string;
}

interface ScienceIssueWire {
  id: string;
  session_id?: string | null;
  task_id?: string | null;
  research_log_id?: string | null;
  category?: string;
  severity: ScienceIssueSeverity;
  status: ScienceIssueStatus;
  title: string;
  description: string;
  evidence_refs?: string[];
  verification_question: string;
  confidence?: number;
  raised_by?: string | null;
  resolution?: string | null;
  resolved_by?: string | null;
  created_at: string;
  updated_at: string;
}

interface ScienceApprovalWire {
  id: string;
  action: string;
  scope?: string | null;
  requesting_session?: string | null;
  requesting_agent?: string | null;
  decision: ScienceApprovalDecision;
  actor?: string | null;
  decided_at?: string | null;
  reason?: string | null;
  expires_at?: string | null;
  consumed_at?: string | null;
  revoked_at?: string | null;
  revoked_by?: string | null;
  revocation_reason?: string | null;
  scope_kind?: ScienceApprovalScopeKind;
  created_at: string;
}

interface ScienceProjectStatusWire {
  project: ScienceProjectWire;
  counts?: {
    tasks?: number;
    tasks_by_status?: Record<string, number>;
    research_log?: number;
    reviews?: number;
    issues?: number;
    open_issues?: number;
    runs?: number;
    artifacts?: number;
    approvals?: number;
  };
}

interface ScienceSkillWire {
  name?: string;
  description?: string | null;
  version?: string | null;
  revision?: string | null;
  enabled?: boolean | null;
}

// ── errors ───────────────────────────────────────────────────────────────────

/**
 * An HTTP error from a science route. The science contract serializes
 * errors as ``{"error": {"type": "<ErrorClass>", "message": "..."}}``;
 * `type` is `null` when the body wasn't in that shape.
 */
export class ScienceApiError extends Error {
  readonly status: number;
  readonly type: string | null;
  constructor(message: string, status: number, type: string | null) {
    super(message);
    this.name = "ScienceApiError";
    this.status = status;
    this.type = type;
  }
}

async function apiErrorFromResponse(res: Response): Promise<ScienceApiError> {
  let message = `${res.status} ${res.statusText}`;
  let type: string | null = null;
  try {
    const body = (await res.json()) as { error?: { type?: string; message?: string } };
    if (body.error?.message) message = body.error.message;
    if (body.error?.type) type = body.error.type;
  } catch {
    // Non-JSON / empty body — keep the status-line fallback.
  }
  return new ScienceApiError(message, res.status, type);
}

async function readJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

// ── wire → TS converters ─────────────────────────────────────────────────────

function projectFromWire(wire: ScienceProjectWire): ScienceProject {
  return {
    name: wire.name,
    directory: wire.directory,
  };
}

function computeConfigFromWire(wire?: ScienceComputeConfigWire): ScienceComputeConfig {
  return {
    defaultProvider: wire?.default_provider ?? "local",
    providers: wire?.providers ?? {},
  };
}

function storageConfigFromWire(wire?: ScienceStorageConfigWire): ScienceStorageConfig {
  return {
    defaultProvider: wire?.default_provider ?? "local",
    allowedRoots: wire?.allowed_roots ?? [],
    providers: wire?.providers ?? {},
  };
}

function providerStatusFromWire(wire: ScienceProviderStatusWire): ScienceProviderStatus {
  return {
    id: wire.id,
    registered: wire.registered,
    dependencyAvailable: wire.dependency_available,
    configured: wire.configured,
    default: wire.default,
  };
}

function toolsStatusFromWire(wire: ScienceToolsStatusWire): ScienceToolsStatus {
  return {
    ok: wire.ok,
    scope: wire.scope,
    computeProviders: wire.compute_providers ?? [],
    storageSchemes: wire.storage_schemes ?? [],
    tools: (wire.tools ?? []).map((tool) => ({
      id: tool.id,
      kind: tool.kind,
      command: tool.command,
      description: tool.description,
      installed: tool.installed,
      enabled: tool.enabled,
      available: tool.available,
      path: tool.path ?? null,
    })),
  };
}

function infrastructureStatusFromWire(
  wire: ScienceInfrastructureStatusWire,
): ScienceInfrastructureStatus {
  return {
    scope: wire.scope,
    configPath: wire.config_path,
    computeConfig: computeConfigFromWire(wire.compute_config),
    storageConfig: storageConfigFromWire(wire.storage_config),
    compute: (wire.compute ?? []).map(providerStatusFromWire),
    storage: (wire.storage ?? []).map(providerStatusFromWire),
  };
}

function configurationPatchToWire(patch: ScienceConfigurationPatch): Record<string, unknown> {
  const wire: Record<string, unknown> = {};
  if (patch.computeConfig) {
    wire.compute_config = {
      default_provider: patch.computeConfig.defaultProvider,
      providers: patch.computeConfig.providers,
    };
  }
  if (patch.storageConfig) {
    wire.storage_config = {
      default_provider: patch.storageConfig.defaultProvider,
      allowed_roots: patch.storageConfig.allowedRoots,
      providers: patch.storageConfig.providers,
    };
  }
  return wire;
}

function taskFromWire(wire: ScienceTaskWire): ScienceTask {
  return {
    id: wire.id,
    title: wire.title,
    instructions: wire.instructions ?? "",
    status: wire.status,
    parentId: wire.parent_id ?? null,
    dependsOn: wire.depends_on ?? [],
    assignedAgent: wire.assigned_agent ?? null,
    assignedSession: wire.assigned_session ?? null,
    expectedOutputs: wire.expected_outputs ?? [],
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

function runFromWire(wire: ScienceRunWire): ScienceRun {
  return {
    id: wire.id,
    provider: wire.provider ?? "local",
    status: wire.status,
    command: wire.command ?? [],
    queuedAt: wire.queued_at,
    startedAt: wire.started_at ?? null,
    finishedAt: wire.finished_at ?? null,
    exitCode: wire.exit_code ?? null,
    costUsd: wire.cost_usd ?? null,
    outputArtifactIds: wire.output_artifact_ids ?? [],
    createdAt: wire.created_at,
  };
}

function artifactFromWire(wire: ScienceArtifactWire): ScienceArtifact {
  return {
    id: wire.id,
    path: wire.path ?? null,
    uri: wire.uri ?? null,
    type: wire.type ?? "file",
    mime: wire.mime ?? null,
    sizeBytes: wire.size_bytes ?? null,
    runId: wire.run_id ?? null,
    taskId: wire.task_id ?? null,
    createdAt: wire.created_at,
  };
}

function researchLogEntryFromWire(wire: ScienceResearchLogEntryWire): ScienceResearchLogEntry {
  return {
    id: wire.id,
    taskId: wire.task_id ?? null,
    agent: wire.agent ?? null,
    session: wire.session ?? null,
    summary: wire.summary ?? "",
    filesChanged: wire.files_changed ?? [],
    runIds: wire.run_ids ?? [],
    artifactIds: wire.artifact_ids ?? [],
    sources: wire.sources ?? [],
    assumptions: wire.assumptions ?? [],
    limitations: wire.limitations ?? [],
    uncertainties: wire.uncertainties ?? [],
    requestedNextStep: wire.requested_next_step ?? null,
    createdAt: wire.created_at,
  };
}

function reviewFromWire(wire: ScienceReviewWire): ScienceReview {
  return {
    id: wire.id,
    sessionId: wire.session_id ?? null,
    reviewerAgent: wire.reviewer_agent ?? null,
    reviewerSession: wire.reviewer_session ?? null,
    reviewerModel: wire.reviewer_model ?? null,
    reviewerHarness: wire.reviewer_harness ?? null,
    reviewedThrough: wire.reviewed_through ?? null,
    summary: wire.summary ?? "",
    issueIds: wire.issue_ids ?? [],
    createdAt: wire.created_at,
  };
}

function issueFromWire(wire: ScienceIssueWire): ScienceIssue {
  return {
    id: wire.id,
    sessionId: wire.session_id ?? null,
    taskId: wire.task_id ?? null,
    researchLogId: wire.research_log_id ?? null,
    category: wire.category ?? "scientific",
    severity: wire.severity,
    status: wire.status,
    title: wire.title,
    description: wire.description,
    evidenceRefs: wire.evidence_refs ?? [],
    verificationQuestion: wire.verification_question,
    confidence: wire.confidence ?? 0.5,
    raisedBy: wire.raised_by ?? null,
    resolution: wire.resolution ?? null,
    resolvedBy: wire.resolved_by ?? null,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

function approvalFromWire(wire: ScienceApprovalWire): ScienceApproval {
  return {
    id: wire.id,
    action: wire.action,
    scope: wire.scope ?? null,
    requestingSession: wire.requesting_session ?? null,
    requestingAgent: wire.requesting_agent ?? null,
    decision: wire.decision,
    actor: wire.actor ?? null,
    decidedAt: wire.decided_at ?? null,
    reason: wire.reason ?? null,
    expiresAt: wire.expires_at ?? null,
    consumedAt: wire.consumed_at ?? null,
    revokedAt: wire.revoked_at ?? null,
    revokedBy: wire.revoked_by ?? null,
    revocationReason: wire.revocation_reason ?? null,
    scopeKind: wire.scope_kind ?? "one_time",
    createdAt: wire.created_at,
  };
}

function skillFromWire(wire: ScienceSkillWire): ScienceSkill {
  return {
    name: wire.name ?? "",
    description: wire.description ?? null,
    version: wire.version ?? wire.revision?.slice(0, 7) ?? null,
    enabled: wire.enabled ?? null,
  };
}

// ── fetchers ─────────────────────────────────────────────────────────────────

/** Build a `/v1/science` URL with the mandatory `project` query parameter. */
function scienceUrl(path: string, project: string, extra?: Record<string, string>): string {
  const params = new URLSearchParams({ project });
  if (extra) {
    for (const [key, value] of Object.entries(extra)) params.set(key, value);
  }
  return `${path}?${params.toString()}`;
}

/** Authenticated, project-scoped URL for previewing a local artifact. */
export function scienceArtifactContentUrl(project: string, artifactId: string): string {
  return scienceUrl(`/v1/science/artifacts/${encodeURIComponent(artifactId)}/content`, project);
}

/** Snapshot of project state: project record + entity counts. */
export async function getScienceProjectStatus(project: string): Promise<ScienceProjectStatus> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/project/status", project));
  const wire = await readJsonOrThrow<ScienceProjectStatusWire>(res);
  return {
    project: projectFromWire(wire.project),
    counts: {
      tasks: wire.counts?.tasks ?? 0,
      tasksByStatus: wire.counts?.tasks_by_status ?? {},
      researchLog: wire.counts?.research_log ?? 0,
      reviews: wire.counts?.reviews ?? 0,
      issues: wire.counts?.issues ?? 0,
      openIssues: wire.counts?.open_issues ?? 0,
      runs: wire.counts?.runs ?? 0,
      artifacts: wire.counts?.artifacts ?? 0,
      approvals: wire.counts?.approvals ?? 0,
    },
  };
}

/** Application-level CLI readiness shared by every project. */
export async function getScienceTools(): Promise<ScienceToolsStatus> {
  const res = await authenticatedFetch("/v1/science/tools");
  return toolsStatusFromWire(await readJsonOrThrow<ScienceToolsStatusWire>(res));
}

/** Reusable app-level compute and storage connector definitions. */
export async function getScienceInfrastructure(): Promise<ScienceInfrastructureStatus> {
  const res = await authenticatedFetch("/v1/science/infrastructure");
  return infrastructureStatusFromWire(await readJsonOrThrow<ScienceInfrastructureStatusWire>(res));
}

/** Persist app-level connector definitions inherited by every project. */
export async function updateScienceInfrastructure(
  patch: ScienceConfigurationPatch,
): Promise<ScienceInfrastructureStatus> {
  const res = await authenticatedFetch("/v1/science/infrastructure", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(configurationPatchToWire(patch)),
  });
  return infrastructureStatusFromWire(await readJsonOrThrow<ScienceInfrastructureStatusWire>(res));
}

/** Initialize a local science project and return its initial status. */
export async function createScienceProject(directory: string): Promise<ScienceProjectStatus> {
  const res = await authenticatedFetch("/v1/science/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ directory }),
  });
  const wire = await readJsonOrThrow<ScienceProjectStatusWire>(res);
  return {
    project: projectFromWire(wire.project),
    counts: {
      tasks: wire.counts?.tasks ?? 0,
      tasksByStatus: wire.counts?.tasks_by_status ?? {},
      researchLog: wire.counts?.research_log ?? 0,
      reviews: wire.counts?.reviews ?? 0,
      issues: wire.counts?.issues ?? 0,
      openIssues: wire.counts?.open_issues ?? 0,
      runs: wire.counts?.runs ?? 0,
      artifacts: wire.counts?.artifacts ?? 0,
      approvals: wire.counts?.approvals ?? 0,
    },
  };
}

/** All tasks in the project plan. */
export async function listScienceTasks(project: string): Promise<ScienceTask[]> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/tasks", project));
  const wire = await readJsonOrThrow<ScienceTaskWire[]>(res);
  return wire.map(taskFromWire);
}

/**
 * Advisory background reviews, optionally filtered to one session.
 */
export async function listScienceReviews(
  project: string,
  sessionId?: string,
): Promise<ScienceReview[]> {
  const res = await authenticatedFetch(
    scienceUrl("/v1/science/reviews", project, sessionId ? { session_id: sessionId } : undefined),
  );
  const wire = await readJsonOrThrow<ScienceReviewWire[]>(res);
  return wire.map(reviewFromWire);
}

/** Reviewer issue checklist, optionally filtered to one status/session. */
export async function listScienceIssues(
  project: string,
  status?: ScienceIssueStatus,
  sessionId?: string,
): Promise<ScienceIssue[]> {
  const extra: Record<string, string> = {};
  if (status) extra.status = status;
  if (sessionId) extra.session_id = sessionId;
  const res = await authenticatedFetch(
    scienceUrl("/v1/science/issues", project, Object.keys(extra).length ? extra : undefined),
  );
  const wire = await readJsonOrThrow<ScienceIssueWire[]>(res);
  return wire.map(issueFromWire);
}

/** Resolve, dismiss, or reopen one reviewer issue. */
export async function updateScienceIssue(
  project: string,
  issueId: string,
  status: ScienceIssueStatus,
  resolution?: string,
): Promise<ScienceIssue> {
  const res = await authenticatedFetch(
    scienceUrl(`/v1/science/issues/${encodeURIComponent(issueId)}`, project),
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status,
        resolution,
        resolved_by: "web-user",
      }),
    },
  );
  return issueFromWire(await readJsonOrThrow<ScienceIssueWire>(res));
}

/**
 * Runs (jobs) in the project.
 */
export async function listScienceRuns(project: string): Promise<ScienceRun[]> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/jobs", project));
  const wire = await readJsonOrThrow<ScienceRunWire[]>(res);
  return wire.map(runFromWire);
}

/** Tail of a run's captured stdout/stderr. */
export async function getScienceRunLogs(
  project: string,
  runId: string,
  cursor?: string,
): Promise<ScienceRunLogs> {
  const res = await authenticatedFetch(
    scienceUrl(
      `/v1/science/jobs/${encodeURIComponent(runId)}/logs`,
      project,
      cursor ? { cursor } : undefined,
    ),
  );
  return readJsonOrThrow<ScienceRunLogs>(res);
}

/** Request cancellation of a queued/running job. */
export async function cancelScienceRun(project: string, runId: string): Promise<ScienceRun> {
  const res = await authenticatedFetch(
    scienceUrl(`/v1/science/jobs/${encodeURIComponent(runId)}:cancel`, project),
    { method: "POST" },
  );
  return runFromWire(await readJsonOrThrow<ScienceRunWire>(res));
}

/** Artifacts produced by one run. */
export async function listScienceRunOutputs(
  project: string,
  runId: string,
): Promise<ScienceArtifact[]> {
  const res = await authenticatedFetch(
    scienceUrl(`/v1/science/jobs/${encodeURIComponent(runId)}/outputs`, project),
  );
  const wire = await readJsonOrThrow<ScienceArtifactWire[]>(res);
  return wire.map(artifactFromWire);
}

/** Complete append-only research record for a workspace. */
export async function listScienceResearchLog(project: string): Promise<ScienceResearchLogEntry[]> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/research-log", project));
  const wire = await readJsonOrThrow<ScienceResearchLogEntryWire[]>(res);
  return wire.map(researchLogEntryFromWire);
}

/** All artifacts registered across tasks and runs in a workspace. */
export async function listScienceArtifacts(project: string): Promise<ScienceArtifact[]> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/artifacts", project));
  const wire = await readJsonOrThrow<ScienceArtifactWire[]>(res);
  return wire.map(artifactFromWire);
}

/** Approval records (pending + decided) for the project. */
export async function listScienceApprovals(project: string): Promise<ScienceApproval[]> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/approvals", project));
  const wire = await readJsonOrThrow<ScienceApprovalWire[]>(res);
  return wire.map(approvalFromWire);
}

/** Resolve one pending semantic approval from the human-facing UI. */
export async function resolveScienceApproval(
  project: string,
  approvalId: string,
  decision: Exclude<ScienceApprovalDecision, "pending" | "revoked">,
  scopeKind: ScienceApprovalScopeKind = "one_time",
): Promise<ScienceApproval> {
  const res = await authenticatedFetch(
    scienceUrl(`/v1/science/approvals/${encodeURIComponent(approvalId)}:resolve`, project),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        actor: "web-user",
        scope_kind: scopeKind,
      }),
    },
  );
  return approvalFromWire(await readJsonOrThrow<ScienceApprovalWire>(res));
}

/** Revoke one previously approved semantic permission. */
export async function revokeScienceApproval(
  project: string,
  approvalId: string,
): Promise<ScienceApproval> {
  const res = await authenticatedFetch(
    scienceUrl(`/v1/science/approvals/${encodeURIComponent(approvalId)}:revoke`, project),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "web-user" }),
    },
  );
  return approvalFromWire(await readJsonOrThrow<ScienceApprovalWire>(res));
}

/**
 * Installed science skills for a project, including enablement state.
 */
export async function listScienceSkills(project: string): Promise<ScienceSkill[]> {
  const res = await authenticatedFetch(scienceUrl("/v1/science/skills", project));
  const wire = await readJsonOrThrow<ScienceSkillWire[]>(res);
  return wire.map(skillFromWire);
}
