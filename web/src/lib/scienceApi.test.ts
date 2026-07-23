import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authenticatedFetch: vi.fn(),
}));

vi.mock("./identity", () => ({
  authenticatedFetch: mocks.authenticatedFetch,
}));

import {
  getScienceInfrastructure,
  getScienceTools,
  listScienceIssues,
  updateScienceIssue,
  updateScienceInfrastructure,
} from "./scienceApi";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  } as Response;
}

const toolsWire = {
  ok: true,
  scope: "app",
  compute_providers: ["local", "modal"],
  storage_schemes: ["file"],
  tools: [
    {
      id: "python",
      kind: "cli",
      command: "python",
      description: "Python interpreter",
      installed: true,
      enabled: true,
      available: true,
      path: "/usr/bin/python",
    },
  ],
};

const infrastructureWire = {
  scope: "app",
  config_path: "/Users/researcher/.omnisci/infrastructure.yaml",
  compute_config: {
    default_provider: "modal",
    providers: { modal: { app_name: "mapping-study" } },
  },
  storage_config: {
    default_provider: "local",
    allowed_roots: ["data", "results"],
    providers: {},
  },
  compute: [
    {
      id: "modal",
      registered: true,
      dependency_available: true,
      configured: true,
      default: true,
    },
  ],
  storage: [
    {
      id: "local",
      registered: true,
      dependency_available: true,
      configured: true,
      default: true,
    },
  ],
};

beforeEach(() => {
  mocks.authenticatedFetch.mockReset();
});

describe("science infrastructure API", () => {
  it("loads app tool readiness without a project query", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(jsonResponse(toolsWire));

    const status = await getScienceTools();

    expect(mocks.authenticatedFetch).toHaveBeenCalledWith("/v1/science/tools");
    expect(status.computeProviders).toEqual(["local", "modal"]);
    expect(status.tools[0]).toMatchObject({ command: "python", available: true });
  });

  it("loads and updates app-level infrastructure without a project query", async () => {
    mocks.authenticatedFetch
      .mockResolvedValueOnce(jsonResponse(infrastructureWire))
      .mockResolvedValueOnce(jsonResponse(infrastructureWire));

    const status = await getScienceInfrastructure();
    await updateScienceInfrastructure({
      computeConfig: {
        defaultProvider: "modal",
        providers: { modal: { app_name: "mapping-study" } },
      },
    });

    expect(mocks.authenticatedFetch.mock.calls[0][0]).toBe("/v1/science/infrastructure");
    expect(status.configPath).toContain(".omnisci/infrastructure.yaml");
    const [url, init] = mocks.authenticatedFetch.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/v1/science/infrastructure");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toMatchObject({
      compute_config: {
        default_provider: "modal",
        providers: { modal: { app_name: "mapping-study" } },
      },
    });
  });
});

describe("science reviewer issue API", () => {
  const issueWire = {
    id: "issue_1",
    session_id: "session_1",
    task_id: null,
    research_log_id: "log_1",
    category: "statistics",
    severity: "major",
    status: "open",
    title: "Unclear experimental unit",
    description: "Replicates may not be independent.",
    evidence_refs: ["results/model.json"],
    verification_question: "How many independent samples were used?",
    confidence: 0.8,
    raised_by: "reviewer",
    resolution: null,
    resolved_by: null,
    created_at: "2026-07-23T10:00:00Z",
    updated_at: "2026-07-23T10:00:00Z",
  };

  it("maps and filters issue checklist records", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(jsonResponse([issueWire]));

    const issues = await listScienceIssues("/tmp/project", "open", "session_1");

    expect(mocks.authenticatedFetch).toHaveBeenCalledWith(
      "/v1/science/issues?project=%2Ftmp%2Fproject&status=open&session_id=session_1",
    );
    expect(issues[0]).toMatchObject({
      researchLogId: "log_1",
      verificationQuestion: "How many independent samples were used?",
      evidenceRefs: ["results/model.json"],
    });
  });

  it("records an explicit resolution note", async () => {
    mocks.authenticatedFetch.mockResolvedValueOnce(
      jsonResponse({
        ...issueWire,
        status: "resolved",
        resolution: "Confirmed three biological samples.",
        resolved_by: "web-user",
      }),
    );

    const issue = await updateScienceIssue(
      "/tmp/project",
      "issue_1",
      "resolved",
      "Confirmed three biological samples.",
    );

    const [url, init] = mocks.authenticatedFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/science/issues/issue_1?project=%2Ftmp%2Fproject");
    expect(JSON.parse(String(init.body))).toEqual({
      status: "resolved",
      resolution: "Confirmed three biological samples.",
      resolved_by: "web-user",
    });
    expect(issue).toMatchObject({
      status: "resolved",
      resolution: "Confirmed three biological samples.",
    });
  });
});
