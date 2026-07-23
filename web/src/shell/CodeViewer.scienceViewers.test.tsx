// Dispatch tests: CodeViewer routes .csv/.tsv to the table viewer, .json to
// the tree viewer, and .parquet to the local table preview — mirroring the
// existing image/PDF routing tests in CodeViewer.test.tsx.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { useFileContent } from "@/hooks/useFileContent";
import { CodeViewer } from "./CodeViewer";

vi.mock("@/hooks/usePermissions", () => ({ useCanEdit: vi.fn() }));
vi.mock("@/components/ai-elements/code-block", () => ({
  highlightCode: vi.fn(() => null),
  CodeBlockContent: ({ code }: { code: string }) => <pre>{code}</pre>,
}));
vi.mock("./MarkdownRichTextViewer", () => ({ MarkdownRichTextViewer: () => null }));
// Stub the lazy Monaco editor; its testid is the signal that a viewer's
// "Source" toggle routed to the editor.
vi.mock("./MonacoCodeEditor", () => ({
  MonacoCodeEditor: () => <div data-testid="monaco-editor-stub" />,
}));
vi.mock("./PdfViewer", () => ({
  PdfViewer: () => <div data-testid="pdf-viewer-stub" />,
}));
vi.mock("./ParquetTableViewer", () => ({
  ParquetTableViewer: () => <div data-testid="parquet-viewer-stub" />,
}));

import * as permissions from "@/hooks/usePermissions";

function makeFileQuery(content: string): ReturnType<typeof useFileContent> {
  return {
    data: { content, encoding: "utf-8", bytes: content.length, truncated: false },
    isLoading: false,
    isError: false,
    isSuccess: true,
    error: null,
  } as unknown as ReturnType<typeof useFileContent>;
}

const noopRef = { current: null };

function renderViewer(content: string, path: string) {
  return render(
    <CodeViewer
      conversationId="conv_1"
      path={path}
      fileQuery={makeFileQuery(content)}
      comments={[]}
      activeSelection={null}
      onSetActiveSelection={() => {}}
      panelOpen={true}
      searchOpen={false}
      setSearchOpen={() => {}}
      searchInputRef={noopRef}
      viewMode="source"
    />,
  );
}

beforeEach(() => {
  vi.mocked(permissions.useCanEdit).mockReturnValue(true);
});

afterEach(cleanup);

describe("CodeViewer science viewer dispatch", () => {
  it("routes .csv files to the table viewer", () => {
    renderViewer("name,value\nalpha,1\n", "data/results.csv");
    expect(screen.getByRole("button", { name: "Table" })).toBeTruthy();
    expect(screen.getByText("alpha")).toBeTruthy();
  });

  it("routes .tsv files to the table viewer", () => {
    renderViewer("name\tvalue\nalpha\t1\n", "data/results.tsv");
    expect(screen.getByRole("button", { name: "Table" })).toBeTruthy();
    expect(screen.getByText("alpha")).toBeTruthy();
  });

  it("table viewer's Source toggle reaches the Monaco editor", async () => {
    renderViewer("name,value\nalpha,1\n", "data/results.csv");
    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(await screen.findByTestId("monaco-editor-stub")).toBeTruthy();
  });

  it("routes .json files to the tree viewer", () => {
    renderViewer('{"a": 1}', "config.json");
    expect(screen.getByRole("button", { name: "Tree" })).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
  });

  it("tree viewer's Source toggle reaches the Monaco editor", async () => {
    renderViewer('{"a": 1}', "config.json");
    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(await screen.findByTestId("monaco-editor-stub")).toBeTruthy();
  });

  it("routes .parquet files to the table viewer", async () => {
    renderViewer("PAR1....", "data/results.parquet");
    expect(await screen.findByTestId("parquet-viewer-stub")).toBeTruthy();
  });
});
