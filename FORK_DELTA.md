# Fork delta

This repository is a fork of [omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent)
("upstream" remote) that adds the OmniSci scientific workbench layer.

- **Pinned upstream release:** `v0.6.0` (`375f540421baf3ad46fae0805b78063682f281de`)
- **Policy:** pin releases, do not track upstream `main`. Keep science code in new
  directories. Wrap unavoidable core changes behind small interfaces or feature flags.

## Modified upstream files

| File                                                                                                                                                                                                                                                              | Reason                                                                                                                                                |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pyproject.toml`, `uv.lock`                                                                                                                                                                                                                                       | Bundle the isolated `omnisci` package/CLI and optional provider dependencies in the fork wheel, expose it to tests and lock the resolved environment. |
| `omnigent/server/app.py`                                                                                                                                                                                                                                          | Advertise the science capability and mount the optional science API router.                                                                           |
| `web/package.json`, `web/package-lock.json`                                                                                                                                                                                                                       | Add the lazy Parquet viewer dependency and refresh frontend dependency resolution.                                                                    |
| `web/src/lib/capabilities.ts`                                                                                                                                                                                                                                     | Add the fail-closed `science_enabled` server capability.                                                                                              |
| `web/src/App.tsx`, `web/src/shell/Sidebar.tsx`                                                                                                                                                                                                                   | Make the scientific Project workspace a top-level OmniSci destination.                                                                                |
| `web/src/shell/AppShell.tsx`                                                                                                                                                                                                                                      | Gate and route the contextual Project workspace-rail tab and its compact-screen drawer.                                                               |
| `web/src/shell/ChatHeader.tsx`                                                                                                                                                                                                                                    | Expose the contextual Project drawer from the compact-screen session menu.                                                                            |
| `web/src/shell/WorkspacePanel.tsx`                                                                                                                                                                                                                                | Render the contextual Project tab and panel.                                                                                                          |
| `web/src/shell/CodeViewer.tsx`                                                                                                                                                                                                                                    | Dispatch CSV/TSV, JSON and Parquet files to maintained science viewers.                                                                               |
| `web/src/shell/codeViewerHelpers.ts`                                                                                                                                                                                                                              | Detect science table/tree formats and treat Parquet as binary.                                                                                        |
| `web/src/shell/railTabs.ts`                                                                                                                                                                                                                                       | Add `science` to the right-rail tab type.                                                                                                             |
| `web/src/lib/sessionWorkspaceState.ts`, `web/src/lib/sessionWorkspaceState.test.ts`                                                                                                                                                                               | Persist and restore the contextual Project rail selection.                                                                                            |
| `web/src/embed.tsx`, `web/src/main.tsx`                                                                                                                                                                                                                           | Keep non-server capability fallbacks fail-closed for Science.                                                                                         |
| `web/src/components/PermissionsModal.test.tsx`, `web/src/shell/AppShell.test.tsx`, `web/src/shell/ChatHeader.test.tsx`, `web/src/shell/NewChatDialog.test.tsx`, `web/src/shell/Sidebar.rowActions.test.tsx`, `web/src/shell/WorkspacePanel.test.tsx`              | Update capability fixtures and cover the Science rail and compact-screen seams.                                                                       |
| `web/index.html`, `web/vite.config.ts`                                                                                                                                                                                                                            | Present the fork as OmniSci in the browser title and installable-app manifest.                                                                        |
| `web/src/shell/NewChatDialog.tsx`, `web/src/pages/ChatPage.tsx`, `web/src/shell/TitleBarServerPicker.tsx`                                                                                                                                                         | Make OmniSci the top-level shell identity and add research-native launcher copy and prompt starters.                                                  |
| `web/src/shell/settingsNav.tsx`, `web/src/pages/SettingsPage.tsx`                                                                                                                                                                                                 | Add Project, Compute, Storage, and tool readiness as first-class research infrastructure settings.                                                     |
| `web/src/pages/LoginPage.tsx`, `web/src/pages/RegisterPage.tsx`, `web/src/components/pwa/PWAUpdateBanner.tsx`, `web/src/components/UpdateBanner.tsx`, `web/src/components/PermissionsModal.tsx`, `web/src/components/OttoEyes.tsx`, `web/src/lib/themePalette.ts` | Use the OmniSci product identity across user-visible auth, update, sharing, accessibility, and theme surfaces.                                        |
| `web/src/pages/SettingsPage.test.tsx`, `web/src/shell/settingsNav.test.tsx`, `web/src/shell/Sidebar.test.tsx`, `web/src/components/OttoEyes.test.tsx`, `web/src/components/UpdateBanner.test.tsx`                                                                 | Cover OmniSci research settings, navigation defaults, shell branding, and update/accessibility copy.                                                  |

## Added files (not upstream)

- `science/` — self-contained Python package (`omnisci`): domain schemas,
  SQLite project state, JSON CLI/MCP façades, role configs, local compute,
  local/S3 storage, skills and tests.
- `omnigent/server/routes/science.py` — authenticated HTTP adapter over the
  shared science application service.
- `tests/science_server/` — server seam/contract tests.
- `docs/OMNISCI_PRD.md` — concise product boundary and requirements for the
  scientific layer relative to upstream OmniGent.
- `web/src/components/science/`, `web/src/hooks/useScience.ts`,
  `web/src/lib/scienceApi.ts`, `web/src/lib/sciencePreferences.ts` — science
  project panel, provider settings, and typed client state.
- `web/src/pages/ProjectPage.tsx` — top-level scientific project workspace.
- `web/src/assets/omnisci-scientist.png`, `web/src/components/OmniSciMascot.tsx` —
  generated lab-coated OmniSci launcher mascot and its accessible UI wrapper.
- `web/src/shell/CsvTableViewer.tsx`, `web/src/shell/JsonTreeViewer.tsx`,
  `web/src/shell/ParquetTableViewer.tsx`, `web/src/shell/RichSourceToggle.tsx`
  and their tests — maintained artifact viewers.
- `examples/science/` — the Science agent bundle, specialist skills, and its
  restricted asynchronous reviewer.
- `FORK_DELTA.md` — this file.

Every future change to an upstream file must be recorded in the table above with a reason.
