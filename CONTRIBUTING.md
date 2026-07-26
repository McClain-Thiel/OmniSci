# Contributing to OmniSci

Thanks for your interest in OmniSci. Issues and pull requests are welcome. For
larger changes, open an issue first so we can agree on the approach before you
spend a weekend on it.

Please don't include secrets, internal URLs, personal data, or private
configuration in issues, tests, examples, or logs.

## What this project is, and where your work goes

OmniSci is a **permanent fork** of
[omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent), pinned to an
upstream release. We do not track upstream `main`, and we do not upstream
patches. Work you contribute here stays here — that is worth knowing before you
start.

The practical consequence for contributors is the fork policy in
[`FORK_DELTA.md`](FORK_DELTA.md): science-specific code goes in **new**
directories, and any change to a file inherited from upstream must add a row to
that file explaining why. Keeping the upstream diff small is what makes the next
release pin merge tractable. If your change can live in `science/` or
`web/src/components/science/` instead of editing an upstream file, put it there.

## Finding something to work on

- **[Good first issues](https://github.com/McClain-Thiel/OmniSci/labels/good%20first%20issue)** —
  scoped so the decision is already made and success is verifiable without
  running any compute.
- **[Help wanted](https://github.com/McClain-Thiel/OmniSci/labels/help%20wanted)** —
  larger, still well-defined.
- **[`docs/OMNISCI_AUDIT.md`](docs/OMNISCI_AUDIT.md)** is the real backlog. Every
  item carries `file:line` evidence and a priority. GitHub issues are views onto
  that document; when the two disagree, the document wins.
- **[`docs/HPC.md`](docs/HPC.md)** covers cluster setup — SSH, schedulers, and the
  shared-filesystem requirement.
- **[`docs/OMNISCI_PRD.md`](docs/OMNISCI_PRD.md)** is the standard to build
  against — in particular the compute **connector contract**, which is what a new
  compute backend has to satisfy.

**To claim an issue, comment on it.** No assignment ceremony. If an issue has
been claimed but is stale for two weeks, it's fair game again — say so in a
comment and go ahead.

## Development setup

A Python package with a frontend under `web/`. Use
[`uv`](https://docs.astral.sh/uv/).

**Supported dev OS: macOS or Linux.** Native Windows is not supported — some test
dependencies are POSIX-only (`pexpect`/`pyte`), a few modules call `os.getuid()`
at import time, and the `pre-commit` hooks assume the Unix `.venv/bin/` layout.
On Windows use **WSL2 (Ubuntu)** and clone into the **Linux** filesystem
(`~/…`, not `/mnt/c`); this matches CI. Git Bash is not sufficient.

Prerequisites:

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- `tmux`, for the native harness terminals the local host launches
  (`brew install tmux`, or `apt install tmux`).
- `bubblewrap` (`bwrap`), **Linux only**, to OS-sandbox those terminals
  (`apt install bubblewrap`). macOS uses the built-in `seatbelt` sandbox.
- Node.js 22 LTS or newer with `npm` when working on `web/`.

```bash
git clone https://github.com/McClain-Thiel/OmniSci.git
cd OmniSci

uv python install
uv venv --python "$(cat .python-version)"
uv sync --extra all --extra dev
source .venv/bin/activate    # or prefix commands with `uv run`
```

Common checks:

```bash
uv run pytest                      # Python tests (e2e/live skipped by default)
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
```

When touching `web/`:

```bash
cd web && npm install && npm run lint && npm run build
```

### Working on the science layer

The `omnisci` package lives outside `tests/`, so it has its own suite and its own
CI lane. The storage tests import `botocore` directly, which the `s3` extra
supplies:

```bash
uv sync --extra all --extra dev --extra s3
uv run pytest science/tests            # the omnisci package
uv run pytest tests/science_server      # the HTTP API seam
```

## Running locally

Three terminals:

```bash
# Terminal 1: local server on :6767
uv run omnigent server

# Terminal 2: register your machine as a host
uv run omnigent host --server http://localhost:6767

# Terminal 3: frontend dev server
cd web && npm run dev
```

Open the Vite URL, usually `http://localhost:5173/`. Host registration is what
lets the web UI browse your filesystem and start sessions on your machine —
without it the UI is read/continue-only.

### Backend-only smoke check

[`scripts/backend-smoke.sh`](scripts/backend-smoke.sh) validates the Python
backend and API server from a source checkout without building the web UI or
configuring credentials:

```bash
scripts/backend-smoke.sh              # boots on port 18080
PORT=18090 scripts/backend-smoke.sh   # override if 18080 is busy
```

Fully isolated and disposable — every artifact lives under one `mktemp -d`
removed on exit, so it never touches your real `~/.omnigent`. It does not cover
the web UI, approval flows, or agent execution.

## Tests

A change that alters behaviour should ship with a test, and a bug fix should add
a test that fails before the fix. Pure refactors, renames, type-only changes,
dependency bumps, and edits with no observable behaviour change don't need one.

Prefer the smallest test that covers the change. A fast, focused **unit test** in
the area suite is the default. Reach for `tests/integration/` only when behaviour
genuinely spans components, and `tests/e2e/` only for full-stack flows a unit
test can't capture.

Most areas mirror their source directory:

| Area changed | Test suite |
| --- | --- |
| `science/omnisci/compute/` | `science/tests/test_compute_*.py` |
| `science/omnisci/` (domain, service, storage) | `science/tests/` |
| `omnigent/server/routes/science.py` | `tests/science_server/` |
| `web/src/components/science/` | colocated `*.test.tsx` + `tests/e2e_ui/` |
| `omnigent/server/` | `tests/server/` |
| `omnigent/runner/` | `tests/runner/` |
| `omnigent/runtime/` | `tests/runtime/` |
| `omnigent/tools/` | `tests/tools/` |
| `omnigent/inner/` | `tests/inner/` |
| `omnigent/db/` | `tests/db/` (a schema migration especially warrants one) |
| `omnigent/policies/` | `tests/policies/` |
| `omnigent/stores/` | `tests/stores/` |
| `omnigent/spec/` | `tests/spec/` |

A new **compute connector** is the one case with a specific bar: it must satisfy
every point of the connector contract in `docs/OMNISCI_PRD.md`, and its tests
must cover submission returning promptly, state surviving a restart, a terminal
failure releasing remote resources, and checksummed output collection.

### Frontend (`web/`)

- Add or update a **colocated Vitest test** next to what you changed; run with
  `npm test`.
- A change to **user-facing UI behaviour** also needs a Playwright test under
  `tests/e2e_ui/`. This is enforced mechanically by the `E2E UI Required` check.
- Styling-only changes, copy tweaks with no flow change, and behaviour-preserving
  refactors are exempt.

## Pull requests

- Branch from `main` and keep changes focused.
- Fill in the PR template — every section. `N/A` is a fine answer where it
  applies.
- For **UI / frontend changes**, check the "UI / frontend change" box and attach
  a **video or images** under `Demo` so a reviewer can see the behaviour without
  checking out the branch.
- If you touched a file inherited from upstream, add the `FORK_DELTA.md` row in
  the same PR.
- CI runs the full matrix on every PR, including the science lane. Open as a
  **draft** while iterating — draft PRs skip the pytest matrix, which makes the
  loop much faster.

A maintainer has to approve before merge. That's the `Maintainer Approval` check,
and it is not a comment on your work — it is how a single-maintainer project
stays honest.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
