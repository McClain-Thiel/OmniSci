import { CpuIcon, FlaskConicalIcon, HardDriveIcon, Settings2Icon } from "lucide-react";
import { useMemo, useState } from "react";
import { SciencePanel } from "@/components/science/SciencePanel";
import { Button } from "@/components/ui/button";
import { useConversations } from "@/hooks/useConversations";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { Link } from "@/lib/routing";

export function ProjectPage() {
  const info = useServerInfo();
  const available = info !== "loading" && info.science_enabled;
  const conversationsQuery = useConversations("", true);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const workspaces = useMemo(() => {
    const seen = new Set<string>();
    return (
      conversationsQuery.data?.pages
        .flatMap((page) => page.data)
        .filter((session) => {
          if (!session.workspace || seen.has(session.workspace)) return false;
          seen.add(session.workspace);
          return true;
        }) ?? []
    );
  }, [conversationsQuery.data]);
  const selected =
    workspaces.find((session) => session.workspace === selectedWorkspace) ?? workspaces[0] ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col px-3 pb-3 pt-16 md:px-6 md:pb-6">
      <div className="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-3 px-1">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex size-8 items-center justify-center rounded-lg bg-teal-500/10 text-teal-700 dark:text-teal-300">
              <FlaskConicalIcon className="size-4" />
            </span>
            <h1 className="text-2xl font-semibold tracking-tight">Provenance</h1>
          </div>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Inspect the traceable record of plans, decisions, compute, artifacts and review.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button asChild variant="outline" size="sm">
            <Link to="/settings/compute">
              <CpuIcon className="size-4" />
              Compute
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/settings/storage">
              <HardDriveIcon className="size-4" />
              Storage
            </Link>
          </Button>
          <Button asChild variant="ghost" size="icon-sm">
            <Link to="/settings/research" aria-label="Provenance settings">
              <Settings2Icon className="size-4" />
            </Link>
          </Button>
        </div>
      </div>

      {workspaces.length > 1 && (
        <div className="mb-2 flex shrink-0 gap-1 overflow-x-auto px-1 pb-1">
          {workspaces.map((session) => (
            <Button
              key={session.workspace}
              type="button"
              size="sm"
              variant={session.workspace === selected?.workspace ? "secondary" : "ghost"}
              className="shrink-0"
              onClick={() => setSelectedWorkspace(session.workspace ?? null)}
            >
              {session.workspace?.split("/").filter(Boolean).pop() ?? session.workspace}
            </Button>
          ))}
        </div>
      )}
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        {available ? (
          selected ? (
            <SciencePanel conversationId={selected.id} projectDir={selected.workspace ?? null} />
          ) : (
            <div className="flex flex-1 items-center justify-center p-8 text-center">
              <div className="max-w-md">
                <FlaskConicalIcon className="mx-auto size-7 text-muted-foreground" />
                <h2 className="mt-3 text-sm font-medium">No workspace provenance yet</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Start a session in a folder. OmniSci will use that working directory
                  automatically.
                </p>
              </div>
            </div>
          )
        ) : (
          <div className="flex flex-1 items-center justify-center p-8 text-center">
            <div className="max-w-md">
              <FlaskConicalIcon className="mx-auto size-7 text-muted-foreground" />
              <h2 className="mt-3 text-sm font-medium">Provenance service unavailable</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                This OmniSci server was started without the scientific provenance package.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
