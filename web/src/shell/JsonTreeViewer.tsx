// Collapsible JSON tree viewer for the science workbench (spec §14.3).
//
// Renders .json files as an expandable object/array tree alongside the Monaco
// source view (passed in as `source`). Invalid JSON falls back to the source
// view with a parse-error notice. Nodes beyond COLLAPSED_DEPTH start collapsed
// so the initial DOM stays small for large documents.

import { useMemo, useState, type ReactNode } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { RichSourceToggle, type RichSourceView } from "./RichSourceToggle";

/** Nodes deeper than this depth start collapsed (root is depth 0). */
export const COLLAPSED_DEPTH = 1;

export function JsonTreeViewer({
  content,
  source,
}: {
  content: string;
  /** The Monaco source view, shown when the user toggles to "Source". */
  source: ReactNode;
}) {
  const [view, setView] = useState<RichSourceView>("rich");
  const parsed = useMemo<{ value?: unknown; error?: string }>(() => {
    try {
      return { value: JSON.parse(content) };
    } catch (e) {
      return { error: e instanceof Error ? e.message : String(e) };
    }
  }, [content]);

  return (
    <div className="flex h-full flex-col">
      <RichSourceToggle richLabel="Tree" view={view} onViewChange={setView} />
      <div className="min-h-0 flex-1">
        {view === "source" || parsed.error ? (
          <>
            {view === "rich" && parsed.error && (
              <div className="border-b border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
                Invalid JSON — showing source. {parsed.error}
              </div>
            )}
            {source}
          </>
        ) : (
          <div className="h-full overflow-auto px-3 py-2 font-mono text-xs">
            <JsonNode keyName={null} value={parsed.value} depth={0} />
          </div>
        )}
      </div>
    </div>
  );
}

function JsonNode({
  keyName,
  value,
  depth,
}: {
  keyName: string | null;
  value: unknown;
  depth: number;
}) {
  const [open, setOpen] = useState(depth <= COLLAPSED_DEPTH);

  const label = keyName !== null && (
    <span className="text-foreground">{JSON.stringify(keyName)}: </span>
  );

  if (typeof value !== "object" || value === null) {
    return (
      <div className="leading-5">
        {label}
        <LeafValue value={value} />
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries: [string, unknown][] = isArray
    ? value.map((v, i) => [String(i), v] as [string, unknown])
    : Object.entries(value as Record<string, unknown>);

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-0.5 leading-5 text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDownIcon className="size-3" /> : <ChevronRightIcon className="size-3" />}
        {label}
        <span>
          {isArray ? "[" : "{"}
          {!open && ` ${entries.length} ${isArray ? "]" : "}"}`}
        </span>
      </button>
      {open && (
        <div className="ml-2 border-l border-border/50 pl-3">
          {entries.map(([k, v]) => (
            <JsonNode key={k} keyName={k} value={v} depth={depth + 1} />
          ))}
          <div className="leading-5 text-muted-foreground">{isArray ? "]" : "}"}</div>
        </div>
      )}
    </div>
  );
}

function LeafValue({ value }: { value: unknown }) {
  if (typeof value === "string") {
    return <span className="text-green-700 dark:text-green-400">{JSON.stringify(value)}</span>;
  }
  if (typeof value === "number") {
    return <span className="text-blue-700 dark:text-blue-400">{String(value)}</span>;
  }
  // boolean or null
  return <span className="text-purple-700 dark:text-purple-400">{String(value)}</span>;
}
