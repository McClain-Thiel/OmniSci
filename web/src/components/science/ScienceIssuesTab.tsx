import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { ScienceIssue, ScienceIssueSeverity } from "@/lib/scienceApi";
import { useScienceIssues, useScienceReviews, useUpdateScienceIssue } from "@/hooks/useScience";
import {
  ScienceEmpty,
  ScienceLoading,
  ScienceQueryError,
  ScienceSectionHeading,
  formatScienceTime,
} from "./SciencePanel";

const SEVERITY_STYLES: Record<ScienceIssueSeverity, string> = {
  critical: "bg-destructive/15 text-destructive",
  major: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  concern: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  info: "bg-muted text-muted-foreground",
};

function byPriority(a: ScienceIssue, b: ScienceIssue): number {
  if (a.status === "open" && b.status !== "open") return -1;
  if (a.status !== "open" && b.status === "open") return 1;
  return b.updatedAt.localeCompare(a.updatedAt);
}

/**
 * Advisory reviewer checklist. Issues can be checked off without changing task
 * state, approvals, or the running conversation.
 */
export function ScienceIssuesTab({ project }: { project: string }) {
  const issuesQuery = useScienceIssues(project);
  const reviewsQuery = useScienceReviews(project);
  const updateIssue = useUpdateScienceIssue(project);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [resolution, setResolution] = useState("");

  if (issuesQuery.isPending) return <ScienceLoading />;
  if (issuesQuery.isError) return <ScienceQueryError error={issuesQuery.error} />;

  const issues = [...issuesQuery.data].sort(byPriority);
  const latestReview = reviewsQuery.data
    ? [...reviewsQuery.data].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]
    : null;

  return (
    <div className="pb-2">
      {latestReview ? (
        <>
          <ScienceSectionHeading>Latest review scan</ScienceSectionHeading>
          <div className="mx-2 rounded border border-border px-2 py-1.5 text-xs">
            <p className="break-words leading-snug">{latestReview.summary}</p>
            <p className="mt-0.5 text-[10px] text-muted-foreground">
              {formatScienceTime(latestReview.createdAt)}
              {latestReview.issueIds.length > 0
                ? ` · ${latestReview.issueIds.length} issue${latestReview.issueIds.length === 1 ? "" : "s"}`
                : " · no issues raised"}
            </p>
          </div>
        </>
      ) : null}

      <ScienceSectionHeading>Issue checklist</ScienceSectionHeading>
      {issues.length === 0 ? (
        <ScienceEmpty>
          No issues raised. The background reviewer records suspect assumptions and evidence gaps
          here without interrupting the conversation.
        </ScienceEmpty>
      ) : (
        <ul className="px-2">
          {issues.map((issue) => {
            const checked = issue.status !== "open";
            return (
              <li
                key={issue.id}
                className={cn(
                  "flex items-start gap-2 rounded px-1.5 py-2 text-xs",
                  checked && "opacity-60",
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={updateIssue.isPending}
                  aria-label={`${checked ? "Reopen" : "Resolve"} issue: ${issue.title}`}
                  className="mt-0.5 size-3.5 accent-primary"
                  onChange={() => {
                    if (checked) {
                      updateIssue.mutate({
                        issueId: issue.id,
                        status: "open",
                        resolution: "",
                      });
                      return;
                    }
                    setResolvingId(issue.id);
                    setResolution("");
                  }}
                />
                <div className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium leading-none",
                        SEVERITY_STYLES[issue.severity],
                      )}
                    >
                      {issue.severity}
                    </span>
                    <span className={cn("font-medium", checked && "line-through")}>
                      {issue.title}
                    </span>
                  </span>
                  <span className="mt-1 block break-words leading-snug text-muted-foreground">
                    {issue.description}
                  </span>
                  <span className="mt-1 block break-words text-[10px] text-muted-foreground">
                    Check: {issue.verificationQuestion}
                  </span>
                  {resolvingId === issue.id ? (
                    <div className="mt-2 flex items-center gap-1.5">
                      <Input
                        value={resolution}
                        onChange={(event) => setResolution(event.target.value)}
                        placeholder="Evidence that resolved this issue"
                        aria-label={`Resolution for issue: ${issue.title}`}
                        className="h-7 text-xs"
                      />
                      <Button
                        type="button"
                        size="sm"
                        className="h-7"
                        disabled={!resolution.trim() || updateIssue.isPending}
                        onClick={() => {
                          updateIssue.mutate({
                            issueId: issue.id,
                            status: "resolved",
                            resolution: resolution.trim(),
                          });
                          setResolvingId(null);
                          setResolution("");
                        }}
                      >
                        Resolve
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7"
                        onClick={() => setResolvingId(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  ) : null}
                  {checked && issue.resolution ? (
                    <span className="mt-1 block break-words text-[10px] text-muted-foreground">
                      Resolution: {issue.resolution}
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
