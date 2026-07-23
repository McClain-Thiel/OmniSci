import { ExternalLinkIcon, FileSearchIcon, FlaskConicalIcon, Loader2Icon } from "lucide-react";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useScienceArtifacts,
  useScienceProjectStatus,
  useScienceResearchLog,
} from "@/hooks/useScience";
import { scienceArtifactContentUrl, ScienceApiError } from "@/lib/scienceApi";
import { Link } from "@/lib/routing";
import { SciencePlanTab } from "./SciencePlanTab";
import { ScienceIssuesTab } from "./ScienceIssuesTab";
import { ScienceRunsTab } from "./ScienceRunsTab";
import { ScienceApprovalsTab } from "./ScienceApprovalsTab";
import { ScienceSkillsTab } from "./ScienceSkillsTab";

// ── shared bits (used by the per-tab components in this directory) ──────────

/** Centered spinner shown while a science query is in flight. */
export function ScienceLoading() {
  return (
    <div className="flex flex-1 items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
      <Loader2Icon className="size-3.5 animate-spin" />
      Loading…
    </div>
  );
}

/** Inline error state for a failed science query. */
export function ScienceQueryError({ error }: { error: unknown }) {
  const notFound = error instanceof ScienceApiError && error.status === 404;
  const message = notFound
    ? "The requested science project resource was not found."
    : error instanceof Error
      ? error.message
      : "Failed to load science data.";
  return <div className="px-3 py-6 text-center text-xs text-muted-foreground">{message}</div>;
}

/** Muted centered empty state. */
export function ScienceEmpty({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-6 text-center text-xs text-muted-foreground">{children}</div>;
}

/** Small uppercase section heading inside a tab. */
export function ScienceSectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="px-2 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </h3>
  );
}

/** Locale-short timestamp for the ISO strings the science API returns. */
export function formatScienceTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── panel ────────────────────────────────────────────────────────────────────

type SciencePanelTab = "record" | "plan" | "issues" | "runs" | "approvals" | "skills";

/**
 * Header status line: project name + entity counts once the status probe
 * lands, with graceful loading / error / no-project fallbacks.
 */
function ScienceStatusLine({ project }: { project: string }) {
  const statusQuery = useScienceProjectStatus(project);
  if (statusQuery.isPending) {
    return <span className="text-muted-foreground">Loading project…</span>;
  }
  if (statusQuery.isError) {
    return (
      <span className="text-muted-foreground">
        {statusQuery.error instanceof ScienceApiError && statusQuery.error.status === 404
          ? "Workspace folder not found."
          : "Failed to load project status."}
      </span>
    );
  }
  const { project: proj, counts } = statusQuery.data;
  const done = counts.tasksByStatus["done"] ?? 0;
  return (
    <span className="truncate text-muted-foreground" title={proj.directory}>
      <span className="font-medium text-foreground">{proj.name}</span>
      {` — ${done}/${counts.tasks} tasks · ${counts.runs} runs · ${counts.openIssues} open issues`}
    </span>
  );
}

