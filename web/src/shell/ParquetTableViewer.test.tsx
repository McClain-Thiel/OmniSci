import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FileContentResponse } from "@/hooks/useFileContent";
import { MAX_TABLE_BYTES } from "./CsvTableViewer";
import { ParquetTableViewer } from "./ParquetTableViewer";

const { parquetMetadataAsync, parquetReadObjects, parquetSchema } = vi.hoisted(() => ({
  parquetMetadataAsync: vi.fn(),
  parquetReadObjects: vi.fn(),
  parquetSchema: vi.fn(),
}));

vi.mock("hyparquet", () => ({
  parquetMetadataAsync,
  parquetReadObjects,
  parquetSchema,
}));

function response(bytes = 12): FileContentResponse {
  return {
    object: "session.environment.filesystem.file_content",
    path: "results/table.parquet",
    content_type: "application/vnd.apache.parquet",
    encoding: "base64",
    content: btoa("PAR1payload"),
    bytes,
    truncated: false,
  };
}

beforeEach(() => {
  parquetMetadataAsync.mockReset().mockResolvedValue({ num_rows: 2n });
  parquetSchema.mockReset().mockReturnValue({
    children: [{ element: { name: "sample" } }, { element: { name: "count" } }],
  });
  parquetReadObjects.mockReset().mockResolvedValue([
    { sample: "control", count: 3n },
    { sample: "treated", count: 5n },
  ]);
});

describe("ParquetTableViewer", () => {
  it("parses a small file locally and renders rows", async () => {
    render(
      <ParquetTableViewer data={response()} path="results/table.parquet" conversationId="c1" />,
    );

    expect(await screen.findByText("control")).toBeTruthy();
    expect(screen.getByText("treated")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(parquetReadObjects).toHaveBeenCalledWith(
      expect.objectContaining({ columns: ["sample", "count"], rowEnd: 2 }),
    );
  });

  it("does not parse files above the preview limit", async () => {
    render(
      <ParquetTableViewer
        data={response(MAX_TABLE_BYTES + 1)}
        path="results/large.parquet"
        conversationId="c1"
      />,
    );

    expect(screen.getByText("File too large to preview as a table.")).toBeTruthy();
    await waitFor(() => expect(parquetMetadataAsync).not.toHaveBeenCalled());
  });
});
