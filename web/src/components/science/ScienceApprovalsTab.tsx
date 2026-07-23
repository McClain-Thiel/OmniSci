import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type {
  ScienceApproval,
  ScienceApprovalDecision,
  ScienceApprovalScopeKind,
} from "@/lib/scienceApi";
import {
  useResolveScienceApproval,
  useRevokeScienceApproval,
  useScienceApprovals,
} from "@/hooks/useScience";
import {
  ScienceEmpty,
  ScienceLoading,
  ScienceQueryError,
  ScienceSectionHeading,
  formatScienceTime,
} from "./SciencePanel";

const DECISION_STYLES: Record<ScienceApprovalDecision, string> = {
  pending: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  approved: "bg-success/15 text-success",
  denied: "bg-destructive/15 text-destructive",
  revoked: "bg-muted text-muted-foreground",
};

function ApprovalRow({
  approval,
  resolving,
  revoking,
  onResolve,
  onRevoke,
}: {
  approval: ScienceApproval;
  resolving: boolean;
  revoking: boolean;
  onResolve?: (
    decision: Exclude<ScienceApprovalDecision, "pending" | "revoked">,
    scopeKind: ScienceApprovalScopeKind,
  ) => void;
  onRevoke?: () => void;
}) {
  return (
    <li className="flex items-start gap-2 rounded px-1.5 py-1.5 text-xs">
      <span
        className={cn(
          "mt-0.5 inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium leading-none",
          DECISION_STYLES[approval.decision],
        )}
      >
        {approval.decision}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block break-words font-mono text-[11px] leading-snug">
          {approval.action}
        </span>
        <span className="block truncate text-[10px] text-muted-foreground">
          {[
            approval.scope,
            approval.requestingAgent ? `by ${approval.requestingAgent}` : null,
            approval.decision === "pending"
              ? `requested ${formatScienceTime(approval.createdAt)}`
              : approval.decision === "revoked"
                ? `${approval.revokedBy ?? "revoked"} ${formatScienceTime(approval.revokedAt)}`
                : `${approval.actor ?? "decided"} ${formatScienceTime(approval.decidedAt)}`,
            approval.decision === "revoked" ? approval.revocationReason : approval.reason,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
        {approval.decision === "pending" && onResolve && (
          <span className="mt-1 flex flex-wrap gap-1">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="h-6 px-1.5 text-[10px]"
              disabled={resolving}
              onClick={() => onResolve("approved", "one_time")}
            >
              Allow once
            </Button>
            {approval.action.startsWith("storage.") && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="h-6 px-1.5 text-[10px]"
                disabled={resolving}
                onClick={() => onResolve("approved", "prefix")}
              >
                Allow prefix
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="h-6 px-1.5 text-[10px]"
              disabled={resolving}
              onClick={() => onResolve("approved", "project")}
            >
              Allow project
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-6 px-1.5 text-[10px] text-destructive"
              disabled={resolving}
              onClick={() => onResolve("denied", "one_time")}
            >
              Deny
            </Button>
          </span>
        )}
        {approval.decision === "approved" &&
          onRevoke &&
          (approval.scopeKind !== "one_time" || approval.consumedAt === null) && (
            <span className="mt-1 block">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 px-1.5 text-[10px] text-destructive"
                disabled={revoking}
                onClick={onRevoke}
              >
                Revoke
              </Button>
            </span>
          )}
      </span>
    </li>
  );
}

/** Pending approval requests followed by recent decisions (spec §14.2). */
export function ScienceApprovalsTab({ project }: { project: string }) {
  const approvalsQuery = useScienceApprovals(project);
  const resolveMutation = useResolveScienceApproval(project);
  const revokeMutation = useRevokeScienceApproval(project);
  if (approvalsQuery.isPending) return <ScienceLoading />;
  if (approvalsQuery.isError) return <ScienceQueryError error={approvalsQuery.error} />;
  const approvals = approvalsQuery.data;
  if (approvals.length === 0) {
    return <ScienceEmpty>No approval requests yet.</ScienceEmpty>;
  }
  const pending = approvals
    .filter((a) => a.decision === "pending")
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  const recent = approvals
    .filter((a) => a.decision !== "pending")
    .sort((a, b) => (b.decidedAt ?? "").localeCompare(a.decidedAt ?? ""));
  return (
    <div className="pb-2">
      <ScienceSectionHeading>Pending</ScienceSectionHeading>
      {pending.length === 0 ? (
        <ScienceEmpty>Nothing awaiting approval.</ScienceEmpty>
      ) : (
        <ul className="px-2">
          {pending.map((a) => (
            <ApprovalRow
              key={a.id}
              approval={a}
              resolving={
                resolveMutation.isPending && resolveMutation.variables?.approvalId === a.id
              }
              revoking={false}
              onResolve={(decision, scopeKind) =>
                resolveMutation.mutate({ approvalId: a.id, decision, scopeKind })
              }
            />
          ))}
        </ul>
      )}
      {resolveMutation.isError && (
        <div className="px-3 py-1 text-[10px] text-destructive">
          {resolveMutation.error instanceof Error
            ? resolveMutation.error.message
            : "Failed to resolve approval."}
        </div>
      )}
      {revokeMutation.isError && (
        <div className="px-3 py-1 text-[10px] text-destructive">
          {revokeMutation.error instanceof Error
            ? revokeMutation.error.message
            : "Failed to revoke approval."}
        </div>
      )}
      <ScienceSectionHeading>Recent decisions</ScienceSectionHeading>
      {recent.length === 0 ? (
        <ScienceEmpty>No decisions recorded yet.</ScienceEmpty>
      ) : (
        <ul className="px-2">
          {recent.slice(0, 20).map((a) => (
            <ApprovalRow
              key={a.id}
              approval={a}
              resolving={false}
              revoking={revokeMutation.isPending && revokeMutation.variables === a.id}
              onRevoke={() => revokeMutation.mutate(a.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
