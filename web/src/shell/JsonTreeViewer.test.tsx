import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { JsonTreeViewer } from "./JsonTreeViewer";
import { isJsonPath } from "./codeViewerHelpers";

afterEach(cleanup);

// ---------------------------------------------------------------------------
// isJsonPath — file-type detection
// ---------------------------------------------------------------------------

describe("isJsonPath", () => {
  it.each([
    ["config.json", true],
    ["DATA/RESULTS.JSON", true],
    ["data.csv", false],
    ["notes.md", false],
  ])("maps %s to %s", (path, expected) => {
    expect(isJsonPath(path)).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// JsonTreeViewer — tree rendering, collapsing, fallback, source toggle
// ---------------------------------------------------------------------------

function renderViewer(content: string) {
  return render(<JsonTreeViewer content={content} source={<div data-testid="source-stub" />} />);
}

describe("JsonTreeViewer", () => {
  it("renders object keys and primitive values", () => {
    const { container } = renderViewer('{"name": "alpha", "count": 3, "ok": true, "extra": null}');
    const text = container.textContent ?? "";
    expect(text).toContain('"name":');
    expect(text).toContain('"alpha"');
    expect(text).toContain("3");
    expect(text).toContain("true");
    expect(text).toContain("null");
  });

  it("collapses nodes below the default depth and expands them on click", () => {
    // "nested" is at depth 1 (expanded by default); "deep" at depth 2 starts collapsed.
    renderViewer('{"nested": {"deep": {"leaf": 1}}}');
    expect(screen.queryByText("1")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /"deep"/ }));
    expect(screen.getByText("1")).toBeTruthy();
  });

  it("collapses an expanded subtree on click, hiding its children", () => {
    renderViewer('{"a": 1, "b": 2}');
    expect(screen.getByText("1")).toBeTruthy();

    // The root toggle is the only button without a key label.
    const rootToggle = screen.getAllByRole("button").find((b) => b.textContent?.includes("{"));
    expect(rootToggle).toBeDefined();
    fireEvent.click(rootToggle!);
    expect(screen.queryByText("1")).toBeNull();
  });

  it("renders arrays with numeric indices", () => {
    const { container } = renderViewer('{"items": [10, 20]}');
    const text = container.textContent ?? "";
    expect(text).toContain('"items":');
    expect(text).toContain("10");
    expect(text).toContain("20");
  });

  it("falls back to the source view with a notice for invalid JSON", () => {
    renderViewer("{not valid json");
    expect(screen.getByText(/Invalid JSON/)).toBeTruthy();
    expect(screen.getByTestId("source-stub")).toBeTruthy();
  });

  it("toggles between the tree and the source view", () => {
    renderViewer('{"a": 1}');
    expect(screen.queryByTestId("source-stub")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(screen.getByTestId("source-stub")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Tree" }));
    expect(screen.queryByTestId("source-stub")).toBeNull();
  });
});
