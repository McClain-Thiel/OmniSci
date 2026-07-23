// Small two-way header toggle used by the science data viewers (CSV table,
// JSON tree) to switch between the rich preview and the Monaco source view.

import { cn } from "@/lib/utils";

export type RichSourceView = "rich" | "source";

export function RichSourceToggle({
  richLabel,
  view,
  onViewChange,
}: {
  /** Label for the rich view button, e.g. "Table" or "Tree". */
  richLabel: string;
  view: RichSourceView;
  onViewChange: (view: RichSourceView) => void;
}) {
  const button = (target: RichSourceView, label: string) => (
    <button
      key={target}
      type="button"
      aria-pressed={view === target}
      onClick={() => onViewChange(target)}
      className={cn(
        "rounded px-2 py-0.5 text-xs",
        view === target
          ? "bg-muted font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1">
      {button("rich", richLabel)}
      {button("source", "Source")}
    </div>
  );
}