function ScienceRecordTab({
  project,
  conversationId,
  onOpenFile,
}: {
  project: string;
  conversationId: string;
  onOpenFile?: (path: string) => void;
}) {
  const logQuery = useScienceResearchLog(project);
  const artifactsQuery = useScienceArtifacts(project);

  if (logQuery.isPending || artifactsQuery.isPending) return <ScienceLoading />;
  if (logQuery.isError) return <ScienceQueryError error={logQuery.error} />;
  if (artifactsQuery.isError) return <ScienceQueryError error={artifactsQuery.error} />;

  const entries = [...logQuery.data].reverse();
  const artifacts = [...artifactsQuery.data].reverse();
  return (
    <div className="pb-3">
      <ScienceSectionHeading>Research log</ScienceSectionHeading>
      {entries.length === 0 ? (
        <ScienceEmpty>
          No recorded decisions yet. Agent actions linked to this workspace will appear here.
        </ScienceEmpty>
      ) : (
        <div className="space-y-1 px-2">
          {entries.map((entry) => (
            <article key={entry.id} className="rounded-md border border-border px-2.5 py-2">
              <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span className="truncate">
                  {entry.agent ?? "agent"}
                  {entry.session === conversationId ? " · this session" : ""}
                </span>
                <time className="shrink-0">{formatScienceTime(entry.createdAt)}</time>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-xs leading-5">{entry.summary}</p>
              {(entry.filesChanged.length > 0 ||
                entry.runIds.length > 0 ||
                entry.artifactIds.length > 0) && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  {[
                    entry.filesChanged.length > 0
                      ? `${entry.filesChanged.length} file${entry.filesChanged.length === 1 ? "" : "s"}`
                      : null,
                    entry.runIds.length > 0
                      ? `${entry.runIds.length} run${entry.runIds.length === 1 ? "" : "s"}`
                      : null,
                    entry.artifactIds.length > 0
                      ? `${entry.artifactIds.length} artifact${entry.artifactIds.length === 1 ? "" : "s"}`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              )}
            </article>
          ))}
        </div>
      )}

      <ScienceSectionHeading>Artifacts</ScienceSectionHeading>
      {artifacts.length === 0 ? (
        <ScienceEmpty>No registered figures, data, results or logs yet.</ScienceEmpty>
      ) : (
        <div className="space-y-1 px-2">
          {artifacts.map((artifact) => {
            const content = (
              <>
                <span className="min-w-0 flex-1 truncate">
                  {artifact.path ?? artifact.uri ?? artifact.id}
                </span>
                <span className="shrink-0 text-[10px] uppercase text-muted-foreground">
                  {artifact.type}
                </span>
                {artifact.path ? (
                  <FileSearchIcon className="size-3 shrink-0 text-muted-foreground" />
                ) : (
                  <ExternalLinkIcon className="size-3 shrink-0 text-muted-foreground" />
                )}
              </>
            );
            const className =
              "flex items-center gap-2 rounded-md border border-border px-2.5 py-2 text-xs hover:bg-muted/50";
            return artifact.path ? (
              <Link
                key={artifact.id}
                to={`/c/${conversationId}?file=${encodeURIComponent(artifact.path)}`}
                onClick={(event) => {
                  if (!onOpenFile) return;
                  event.preventDefault();
                  onOpenFile(artifact.path!);
                }}
                className={className}
              >
                {content}
              </Link>
            ) : (
              <a
                key={artifact.id}
                href={scienceArtifactContentUrl(project, artifact.id)}
                target="_blank"
                rel="noreferrer"
                className={className}
              >
                {content}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * Scientific provenance workspace. The directory comes from the active
 * session's workspace; users never paste a second project path.
 */
export function SciencePanel({
  conversationId,
  projectDir,
  onOpenFile,
}: {
  conversationId: string;
  projectDir: string | null;
  onOpenFile?: (path: string) => void;
}) {
  const [tab, setTab] = useState<SciencePanelTab>("record");
  return (
    <div data-conversation-id={conversationId} className="flex min-h-0 flex-1 flex-col bg-card">
      {/* The active session workspace is the provenance root. */}
      <div className="shrink-0 space-y-1.5 border-b border-border px-2 py-2">
        <div className="flex items-center gap-2">
          <FlaskConicalIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">Provenance</span>
        </div>
        <div className="truncate px-0.5 text-[11px] leading-4">
          {projectDir ? (
            <ScienceStatusLine project={projectDir} />
          ) : (
            <span className="text-muted-foreground">This session has no workspace folder.</span>
          )}
        </div>
      </div>

      {projectDir ? (
        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as SciencePanelTab)}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList variant="pill" className="mx-2 mt-1.5 shrink-0 justify-start overflow-x-auto">
            <TabsTrigger value="record" className="h-7 px-2 text-[11px]">
              Record
            </TabsTrigger>
            <TabsTrigger value="plan" className="h-7 px-2 text-[11px]">
              Plan
            </TabsTrigger>
            <TabsTrigger value="issues" className="h-7 px-2 text-[11px]">
              Issues
            </TabsTrigger>
            <TabsTrigger value="runs" className="h-7 px-2 text-[11px]">
              Runs
            </TabsTrigger>
            <TabsTrigger value="approvals" className="h-7 px-2 text-[11px]">
              Approvals
            </TabsTrigger>
            <TabsTrigger value="skills" className="h-7 px-2 text-[11px]">
              Skills
            </TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <TabsContent value="record" className="mt-0">
              <ScienceRecordTab
                project={projectDir}
                conversationId={conversationId}
                onOpenFile={onOpenFile}
              />
            </TabsContent>
            <TabsContent value="plan" className="mt-0">
              <SciencePlanTab project={projectDir} />
            </TabsContent>
            <TabsContent value="issues" className="mt-0">
              <ScienceIssuesTab project={projectDir} />
            </TabsContent>
            <TabsContent value="runs" className="mt-0">
              <ScienceRunsTab
                project={projectDir}
                conversationId={conversationId}
                onOpenFile={onOpenFile}
              />
            </TabsContent>
            <TabsContent value="approvals" className="mt-0">
              <ScienceApprovalsTab project={projectDir} />
            </TabsContent>
            <TabsContent value="skills" className="mt-0">
              <ScienceSkillsTab project={projectDir} />
            </TabsContent>
          </div>
        </Tabs>
      ) : (
        <ScienceEmpty>
          Start this session in a workspace folder to record its plan, decisions, runs, artifacts
          and review history.
        </ScienceEmpty>
      )}
    </div>
  );
}
