import { afterEach, describe, expect, it } from "vitest";
import {
  DEFAULT_SCIENCE_PREFERENCES,
  readSciencePreferences,
  writeSciencePreferences,
} from "./sciencePreferences";

const STORAGE_KEY = "omnigent:science-preferences";

afterEach(() => {
  localStorage.clear();
});

describe("sciencePreferences", () => {
  it("defaults to no project directory when nothing is stored", () => {
    // With no saved choice the panel shows its directory-input empty state,
    // not a probe against a path that may not exist.
    expect(readSciencePreferences()).toEqual(DEFAULT_SCIENCE_PREFERENCES);
    expect(DEFAULT_SCIENCE_PREFERENCES.projectDir).toBeNull();
  });

  it("round-trips a written preference", () => {
    writeSciencePreferences({ projectDir: "/home/user/research/proj-a" });
    expect(readSciencePreferences()).toEqual({ projectDir: "/home/user/research/proj-a" });
  });

  it("falls back to defaults on malformed JSON", () => {
    // A non-JSON string must not throw; read swallows the parse error so a
    // corrupt entry can't break the panel.
    localStorage.setItem(STORAGE_KEY, "}{not json");
    expect(readSciencePreferences()).toEqual(DEFAULT_SCIENCE_PREFERENCES);
  });

  it("falls back to defaults when the stored value is not an object", () => {
    // Valid JSON but the wrong shape (an array) must be rejected wholesale.
    localStorage.setItem(STORAGE_KEY, JSON.stringify(["/tmp/proj"]));
    expect(readSciencePreferences()).toEqual(DEFAULT_SCIENCE_PREFERENCES);
  });

  it("defaults projectDir when the stored field has the wrong type", () => {
    // A record present but with a non-string projectDir must default the
    // field rather than pass a garbage value through to the panel.
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ projectDir: 42 }));
    expect(readSciencePreferences()).toEqual(DEFAULT_SCIENCE_PREFERENCES);
  });

  it("defaults projectDir when the stored value is an empty string", () => {
    // An empty directory is meaningless as a `project` query parameter.
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ projectDir: "" }));
    expect(readSciencePreferences()).toEqual(DEFAULT_SCIENCE_PREFERENCES);
  });
});
