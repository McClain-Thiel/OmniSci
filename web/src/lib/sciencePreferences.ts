// Persisted, app-global preference for the active science project.
//
// The science panel addresses every `/v1/science` endpoint by an absolute
// folder path. The v1 server keeps no project registry, so the client remembers
// the directory the user last opened. This is a *preference*, not
// per-conversation state: the
// choice carries over across sessions and survives a page refresh, mirroring
// the semantics of filesPanelPreferences.
//
// SciencePanel keeps the live React state as the source of truth; these
// helpers only seed that state on mount and snapshot it when the user sets
// a directory, so a refresh reopens the same project.

export interface SciencePreferences {
  /**
   * Absolute path of the active science project directory, sent as the
   * `project` query parameter on every science endpoint. `null` when the
   * user has not opened a project yet (the panel shows its setup state).
   */
  projectDir: string | null;
}

const STORAGE_KEY = "omnigent:science-preferences";

// No project selected by default — the panel opens on its directory-input
// empty state rather than probing a path that may not exist.
export const DEFAULT_SCIENCE_PREFERENCES: SciencePreferences = {
  projectDir: null,
};

/**
 * Read the persisted science preference. Returns the default when nothing
 * is stored, on a server render (no `window`), or when the stored value is
 * malformed — never throws, so a corrupt entry can't break the panel.
 */
export function readSciencePreferences(): SciencePreferences {
  if (typeof window === "undefined") return DEFAULT_SCIENCE_PREFERENCES;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SCIENCE_PREFERENCES;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return DEFAULT_SCIENCE_PREFERENCES;
    }
    const p = parsed as Record<string, unknown>;
    return {
      projectDir:
        typeof p.projectDir === "string" && p.projectDir.length > 0
          ? p.projectDir
          : DEFAULT_SCIENCE_PREFERENCES.projectDir,
    };
  } catch (error) {
    console.warn("Failed to read science preferences; using defaults.", error);
    return DEFAULT_SCIENCE_PREFERENCES;
  }
}

/**
 * Persist the science preference. Swallows quota/access errors so a failed
 * write can't break the panel.
 */
export function writeSciencePreferences(prefs: SciencePreferences): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch (error) {
    console.warn("Failed to persist science preferences.", error);
  }
}
