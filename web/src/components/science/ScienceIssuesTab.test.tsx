import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useScienceIssues, useScienceReviews, useUpdateScienceIssue } from "@/hooks/useScience";
import { ScienceIssuesTab } from "./ScienceIssuesTab";

vi.mock("@/hooks/useScience", () => ({
  useScienceIssues: vi.fn(),
  useScienceReviews: vi.fn(),
  useUpdateScienceIssue: vi.fn(),
}));

const mockIssues = vi.mocked(useScienceIssues);
const mockReviews = vi.mocked(useScienceReviews);
const mockUpdate = vi.mocked(useUpdateScienceIssue);
const mutate = vi.fn();

beforeEach(() => {
  mutate.mockReset();
  mockIssues.mockReturnValue({
    isPending: false,
    isError: false,
    data: [
      {
        id: "issue_1",
        sessionId: "session_1",
        taskId: null,
        researchLogId: "log_1",
        category: "statistics",
        severity: "major",
        status: "open",
        title: "Unclear experimental unit",
        description: "Replicates may be repeated measurements from one sample.",
        evidenceRefs: ["results/model.json"],
        verificationQuestion: "How many independent biological samples were used?",
        confidence: 0.8,
        raisedBy: "reviewer",
        resolution: null,
        resolvedBy: null,
        createdAt: "2026-07-23T10:00:00Z",
        updatedAt: "2026-07-23T10:00:00Z",
      },
    ],
  } as ReturnType<typeof useScienceIssues>);
  mockReviews.mockReturnValue({
    isPending: false,
    isError: false,
    data: [
      {
        id: "review_1",
        sessionId: "session_1",
        reviewerAgent: "reviewer",
        reviewerSession: "session_review",
        reviewerModel: "gpt-5",
        reviewerHarness: "codex",
        reviewedThrough: "log_1",
        summary: "One statistical assumption needs checking.",
        issueIds: ["issue_1"],
        createdAt: "2026-07-23T10:01:00Z",
      },
    ],
  } as ReturnType<typeof useScienceReviews>);
  mockUpdate.mockReturnValue({
    mutate,
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateScienceIssue>);
});

describe("ScienceIssuesTab", () => {
  it("shows advisory review context and resolves an issue without gating work", () => {
    render(<ScienceIssuesTab project="/tmp/project" />);

    expect(screen.getByText("One statistical assumption needs checking.")).toBeInTheDocument();
    expect(screen.getByText("Unclear experimental unit")).toBeInTheDocument();
    expect(
      screen.getByText("Check: How many independent biological samples were used?"),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Resolve issue: Unclear experimental unit" }),
    );
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.change(
      screen.getByRole("textbox", { name: "Resolution for issue: Unclear experimental unit" }),
      { target: { value: "Confirmed three independent biological samples." } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(mutate).toHaveBeenCalledWith({
      issueId: "issue_1",
      status: "resolved",
      resolution: "Confirmed three independent biological samples.",
    });
  });
});
