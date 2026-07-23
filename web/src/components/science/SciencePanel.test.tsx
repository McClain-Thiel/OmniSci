import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import {
  useScienceArtifacts,
  useScienceProjectStatus,
  useScienceResearchLog,
} from "@/hooks/useScience";
import { SciencePanel } from "./SciencePanel";

vi.mock("@/hooks/useScience", () => ({
  useScienceArtifacts: vi.fn(),
  useScienceProjectStatus: vi.fn(),
  useScienceResearchLog: vi.fn(),
}));

vi.mock("./SciencePlanTab", () => ({ SciencePlanTab: () => null }));
vi.mock("./ScienceIssuesTab", () => ({ ScienceIssuesTab: () => null }));
vi.mock("./ScienceRunsTab", () => ({ ScienceRunsTab: () => null }));
vi.mock("./ScienceApprovalsTab", () => ({ ScienceApprovalsTab: () => null }));
vi.mock("./ScienceSkillsTab", () => ({ ScienceSkillsTab: () => null }));

describe("SciencePanel", () => {
  it("uses the session workspace and opens registered files in its viewer", () => {
    const onOpenFile = vi.fn();
    vi.mocked(useScienceProjectStatus).mockReturnValue({
      isPending: false,
      isError: false,
      data: {
        project: { name: "mapping-study", directory: "/tmp/mapping-study" },
        counts: { tasks: 1, tasksByStatus: { done: 1 }, runs: 2, openIssues: 0 },
      },
    } as unknown as ReturnType<typeof useScienceProjectStatus>);
    vi.mocked(useScienceResearchLog).mockReturnValue({
      isPending: false,
      isError: false,
      data: [],
    } as unknown as ReturnType<typeof useScienceResearchLog>);
    vi.mocked(useScienceArtifacts).mockReturnValue({
      isPending: false,
      isError: false,
      data: [
        {
          id: "art_figure",
          path: "figures/response.svg",
          uri: "file:///tmp/mapping-study/figures/response.svg",
          type: "figure",
          mime: "image/svg+xml",
          sizeBytes: 512,
          runId: null,
          taskId: null,
          createdAt: "2026-07-23T10:00:02Z",
        },
      ],
    } as unknown as ReturnType<typeof useScienceArtifacts>);

    render(
      <MemoryRouter>
        <SciencePanel
          conversationId="session_123"
          projectDir="/tmp/mapping-study"
          onOpenFile={onOpenFile}
        />
      </MemoryRouter>,
    );

    expect(screen.getByTitle("/tmp/mapping-study")).toHaveTextContent("mapping-study");
    const figureLink = screen.getByRole("link", { name: /figures\/response\.svg/ });
    expect(figureLink).toHaveAttribute("href", "/c/session_123?file=figures%2Fresponse.svg");
    fireEvent.click(figureLink);
    expect(onOpenFile).toHaveBeenCalledWith("figures/response.svg");
  });
});
