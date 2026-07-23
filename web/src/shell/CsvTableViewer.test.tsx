import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FileContentResponse } from "@/hooks/useFileContent";
import { CsvTableViewer, MAX_TABLE_BYTES, MAX_TABLE_ROWS, parseDelimited } from "./CsvTableViewer";
import { csvDelimiterForPath } from "./codeViewerHelpers";

vi.mock("@/hooks/useFileContent", () => ({
  downloadWorkspaceFile: vi.fn(),
}));

import { downloadWorkspaceFile } from "@/hooks/useFileContent";

afterEach(cleanup);

// ---------------------------------------------------------------------------
// csvDelimiterForPath — file-type detection
// ---------------------------------------------------------------------------

describe("csvDelimiterForPath", () => {
  it.each([
    ["data/results.csv", ","],
    ["DATA.CSV", ","],
    ["data/results.tsv", "\t"],
    ["notes.md", null],
    ["config.json", null],
    ["no-extension", null],
  ])("maps %s to %j", (path, expected) => {
    expect(csvDelimiterForPath(path)).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// parseDelimited — RFC-4180-ish parsing
// ---------------------------------------------------------------------------

describe("parseDelimited", () => {
  it("parses simple rows", () => {
    expect(parseDelimited("a,b,c\n1,2,3\n", ",")).toEqual([
      ["a", "b", "c"],
      ["1", "2", "3"],
    ]);
  });

  it("parses TSV with a tab delimiter", () => {
    expect(parseDelimited("a\tb\n1\t2", "\t")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("handles quoted fields with embedded delimiters, quotes, and newlines", () => {
    expect(parseDelimited('a,b\n"x,y","he said ""hi"""\n"p\nq",z', ",")).toEqual([
      ["a", "b"],
      ["x,y", 'he said "hi"'],
      ["p\nq", "z"],
    ]);
  });

  it("handles CRLF line endings", () => {
    expect(parseDelimited("a,b\r\n1,2\r\n", ",")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("skips a UTF-8 BOM", () => {
    expect(parseDelimited("﻿a,b", ",")).toEqual([["a", "b"]]);
  });

  it("returns no rows for empty input and keeps a single unterminated line", () => {
    expect(parseDelimited("", ",")).toEqual([]);
    expect(parseDelimited("a,b", ",")).toEqual([["a", "b"]]);
  });
});

// ---------------------------------------------------------------------------
// CsvTableViewer — rendering, row cap, large-file fallback, source toggle
// ---------------------------------------------------------------------------

function makeData(content: string, bytes?: number, truncated = false): FileContentResponse {
  return {
    object: "session.environment.filesystem.file_content",
    path: "data/results.csv",
    content_type: "text/csv",
    encoding: "utf-8",
    content,
    bytes: bytes ?? content.length,
    truncated,
  };
}

function renderViewer(data: FileContentResponse, path = "data/results.csv") {
  return render(
    <CsvTableViewer
      data={data}
      path={path}
      conversationId="conv_1"
      source={<div data-testid="source-stub" />}
    />,
  );
}

describe("CsvTableViewer", () => {
  it("renders the header row and data rows as a table", () => {
    const { container } = renderViewer(makeData("name,value\nalpha,1\nbeta,2\n"));
    const headers = Array.from(container.querySelectorAll("thead th")).map((th) => th.textContent);
    expect(headers).toEqual(["#", "name", "value"]);
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("beta")).toBeTruthy();
  });

  it(`caps the table at ${MAX_TABLE_ROWS} rows with a notice`, () => {
    const body = Array.from({ length: MAX_TABLE_ROWS + 5 }, (_, i) => `r${i},${i}`).join("\n");
    renderViewer(makeData(`h1,h2\n${body}\n`));
    expect(
      screen.getByText(
        `Showing first ${MAX_TABLE_ROWS.toLocaleString()} of ${(MAX_TABLE_ROWS + 5).toLocaleString()} rows.`,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(`r${MAX_TABLE_ROWS}`)).toBeNull();
  });

  it("notes when the server truncated the file", () => {
    renderViewer(makeData("a,b\n1,2\n", undefined, true));
    expect(screen.getByText(/truncated by the server/)).toBeTruthy();
  });

  it("falls back to metadata + download for very large files", () => {
    renderViewer(makeData("a,b\n1,2\n", MAX_TABLE_BYTES + 1));
    expect(screen.getByText(/too large to preview as a table/)).toBeTruthy();
    expect(screen.getByText(/5\.0 MB/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /download/i }));
    expect(downloadWorkspaceFile).toHaveBeenCalledWith("conv_1", "data/results.csv");
  });

  it("toggles between the table and the source view", () => {
    renderViewer(makeData("a,b\n1,2\n"));
    expect(screen.queryByTestId("source-stub")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(screen.getByTestId("source-stub")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.queryByTestId("source-stub")).toBeNull();
  });
});
