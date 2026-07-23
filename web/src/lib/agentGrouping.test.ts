import { describe, expect, it } from "vitest";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { partitionAgentsByKind, sortAgentsForDisplay } from "./agentGrouping";

function agent(name: string, displayName: string, harness: string): AvailableAgent {
  return {
    id: `ag_${name}`,
    name,
    display_name: displayName,
    description: null,
    harness,
    skills: [],
  };
}

describe("agent grouping", () => {
  it("puts Science first so it is the first-run default", () => {
    const sorted = sortAgentsForDisplay([
      agent("claude-native-ui", "Claude Code", "claude-native"),
      agent("science", "Science", "codex"),
      agent("codex-native-ui", "Codex", "codex-native"),
    ]);

    expect(sorted.map((entry) => entry.name)).toEqual([
      "science",
      "claude-native-ui",
      "codex-native-ui",
    ]);
  });

  it("groups Science with shipped built-ins", () => {
    const grouped = partitionAgentsByKind([
      agent("science", "Science", "codex"),
      agent("my-agent", "My agent", "claude-sdk"),
    ]);

    expect(grouped.builtins.map((entry) => entry.name)).toEqual(["science"]);
    expect(grouped.customs.map((entry) => entry.name)).toEqual(["my-agent"]);
  });
});
