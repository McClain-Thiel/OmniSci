import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useCancelScienceRun,
  useScienceRunLogs,
  useScienceRunOutputs,
  useScienceRuns,
} from "@/hooks/useScience";
import { ScienceRunsTab } from "./ScienceRunsTab";

vi.mock("@/hooks/useScience", () => ({
  useScienceRuns: vi.fn(),
  useScienceRunLogs: vi.fn(),
  useScienceRunOutputs: vi.fn(),
  useCancelScienceRun: vi.fn(),
}));

const mockRuns = vi.mocked(useScienceRuns);
const mockLogs = vi.mocked(useScienceRunLogs);
const mockOutputs = vi.mocked(useScienceRunOutputs);
const mockCancel = vi.mocked(useCancelScienceRun);

beforeEach(() => {
  mockRuns.mockReturnValue({
    isPending: false,
    isError: false,
    data: [
      {
        id: "run_qsub",
        provider: "qsub",
        status: "succeeded",
        command: ["python3", "analysis.py"],
        queuedAt: "2026-07-23T10:00:00Z",
        startedAt: "2026-07-23T10:00:01Z",
        finishedAt: "2026-07-23T10:00:02Z",
        exitCode: 0,
        costUsd: null,
        outputArtifactIds: ["art_figure"],
        createdAt: "2026-07-23T10:00:00Z",
      },
    ],
  } as ReturnType<typeof useScienceRuns>);
  mockLogs.mockReturnValue({
    isPending: false,
    isError: false,
    data: { stdout: "done\n", stderr: "", cursor: null },
  } as ReturnType<typeof useScienceRunLogs>);
  mockOutputs.mockReturnValue({
    isPending: false,
    isError: false,
    data: [
      {
        id: "art_figure",
        path: "figures/response.svg",
        uri: "file:///tmp/project/figures/response.svg",
        type: "figure",
        mime: "image/svg+xml",
        sizeBytes: 512,
        runId: "run_qsub",
        taskId: null,
        createdAt: "2026-07-23T10:00:02Z",
      },
    ],
  } as ReturnType<typeof useScienceRunOutputs>);
  mockCancel.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCancelScienceRun>);
});

describe("ScienceRunsTab", () => {
  it("opens collected artifacts in the session file viewer", () => {
    const onOpenFile = vi.fn();
    render(
      <MemoryRouter>
        <ScienceRunsTab
          project="/tmp/project"
          conversationId="session_123"
          onOpenFile={onOpenFile}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Toggle details for run run_qsub" }));

    const figureLink = screen.getByRole("link", { name: "figures/response.svg" });
    expect(figureLink).toHaveAttribute("href", "/c/session_123?file=figures%2Fresponse.svg");
    fireEvent.click(figureLink);
    expect(onOpenFile).toHaveBeenCalledWith("figures/response.svg");
  });
});
