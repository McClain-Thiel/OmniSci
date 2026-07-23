export const SCIENCE_AGENT_NAME = "science";

// The host reports in-process SDKs as ready because it cannot inspect their
// ambient credentials. Never auto-route into one on that optimistic signal.
const UNVERIFIABLE_AUTOMATIC_HARNESSES = new Set(["claude-sdk", "antigravity"]);

/**
 * Pick a ready harness override for the built-in Science agent.
 *
 * The bundle declares Codex so a ChatGPT/Codex subscription works on a fresh
 * install. When that harness is explicitly unavailable on the selected host,
 * use the first verifiably ready harness in the UI catalog. SDKs whose ambient
 * credentials cannot be inspected remain explicit choices. Unknown readiness
 * never overrides the bundle: the runner remains the authority at launch time.
 */
export function automaticScienceHarness(
  declaredHarness: string | null | undefined,
  harnessLabels: Record<string, string>,
  readiness: Record<string, boolean | string> | null | undefined,
): string | null {
  if (!declaredHarness || !readiness) return null;
  if (readiness[declaredHarness] === true) return null;
  if (!(declaredHarness in readiness)) return null;

  const fallback = Object.keys(harnessLabels).find(
    (harness) =>
      harness !== declaredHarness &&
      !UNVERIFIABLE_AUTOMATIC_HARNESSES.has(harness) &&
      readiness[harness] === true,
  );
  return fallback ?? null;
}
