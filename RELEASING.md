# Releasing

**This fork does not publish release artifacts.**

OmniSci is a permanent fork of
[omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent) pinned to an
upstream release (see [`FORK_DELTA.md`](FORK_DELTA.md)). Upstream's release
process publishes three PyPI packages from a separate, access-controlled
repository and stages docs onto a site repository. None of that infrastructure is
reachable from here, and every release-side workflow in `.github/workflows/` is
guarded to the upstream repository and inert on this fork.

Use OmniSci from a source checkout — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Bumping the upstream pin

The one release-shaped task this fork does have is moving to a newer upstream
release. Record the new pin in `FORK_DELTA.md`, resolve conflicts in the files
listed in its table, and confirm the science suites still pass:

```bash
uv run pytest science/tests
uv run pytest tests/science_server
```

If this fork ever publishes an artifact, this document gets rewritten rather
than restored from upstream — the upstream process does not apply.
