import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ available: true }));

vi.mock("@/lib/CapabilitiesContext", () => ({
  useServerInfo: () => ({ science_enabled: mocks.available }),
}));

vi.mock("@/components/science/SciencePanel", () => ({
  SciencePanel: ({ projectDir }: { projectDir: string | null }) => (
    <div data-testid="project-workspace">{projectDir}</div>
  ),
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => ({
    data: {
      pages: [
        {
          data: [
            {
              id: "conv_science",
              workspace: "/tmp/mapping-study",
            },
          ],
        },
      ],
    },
  }),
}));

import { ProjectPage } from "./ProjectPage";

describe("ProjectPage", () => {
  it("makes scientific provenance a top-level workspace bound to a session directory", () => {
    render(
      <MemoryRouter>
        <ProjectPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Provenance" })).toBeInTheDocument();
    expect(screen.getByTestId("project-workspace")).toHaveTextContent("/tmp/mapping-study");
    expect(screen.getByRole("link", { name: "Compute" })).toHaveAttribute(
      "href",
      "/settings/compute",
    );
    expect(screen.getByRole("link", { name: "Storage" })).toHaveAttribute(
      "href",
      "/settings/storage",
    );
  });
});
