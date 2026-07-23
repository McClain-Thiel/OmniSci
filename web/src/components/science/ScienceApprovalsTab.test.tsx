import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useResolveScienceApproval,
  useRevokeScienceApproval,
  useScienceApprovals,
} from "@/hooks/useScience";
import { ScienceApprovalsTab } from "./ScienceApprovalsTab";

vi.mock("@/hooks/useScience", () => ({
  useScienceApprovals: vi.fn(),
  useResolveScienceApproval: vi.fn(),
  useRevokeScienceApproval: vi.fn(),
}));

const mockApprovals = vi.mocked(useScienceApprovals);
const mockResolve = vi.mocked(useResolveScienceApproval);
const mockRevoke = vi.mocked(useRevokeScienceApproval);
const mutate = vi.fn();
const revoke = vi.fn();

beforeEach(() => {
  mutate.mockReset();
  revoke.mockReset();
  mockApprovals.mockReturnValue({
    isPending: false,
    isError: false,
    data: [
      {
        id: "approval_1",
        action: "storage.write:s3",
        scope: "s3://science/results/",
        requestingSession: null,
        requestingAgent: "coordinator",
        decision: "pending",
        actor: null,
        decidedAt: null,
        reason: "Dataset license is unclear",
        expiresAt: null,
        scopeKind: "one_time",
        createdAt: "2026-07-22T10:00:00Z",
      },
    ],
  } as ReturnType<typeof useScienceApprovals>);
  mockResolve.mockReturnValue({
    mutate,
    isPending: false,
    isError: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useResolveScienceApproval>);
  mockRevoke.mockReturnValue({
    mutate: revoke,
    isPending: false,
    isError: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useRevokeScienceApproval>);
});

describe("ScienceApprovalsTab", () => {
  it.each([
    ["Allow once", "approved", "one_time"],
    ["Allow prefix", "approved", "prefix"],
    ["Allow project", "approved", "project"],
    ["Deny", "denied", "one_time"],
  ] as const)("resolves pending approval with %s", (label, decision, scopeKind) => {
    render(<ScienceApprovalsTab project="/tmp/project" />);

    fireEvent.click(screen.getByRole("button", { name: label }));

    expect(mutate).toHaveBeenCalledWith({ approvalId: "approval_1", decision, scopeKind });
  });

  it("revokes an approved reusable permission", () => {
    mockApprovals.mockReturnValue({
      isPending: false,
      isError: false,
      data: [
        {
          id: "approval_reusable",
          action: "storage.write:s3",
          scope: "s3://science/results/",
          requestingSession: null,
          requestingAgent: "coordinator",
          decision: "approved",
          actor: "web-user",
          decidedAt: "2026-07-22T10:01:00Z",
          reason: null,
          expiresAt: null,
          consumedAt: null,
          revokedAt: null,
          revokedBy: null,
          revocationReason: null,
          scopeKind: "prefix",
          createdAt: "2026-07-22T10:00:00Z",
        },
      ],
    } as ReturnType<typeof useScienceApprovals>);
    render(<ScienceApprovalsTab project="/tmp/project" />);

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));

    expect(revoke).toHaveBeenCalledWith("approval_reusable");
  });
});
