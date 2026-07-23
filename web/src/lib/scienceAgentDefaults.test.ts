import { describe, expect, it } from "vitest";
import { automaticScienceHarness } from "./scienceAgentDefaults";

const LABELS = {
  "claude-sdk": "Claude SDK",
  codex: "Codex",
  cursor: "Cursor",
  pi: "Pi",
};

describe("automaticScienceHarness", () => {
  it("keeps the declared Codex runtime when it is ready", () => {
    expect(
      automaticScienceHarness("codex", LABELS, {
        codex: true,
        "claude-sdk": true,
      }),
    ).toBeNull();
  });

  it("falls back to the first verifiably ready runtime when Codex needs setup", () => {
    expect(
      automaticScienceHarness("codex", LABELS, {
        codex: "needs-auth",
        "claude-sdk": true,
        cursor: true,
        pi: true,
      }),
    ).toBe("cursor");
  });

  it("does not treat an SDK's optimistic readiness as proof of credentials", () => {
    expect(
      automaticScienceHarness("codex", LABELS, {
        codex: "needs-auth",
        "claude-sdk": true,
        cursor: false,
        pi: false,
      }),
    ).toBeNull();
  });

  it("does not guess when host readiness is unknown", () => {
    expect(automaticScienceHarness("codex", LABELS, null)).toBeNull();
    expect(automaticScienceHarness("codex", LABELS, { "claude-sdk": true })).toBeNull();
  });
});
