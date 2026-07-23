import { cn } from "@/lib/utils";
import type { ScienceTaskStatus } from "@/lib/scienceApi";
import { useScienceTasks } from "@/hooks/useScience";
import { ScienceEmpty, ScienceLoading, ScienceQueryError, formatScienceTime } from "./SciencePanel";

const STATUS_STYLES: Record<ScienceTaskStatus, string> = {
  pending: "bg-muted text-muted-foreground",
  in_progress: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  blocked: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  done: "bg-success/15 text-success",
  cancelled: "bg-muted text-muted-foreground line-through",
};

const STATUS_LABELS: Record<ScienceTaskStatus, string> = {
  pending: "Pending",
  in_progress: "In progress",
  blocked: "Blocked",
  done: "Done",
  cancelled: "Cancelled",
};

// Display order: active work first, terminal states last.
const STATUS_ORDER: ScienceTaskStatus[] = [
  "in_progress",
  "blocked",
  "pending",
  "done",
  "cancelled",
];

/**
 * Plan tab: the project's task list with status badges (spec §14.2). Tasks
 * are grouped by status so in-progress work surfaces above done/cancelled.
 */
export function SciencePlanTab({ project }: { project: string }) {
  const tasksQuery = useScienceTasks(project);
  if (tasksQuery.isPending) return <ScienceLoading />;
  if (tasksQuery.isError) return <ScienceQueryError error={tasksQuery.error} />;
  const tasks = tasksQuery.data;
  if (tasks.length === 0) {
    return (
      <ScienceEmpty>No tasks yet — the plan appears here once tasks are created.</ScienceEmpty>
    );
  }
  const sorted = [...tasks].sort(
    (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status),
  );
  return (
    <ul className="px-2 py-2">
      {sorted.map((task) => (
        <li key={task.id} className="flex items-start gap-2 rounded px-1.5 py-1.5 text-xs">
          <span
            className={cn(
              "mt-0.5 inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium leading-none",
              STATUS_STYLES[task.status],
            )}
          >
            {STATUS_LABELS[task.status]}
          </span>
          <span className="min-w-0">
            <span
              className={cn(
                "block break-words leading-snug",
                (task.status === "done" || task.status === "cancelled") &&
                  "text-muted-foreground line-through",
              )}
            >
              {task.title}
            </span>
            <span className="block truncate text-[10px] text-muted-foreground">
              {[
                task.assignedAgent ? `agent: ${task.assignedAgent}` : null,
                formatScienceTime(task.updatedAt),
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}
