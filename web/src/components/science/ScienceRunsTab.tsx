import { ChevronDownIcon, ChevronRightIcon, FileSearchIcon, XCircleIcon } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { ScienceRun, ScienceRunState } from "@/lib/scienceApi";
import { Link } from "@/lib/routing";
import {
  useCancelScienceRun,
  useScienceRunLogs,
  useScienceRunOutputs,
  useScienceRuns,
} from "@/hooks/useScience";
import { ScienceEmpty, ScienceLoading, ScienceQueryError, formatScienceTime } from "./SciencePanel";

const STATE_STYLES: Record<ScienceRunState, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  succeeded: "bg-success/15 text-success",
  failed: "bg-destructive/15 text-destructive",
  cancelled: "bg-muted text-muted-foreground",
  timeout: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
};

function formatBytes(bytes: number | null): string | null {
  if (bytes === null) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Expanded row detail: captured logs + output artifacts, fetched on demand. */
function RunDetail({
  project,
  conversationId,
  onOpenFile,
  run,
}: {
  project: string;
  conversationId: string;
  onOpenFile?: (path: string) => void;
  run: ScienceRun;
}) {
  const logsQuery = useScienceRunLogs(project, run.id);
  const outputsQuery = useScienceRunOutputs(project, run.id);
  return (
    <div className="space-y-2 px-1.5 pb-2 pt-1">
      <div>
        <div className="pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Logs
        </div>
        {logsQuery.isPending ? (
          <div className="text-[11px] text-muted-foreground">Loading logs…</div>
        ) : logsQuery.isError ? (
          <div className="text-[11px] text-muted-foreground">Logs unavailable.</div>
        ) : logsQuery.data.stdout === "" && logsQuery.data.stderr === "" ? (
          <div className="text-[11px] text-muted-foreground">No log output yet.</div>
        ) : (
          <pre className="max-h-48 overflow-auto rounded bg-muted p-1.5 text-[10px] leading-4">
            {logsQuery.data.stdout}
            {logsQuery.data.stderr && (
              <span className="text-destructive">{logsQuery.data.stderr}</span>
            )}
          </pre>
        )}
      </div>
      <div>
        <div className="pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Outputs
        </div>
        {outputsQuery.isPending ? (
          <div className="text-[11px] text-muted-foreground">Loading outputs…</div>
        ) : outputsQuery.isError ? (
          <div className="text-[11px] text-muted-foreground">Outputs unavailable.</div>
        ) : outputsQuery.data.length === 0 ? (
          <div className="text-[11px] text-muted-foreground">No output artifacts.</div>
        ) : (
          <ul className="space-y-0.5">
            {outputsQuery.data.map((artifact) => (
              <li
                key={artifact.id}
                className="flex min-w-0 items-center gap-1 text-[11px]"
                title={artifact.uri ?? ""}
              >
                <span className="text-muted-foreground">[{artifact.type}]</span>{" "}
                {artifact.path ? (
                  <Link
                    className="min-w-0 truncate text-primary underline-offset-2 hover:underline"
                    to={`/c/${conversationId}?file=${encodeURIComponent(artifact.path)}`}
                    onClick={(event) => {
                      if (!onOpenFile) return;
                      event.preventDefault();
                      onOpenFile(artifact.path!);
                    }}
                  >
                    {artifact.path}
                  </Link>
                ) : (
                  <span className="min-w-0 truncate">{artifact.uri ?? artifact.id}</span>
                )}
                {formatBytes(artifact.sizeBytes) && (
                  <span className="text-muted-foreground">
                    {" "}
                    · {formatBytes(artifact.sizeBytes)}
                  </span>
                )}
                {artifact.path && (
                  <FileSearchIcon
                    aria-hidden="true"
                    className="size-3 shrink-0 text-muted-foreground"
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/**
 * Runs tab: jobs with status, logs, cancel and outputs (spec §14.2).
 */
export function ScienceRunsTab({
  project,
  conversationId,
  onOpenFile,
}: {
  project: string;
  conversationId: string;
  onOpenFile?: (path: string) => void;
}) {
  const runsQuery = useScienceRuns(project);
  const cancelMutation = useCancelScienceRun(project);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  if (runsQuery.isPending) return <ScienceLoading />;
  if (runsQuery.isError) return <ScienceQueryError error={runsQuery.error} />;
  const runs = runsQuery.data;
  if (runs.length === 0) {
    return <ScienceEmpty>No runs yet — submitted jobs appear here.</ScienceEmpty>;
  }
  const sorted = [...runs].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return (
    <ul className="px-2 py-2">
      {sorted.map((run) => {
        const expanded = expandedRunId === run.id;
        const cancellable = run.status === "queued" || run.status === "running";
        return (
          <li key={run.id} className="rounded text-xs">
            <div className="flex items-start gap-1 px-1.5 py-1.5">
              <button
                type="button"
                aria-expanded={expanded}
                aria-label={`Toggle details for run ${run.id}`}
                className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
                onClick={() => setExpandedRunId(expanded ? null : run.id)}
              >
                {expanded ? (
                  <ChevronDownIcon className="size-3.5" />
                ) : (
                  <ChevronRightIcon className="size-3.5" />
                )}
              </button>
              <span
                className={cn(
                  "mt-0.5 inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium leading-none",
                  STATE_STYLES[run.status],
                )}
              >
                {run.status}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[11px] leading-snug">
                  {run.command.length > 0 ? run.command.join(" ") : run.id}
                </span>
                <span className="block truncate text-[10px] text-muted-foreground">
                  {[
                    run.provider,
                    run.exitCode !== null ? `exit ${run.exitCode}` : null,
                    formatScienceTime(run.startedAt ?? run.queuedAt),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
              {cancellable && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 shrink-0 px-1.5 text-[10px] text-muted-foreground"
                  disabled={cancelMutation.isPending}
                  onClick={() => cancelMutation.mutate(run.id)}
                >
                  <XCircleIcon className="size-3.5" />
                  Cancel
                </Button>
              )}
            </div>
            {expanded && (
              <RunDetail
                project={project}
                conversationId={conversationId}
                onOpenFile={onOpenFile}
                run={run}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}
