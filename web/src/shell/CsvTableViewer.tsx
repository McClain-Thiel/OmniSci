// CSV/TSV table viewer for the science workbench (spec §14.3).

import { useMemo, useState, type ReactNode } from "react";
import { DownloadIcon } from "lucide-react";
import { downloadWorkspaceFile, type FileContentResponse } from "@/hooks/useFileContent";
import { csvDelimiterForPath } from "./codeViewerHelpers";
import { RichSourceToggle, type RichSourceView } from "./RichSourceToggle";

/** Rows rendered in the table before the "showing first N rows" notice. */
export const MAX_TABLE_ROWS = 1000;

/** Files larger than this fall back to the metadata + download panel. */
export const MAX_TABLE_BYTES = 5 * 1024 * 1024;

/**
 * Parse CSV/TSV text into rows of fields. Handles RFC-4180 quoting (quoted
 * fields may embed the delimiter, newlines, and `""` escaped quotes) and
 * CRLF/LF line endings. A trailing newline does not produce a phantom row.
 */
export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  // Skip a UTF-8 BOM so it doesn't pollute the first header cell.
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0;
  for (; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  row.push(field);
  // Drop the trailing all-empty row left by a final newline.
  if (row.length > 1 || row[0] !== "") rows.push(row);
  return rows;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function CsvTableViewer({
  data,
  path,
  conversationId,
  source,
}: {
  data: FileContentResponse;
  path: string;
  conversationId: string;
  /** The Monaco source view, shown when the user toggles to "Source". */
  source: ReactNode;
}) {
  const [view, setView] = useState<RichSourceView>("rich");
  const delimiter = csvDelimiterForPath(path) ?? ",";
  const filename = path.split("/").pop() ?? path;

  // Very large files get metadata + download rather than a parsed table.
  if (data.bytes > MAX_TABLE_BYTES) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-sm">
        <div className="font-medium">{filename}</div>
        <div className="text-muted-foreground">
          {formatBytes(data.bytes)}
          {data.content_type ? ` · ${data.content_type}` : ""}
        </div>
        <div className="text-muted-foreground">File too large to preview as a table.</div>
        <button
          type="button"
          onClick={() => void downloadWorkspaceFile(conversationId, path)}
          className="mt-2 flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-secondary"
        >
          <DownloadIcon className="size-3.5" />
          Download
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <RichSourceToggle richLabel="Table" view={view} onViewChange={setView} />
      <div className="min-h-0 flex-1">
        {view === "source" ? (
          source
        ) : (
          <DelimitedTable
            content={data.content}
            delimiter={delimiter}
            truncated={!!data.truncated}
          />
        )}
      </div>
    </div>
  );
}

function DelimitedTable({
  content,
  delimiter,
  truncated,
}: {
  content: string;
  delimiter: string;
  truncated: boolean;
}) {
  const rows = useMemo(() => parseDelimited(content, delimiter), [content, delimiter]);
  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center p-8 text-muted-foreground text-sm">
        Empty file.
      </div>
    );
  }
  const [header, ...body] = rows;
  const shown = body.slice(0, MAX_TABLE_ROWS);
  const columns = header.map((label, index) => ({ key: `${index}:${label}`, index, label }));
  const tableRows = shown.map((values, index) => ({
    key: `${index}:${values.join("\u0000")}`,
    number: index + 1,
    values,
  }));
  const hidden = body.length - shown.length;
  return (
    <div className="flex h-full flex-col">
      {(hidden > 0 || truncated) && (
        <div className="shrink-0 border-b border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
          {hidden > 0
            ? `Showing first ${shown.length.toLocaleString()} of ${body.length.toLocaleString()} rows.`
            : ""}
          {truncated ? " File was truncated by the server; rows may be incomplete." : ""}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-card">
            <tr>
              <th className="border-b border-border px-3 py-1 text-right font-medium text-muted-foreground">
                #
              </th>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className="border-b border-border px-3 py-1 text-left font-medium whitespace-nowrap"
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tableRows.map((row) => (
              <tr key={row.key} className="hover:bg-muted/40">
                <td className="border-b border-border/50 px-3 py-0.5 text-right text-muted-foreground/60 select-none">
                  {row.number}
                </td>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className="border-b border-border/50 px-3 py-0.5 whitespace-nowrap"
                  >
                    {row.values[column.index] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
