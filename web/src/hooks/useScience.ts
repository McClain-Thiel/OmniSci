// TanStack Query hooks for the science panel, wrapping the fetchers in
// `@/lib/scienceApi`. All project-scoped queries key off the project
// directory (the v1 server has no project registry — the directory IS the
// address) and are disabled until the user has set one.
// Queries use `retry: false` so project/API errors surface without
// three redundant requests.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelScienceRun,
  createScienceProject,
  getScienceInfrastructure,
  getScienceProjectStatus,
  getScienceTools,
  getScienceRunLogs,
  listScienceApprovals,
  listScienceArtifacts,
  listScienceIssues,
  listScienceResearchLog,
  listScienceReviews,
  listScienceRunOutputs,
  listScienceRuns,
  listScienceSkills,
  listScienceTasks,
  resolveScienceApproval,
  revokeScienceApproval,
  updateScienceIssue,
  updateScienceInfrastructure,
  type ScienceApprovalDecision,
  type ScienceApprovalScopeKind,
  type ScienceIssueStatus,
  type ScienceRun,
} from "@/lib/scienceApi";

// ── Query keys ───────────────────────────────────────────────────────────────

/**
 * Root key for all science queries. `null` project keys are only used while
 * a hook is disabled (no project set), so they never fetch.
 */
export function scienceQueryKey(project: string | null, ...parts: string[]) {
  return ["science", project ?? "", ...parts];
}

// ── Hooks ────────────────────────────────────────────────────────────────────

/** Project record + entity counts; drives the panel header status line. */
export function useScienceProjectStatus(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "status"),
    queryFn: () => getScienceProjectStatus(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
    refetchInterval: 5_000,
  });
}

/** Application-level CLI readiness shared by every project. */
export function useScienceTools() {
  return useQuery({
    queryKey: ["science", "tools"],
    queryFn: getScienceTools,
    retry: false,
    staleTime: 5_000,
  });
}

/** App-level connectors shared by every science project. */
export function useScienceInfrastructure() {
  return useQuery({
    queryKey: ["science", "infrastructure"],
    queryFn: getScienceInfrastructure,
    retry: false,
    staleTime: 5_000,
  });
}

/** Save app-level connectors and refresh project tool/readiness views. */
export function useUpdateScienceInfrastructure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateScienceInfrastructure,
    onSuccess: (status) => {
      queryClient.setQueryData(["science", "infrastructure"], status);
      void queryClient.invalidateQueries({ queryKey: ["science"] });
    },
  });
}

/** Initialize a project directory through the science server. */
export function useCreateScienceProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (directory: string) => createScienceProject(directory),
    onSuccess: (status) => {
      queryClient.setQueryData(scienceQueryKey(status.project.directory, "status"), status);
    },
  });
}

/** Plan tab: all tasks in the project. */
export function useScienceTasks(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "tasks"),
    queryFn: () => listScienceTasks(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
  });
}

/** Append-only research log shown in the Provenance record. */
export function useScienceResearchLog(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "research-log"),
    queryFn: () => listScienceResearchLog(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
    refetchInterval: 5_000,
  });
}

/** Registered artifacts shown alongside the research log. */
export function useScienceArtifacts(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "artifacts"),
    queryFn: () => listScienceArtifacts(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
    refetchInterval: 5_000,
  });
}

/** Completed advisory review scans. */
export function useScienceReviews(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "reviews"),
    queryFn: () => listScienceReviews(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
    refetchInterval: 5_000,
  });
}

/** Quiet reviewer checklist. */
export function useScienceIssues(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "issues"),
    queryFn: () => listScienceIssues(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
    refetchInterval: 5_000,
  });
}

/** Resolve, dismiss, or reopen one issue and refresh the checklist. */
export function useUpdateScienceIssue(project: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      issueId,
      status,
      resolution,
    }: {
      issueId: string;
      status: ScienceIssueStatus;
      resolution?: string;
    }) => updateScienceIssue(project!, issueId, status, resolution),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scienceQueryKey(project, "issues") });
      void queryClient.invalidateQueries({ queryKey: scienceQueryKey(project, "status") });
    },
  });
}

function isActiveRun(run: ScienceRun): boolean {
  return run.status === "queued" || run.status === "running";
}

/**
 * Runs tab: jobs in the project. Polls while any run is queued/running so
 * status flips (and newly finished runs) show up without a manual refresh.
 */
export function useScienceRuns(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "runs"),
    queryFn: () => listScienceRuns(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
    refetchInterval: (query) => (query.state.data?.some(isActiveRun) ? 3_000 : false),
  });
}

/** Logs for one run, fetched on demand when its row is expanded. */
export function useScienceRunLogs(project: string | null, runId: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "run-logs", runId ?? ""),
    queryFn: () => getScienceRunLogs(project!, runId!),
    enabled: !!project && !!runId,
    retry: false,
    staleTime: 1_000,
  });
}

/** Output artifacts for one run, fetched on demand when its row is expanded. */
export function useScienceRunOutputs(project: string | null, runId: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "run-outputs", runId ?? ""),
    queryFn: () => listScienceRunOutputs(project!, runId!),
    enabled: !!project && !!runId,
    retry: false,
    staleTime: 5_000,
  });
}

/** Cancel a queued/running job, then refresh the runs list. */
export function useCancelScienceRun(project: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => cancelScienceRun(project!, runId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scienceQueryKey(project, "runs") });
    },
  });
}

/** Approvals tab: pending + recently decided approval records. */
export function useScienceApprovals(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "approvals"),
    queryFn: () => listScienceApprovals(project!),
    enabled: !!project,
    retry: false,
    staleTime: 2_000,
  });
}

/** Resolve an approval and refresh the approval list. */
export function useResolveScienceApproval(project: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      approvalId,
      decision,
      scopeKind,
    }: {
      approvalId: string;
      decision: Exclude<ScienceApprovalDecision, "pending" | "revoked">;
      scopeKind: ScienceApprovalScopeKind;
    }) => resolveScienceApproval(project!, approvalId, decision, scopeKind),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scienceQueryKey(project, "approvals") });
    },
  });
}

/** Revoke an approved permission and refresh the approval list. */
export function useRevokeScienceApproval(project: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (approvalId: string) => revokeScienceApproval(project!, approvalId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scienceQueryKey(project, "approvals") });
    },
  });
}

/**
 * Skills tab: installed skills and project enablement state.
 */
export function useScienceSkills(project: string | null) {
  return useQuery({
    queryKey: scienceQueryKey(project, "skills"),
    queryFn: () => listScienceSkills(project!),
    enabled: !!project,
    retry: false,
    staleTime: 30_000,
  });
}
