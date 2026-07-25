// Integrity checks for .github/areas.json -- the single source of truth for both
// issue triage and PR reviewer assignment. Run offline: `node .github/workflows/areas.test.js`
// (cwd = repo root). No network. Guards the invariants the two workflows rely on.
const fs = require("fs");
const path = require("path");

const areas = JSON.parse(fs.readFileSync(path.resolve(".github/areas.json"), "utf8")).areas;
const maint = new Set(
  fs.readFileSync(path.resolve(".github/MAINTAINER"), "utf8")
    .split("\n").map((l) => l.replace(/#.*/, "").trim().toLowerCase()).filter(Boolean)
);

// The comp:* labels that exist in the repo (gh cannot add a label that does not
// exist, and there is no label-sync). Every area label must be one of these.
// The last four are this fork's science layer.
const ALLOWED_LABELS = new Set([
  "comp:server", "comp:runner", "comp:repr", "comp:web-ui",
  "comp:tui", "comp:policies", "comp:harnesses", "comp:infra",
  "comp:science-core", "comp:compute", "comp:sci-skills", "comp:workbench-ui",
]);

let failures = 0;
function assert(name, cond, detail) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${detail ? "  -- " + detail : ""}`);
  if (!cond) failures++;
}

// Every owner is a known maintainer.
for (const a of areas)
  for (const o of a.owners || [])
    assert(`owner @${o} (area ${a.key}) is in MAINTAINER`, maint.has(o.toLowerCase()));

// Every label is one of the real comp:* labels.
for (const a of areas)
  assert(`area ${a.key} label ${a.label} is a real comp:*`, ALLOWED_LABELS.has(a.label));

// Every area has an owner. Upstream requires 2+; this fork has a single
// maintainer, so the floor is 1 -- raise it again when a second one appears.
// Paused owners still count: pausing someone must not force adding a new one.
for (const a of areas) {
  const n = (a.owners || []).length + (a.owners_paused || []).length;
  assert(`area ${a.key} has >= 1 owner`, n >= 1, `${n} owner(s)`);
}

// Every area has a definition and at least one path.
for (const a of areas) {
  assert(`area ${a.key} has a definition`, typeof a.definition === "string" && a.definition.length > 0);
  assert(`area ${a.key} has paths`, Array.isArray(a.paths) && a.paths.length > 0);
}

// Path resolution (last-match-wins startsWith) sends representative files to the
// expected area -- especially the web/ carve-out ordering and harness prefixes.
function resolve(fn) {
  let match = null;
  for (const a of areas) for (const p of a.paths) if (fn.startsWith(p)) match = a;
  return match;
}
// Ordering-sensitive cases: each carve-out must beat the general prefix it
// narrows, and each general prefix must still win for everything else.
const cases = [
  ["omnigent/inner/claude_sdk_executor.py", "harnesses"],
  ["omnigent/server/api.py", "server"],
  ["omnigent/server/routes/science.py", "science-core"],
  ["web/src/main.tsx", "web"],
  ["web/src/components/science/SciencePanel.tsx", "workbench-ui"],
  ["web/src/shell/CsvTableViewer.tsx", "workbench-ui"],
  ["science/omnisci/service.py", "science-core"],
  ["science/omnisci/compute/modal.py", "compute"],
  ["science/omnisci/skills/install.py", "sci-skills"],
  ["science/tests/test_compute_ssh.py", "compute"],
  ["science/tests/test_repository.py", "science-core"],
  ["examples/science/config.yaml", "sci-skills"],
];
for (const [fn, key] of cases) {
  const m = resolve(fn);
  assert(`${fn} -> ${key}`, m && m.key === key, m ? m.key : "(unmatched)");
}

console.log(failures ? `\n${failures} FAILURE(S)` : "\nAll areas.json integrity checks passed.");
process.exitCode = failures ? 1 : 0;
