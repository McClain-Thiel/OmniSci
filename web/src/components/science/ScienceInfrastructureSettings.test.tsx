import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ScienceInfrastructureStatus, ScienceToolsStatus } from "@/lib/scienceApi";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  tools: null as ScienceToolsStatus | null,
  infrastructure: null as ScienceInfrastructureStatus | null,
}));

vi.mock("@/hooks/useScience", () => ({
  useScienceTools: () => ({
    data: mocks.tools,
    isLoading: false,
    error: null,
  }),
  useScienceInfrastructure: () => ({
    data: mocks.infrastructure,
    isLoading: false,
    error: null,
  }),
  useUpdateScienceInfrastructure: () => ({
    mutate: mocks.mutate,
    isPending: false,
    isSuccess: false,
    error: null,
  }),
}));

import { ComputeSettings, StorageSettings, ToolsSettings } from "./ScienceInfrastructureSettings";

const infrastructure: ScienceInfrastructureStatus = {
  scope: "app",
  configPath: "/Users/researcher/.omnisci/infrastructure.yaml",
  computeConfig: {
    defaultProvider: "modal",
    providers: {
      modal: {
        app_name: "mapping-study",
        default_image: "python:3.12-slim",
        max_runtime_minutes: 90,
        collection_grace_seconds: 900,
      },
      ssh: {
        host: "compute.example.edu",
        user: "researcher",
        port: 22,
        identity_file: "/Users/researcher/.ssh/id_ed25519",
        known_hosts_file: "/Users/researcher/.ssh/known_hosts",
        remote_root: "/tmp/omnisci",
        max_runtime_minutes: 60,
        cleanup_remote: true,
      },
    },
  },
  storageConfig: {
    defaultProvider: "s3",
    allowedRoots: ["data", "results"],
    providers: {
      s3: {
        allowed_buckets: ["research-data"],
        allowed_prefixes: ["research-data/project-a/"],
        allow_write: false,
        region_name: "eu-west-2",
      },
    },
  },
  compute: [
    {
      id: "local",
      registered: true,
      dependencyAvailable: true,
      configured: true,
      default: false,
    },
    {
      id: "modal",
      registered: true,
      dependencyAvailable: true,
      configured: true,
      default: true,
    },
    {
      id: "ssh",
      registered: true,
      dependencyAvailable: true,
      configured: true,
      default: false,
    },
    {
      id: "slurm",
      registered: true,
      dependencyAvailable: true,
      configured: false,
      default: false,
    },
    {
      id: "qsub",
      registered: true,
      dependencyAvailable: true,
      configured: false,
      default: false,
    },
  ],
  storage: [
    {
      id: "local",
      registered: true,
      dependencyAvailable: true,
      configured: true,
      default: false,
    },
    {
      id: "s3",
      registered: true,
      dependencyAvailable: true,
      configured: true,
      default: true,
    },
  ],
};

const tools: ScienceToolsStatus = {
  ok: true,
  scope: "app",
  computeProviders: ["local", "modal", "ssh"],
  storageSchemes: ["file", "s3"],
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

beforeEach(() => {
  localStorage.setItem(
    "omnigent:science-preferences",
    JSON.stringify({ projectDir: "/tmp/mapping" }),
  );
  mocks.tools = tools;
  mocks.infrastructure = infrastructure;
  mocks.mutate.mockReset();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("science infrastructure settings", () => {
  it("loads and saves the configured Modal provider", () => {
    render(<ComputeSettings />);

    expect(screen.getAllByText("Modal Sandbox").length).toBeGreaterThan(0);
    expect(screen.getByDisplayValue("mapping-study")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Modal maximum runtime (minutes)"), {
      target: { value: "120" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save compute configuration" }));

    expect(mocks.mutate).toHaveBeenCalledWith({
      computeConfig: expect.objectContaining({
        defaultProvider: "modal",
        providers: expect.objectContaining({
          modal: expect.objectContaining({ max_runtime_minutes: 120 }),
        }),
      }),
    });
  });

  it("saves SSH host configuration without private-key contents", () => {
    render(<ComputeSettings />);

    fireEvent.change(screen.getByLabelText("SSH host"), {
      target: { value: "ec2.example.net" },
    });
    fireEvent.change(screen.getByLabelText("SSH maximum runtime (minutes)"), {
      target: { value: "45" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save compute configuration" }));

    const payload = mocks.mutate.mock.calls[0][0];
    expect(payload.computeConfig.providers.ssh).toMatchObject({
      host: "ec2.example.net",
      user: "researcher",
      max_runtime_minutes: 45,
      identity_file: "/Users/researcher/.ssh/id_ed25519",
      known_hosts_file: "/Users/researcher/.ssh/known_hosts",
    });
    expect(JSON.stringify(payload)).not.toMatch(/BEGIN .*PRIVATE KEY/);
  });

  it("configures Slurm and qsub over the shared SSH transport", () => {
    render(<ComputeSettings />);

    fireEvent.click(screen.getByLabelText("Enable Slurm compute"));
    fireEvent.change(screen.getByLabelText(/Partition/), { target: { value: "gpu" } });
    fireEvent.click(screen.getByLabelText("Enable qsub compute"));
    fireEvent.change(screen.getByLabelText(/Queue/), { target: { value: "workq" } });
    fireEvent.click(screen.getByRole("button", { name: "Save compute configuration" }));

    const providers = mocks.mutate.mock.calls[0][0].computeConfig.providers;
    expect(providers.slurm).toMatchObject({ transport_ref: "ssh", partition: "gpu" });
    expect(providers.qsub).toMatchObject({
      transport_ref: "ssh",
      dialect: "pbs",
      queue: "workq",
      gpu_resource: "ngpus",
    });
    expect(providers.slurm.host).toBeUndefined();
    expect(providers.qsub.identity_file).toBeUndefined();
  });

  it("saves S3 policy without credential fields", () => {
    render(<StorageSettings />);

    expect(screen.getByDisplayValue("research-data")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Allow S3 writes"));
    fireEvent.click(screen.getByRole("button", { name: "Save storage configuration" }));

    const payload = mocks.mutate.mock.calls[0][0];
    expect(payload.storageConfig).toMatchObject({
      defaultProvider: "s3",
      allowedRoots: ["data", "results"],
      providers: {
        s3: expect.objectContaining({
          allowed_buckets: ["research-data"],
          allow_write: true,
        }),
      },
    });
    expect(JSON.stringify(payload)).not.toMatch(/secret|token|access_key/);
  });

  it("shows server-detected CLI readiness", () => {
    render(<ToolsSettings />);

    expect(screen.getByText("local, modal, ssh")).toBeInTheDocument();
    expect(screen.getByText("/usr/bin/python")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();
  });
});
