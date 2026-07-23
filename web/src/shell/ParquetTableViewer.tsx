// Read-only Parquet table preview for the science workbench (spec §14.3).

import { useEffect, useState } from "react";
import { DownloadIcon } from "lucide-react";
import { parquetMetadataAsync, parquetReadObjects, parquetSchema } from "hyparquet";
import {
  downloadWorkspaceFile,
  fileContentToBlob,
  type FileContentResponse,
} from "@/hooks/useFileContent";
import { MAX_TABLE_BYTES, MAX_TABLE_ROWS } from "./CsvTableViewer";

interface ParquetPreview {
  columns: string[];
  rows: string[][];
  totalRows: bigint;
}

export function ParquetTableViewer({
  data,
  path,
  conversationId,
}: {
  data: FileContentResponse;
  path: string;
  conversationId: string;
}) {
  const [preview, setPreview] = useState<ParquetPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const filename = path.split("/").pop() ?? path;
  const cannotPreview = data.bytes > MAX_TABLE_BYTES || !!data.truncated;

  useEffect(() => {
    if (cannotPreview) return;
    let cancelled = false;
    setPreview(null);
    setError(null);
    void (async () => {
      try {
        const file = await fileContentToBlob(data).arrayBuffer();
        const metadata = await parquetMetadataAsync(file);
        const columns = parquetSchema(metadata).children.map((child) => child.element.name);
        const totalRows = metadata.num_rows;
        const objects = await parquetReadObjects({
          file,
          metadata,
          columns,
          rowEnd: Number(totalRows > BigInt(MAX_TABLE_ROWS) ? MAX_TABLE_ROWS : totalRows),
        });
        if (!cancelled) {
          setPreview({
            columns,
            rows: objects.map((row) => columns.map((column) => formatCell(row[column]))),
            totalRows,
          });
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cannotPreview, data]);

  if (cannotPreview) {
    return (
      <ParquetFallback
        filename={filename}
        detail={
          data.truncated
            ? "The server truncated this file before it reached the viewer."
            : "File too large to preview as a table."
        }
        conversationId={conversationId}
        path={path}
      />
    );
  }
  if (error) {
    return (
      <ParquetFallback
        filename={filename}
        detail={`Unable to parse Parquet: ${error}`}
        conversationId={conversationId}
        path={path}
      />
    );
  }
  if (!preview) {
    return (
      <div className="flex items-center justify-center p-8 text-muted-foreground text-sm">
        Reading Parquet metadata…
      </div>
    );
  }

  const hidden = preview.totalRows - BigInt(preview.rows.length);
  return (
    <div className="flex h-full flex-col">
      {hidden > 0n && (
        <div className="shrink-0 border-b border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
          Showing first {preview.rows.length.toLocaleString()} of {preview.totalRows.toString()}{" "}
          rows.
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-card">
            <tr>
              <th className="border-b border-border px-3 py-1 text-right font-medium text-muted-foreground">
                #
              </th>
              {preview.columns.map((column, index) => (
                <th
                  key={`${index}:${column}`}
                  className="border-b border-border px-3 py-1 text-left font-medium whitespace-nowrap"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="hover:bg-muted/40">
                <td className="border-b border-border/50 px-3 py-0.5 text-right text-muted-foreground/60 select-none">
                  {rowIndex + 1}
                </td>
                {preview.columns.map((column, columnIndex) => (
                  <td
                    key={`${columnIndex}:${column}`}
                    className="max-w-96 truncate border-b border-border/50 px-3 py-0.5 whitespace-nowrap"
                    title={row[columnIndex]}
                  >
                    {row[columnIndex]}
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

function ParquetFallback({
  filename,
  detail,
  conversationId,
  path,
}: {
  filename: string;
  detail: string;
  conversationId: string;
  path: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-sm">
      <div className="font-medium">{filename}</div>
      <div className="text-center text-muted-foreground">{detail}</div>
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

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date) return value.toISOString();
  if (value instanceof Uint8Array) {
    const shown = value.subarray(0, 32);
    const hex = Array.from(shown, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `0x${hex}${value.length > shown.length ? "…" : ""}`;
  }
  if (typeof value === "object") {
    return JSON.stringify(value, (_key, nested) =>
      typeof nested === "bigint" ? nested.toString() : nested,
    );
  }
  return String(value);
}
