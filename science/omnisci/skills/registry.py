# SPDX-License-Identifier: Apache-2.0
"""Skill source registry (spec §10.1) and git fetch/resolve helpers.

Sources are declared in ``.omnisci/sources.yaml``:

```yaml
sources:
  k_dense:
    kind: git
    url: https://github.com/K-Dense-AI/scientific-agent-skills
    ref: v2.42.0
    layout: agent-skills
```

``sync`` clones/fetches each source into a bare cache under
``.omnisci/cache/sources/<name>.git`` and resolves the configured ref to a
commit. It never touches installed skill snapshots or the lockfile
(spec §10.3: fetch upstream refs without changing installed projects).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel

from omnisci.errors import NotFoundError, StateError

SOURCES_FILE = "sources.yaml"
CACHE_DIR = "cache/sources"


class SkillSource(BaseModel):
    kind: str = "git"
    url: str
    ref: str = "HEAD"
    layout: str = "agent-skills"


def load_sources(science_dir: Path) -> dict[str, SkillSource]:
    path = science_dir / SOURCES_FILE
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    sources: dict[str, SkillSource] = {}
    for name, raw in (data.get("sources") or {}).items():
        src = SkillSource.model_validate(raw)
        if src.kind != "git":
            raise StateError(f"skill source '{name}': unsupported kind '{src.kind}' (only 'git')")
        if src.layout != "agent-skills":
            raise StateError(
                f"skill source '{name}': unsupported layout '{src.layout}' (only 'agent-skills')"
            )
        sources[name] = src
    return sources


def _git(repo: Path | None, *args: str) -> str:
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    cmd += list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StateError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def cache_repo(science_dir: Path, name: str) -> Path:
    return science_dir / CACHE_DIR / f"{name}.git"


def sync_source(science_dir: Path, name: str, source: SkillSource) -> str:
    """Clone or fetch the source into the cache; return the resolved commit."""
    repo = cache_repo(science_dir, name)
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        _git(None, "clone", "--bare", "--quiet", source.url, str(repo))
    else:
        # Bare clones do not reliably retain a fetch refspec. Update the
        # cached branch and tag refs explicitly so a moving configured ref
        # resolves to the newly fetched commit without touching installations.
        _git(
            repo,
            "fetch",
            "--quiet",
            "origin",
            "+refs/heads/*:refs/heads/*",
            "+refs/tags/*:refs/tags/*",
        )
    try:
        return _git(repo, "rev-parse", f"{source.ref}^{{commit}}")
    except StateError:
        raise StateError(f"skill source '{name}': cannot resolve ref '{source.ref}'") from None


def sync_sources(science_dir: Path, only: str | None = None) -> dict[str, str]:
    sources = load_sources(science_dir)
    if only is not None:
        if only not in sources:
            raise NotFoundError(f"unknown skill source: {only}")
        sources = {only: sources[only]}
    return {name: sync_source(science_dir, name, src) for name, src in sources.items()}


def resolved_revision(science_dir: Path, name: str) -> str | None:
    """Revision the source ref resolved to at last sync (None if never synced)."""
    repo = cache_repo(science_dir, name)
    if not repo.exists():
        return None
    try:
        ref = load_sources(science_dir)[name].ref
        return _git(repo, "rev-parse", f"{ref}^{{commit}}")
    except (StateError, KeyError):
        return None


def list_source_skills(science_dir: Path, name: str) -> list[dict]:
    """Skills available in a synced source: every directory holding a
    ``SKILL.md`` (Agent Skills directory format)."""
    sources = load_sources(science_dir)
    if name not in sources:
        raise NotFoundError(f"unknown skill source: {name}")
    repo = cache_repo(science_dir, name)
    if not repo.exists():
        raise StateError(f"skill source '{name}' is not synced; run 'science skills sync' first")
    sha = _git(repo, "rev-parse", f"{sources[name].ref}^{{commit}}")
    listing = _git(repo, "ls-tree", "-r", "--name-only", sha)
    skills = []
    for line in listing.splitlines():
        if Path(line).name != "SKILL.md":
            continue
        skill_path = Path(line).parent.as_posix()
        if skill_path == ".":
            continue  # a repo-root SKILL.md is not an installable skill dir
        skills.append(
            {
                "name": Path(skill_path).name,
                "source": name,
                "path": skill_path,
                "revision": sha,
            }
        )
    return sorted(skills, key=lambda s: (s["name"], s["source"]))


def archive_skill(
    science_dir: Path,
    source: str,
    revision: str,
    skill_path: str,
    dest: Path,
) -> None:
    """Materialize one skill directory at a pinned revision into ``dest``.

    Immutable plain-file snapshot via ``git archive`` — no working tree and
    no git submodules (spec §10.3).
    """
    repo = cache_repo(science_dir, source)
    if not repo.exists():
        raise StateError(f"skill source '{source}' is not synced; run 'science skills sync' first")
    proc = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", revision, skill_path],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise NotFoundError(
            f"skill path '{skill_path}' not found in source '{source}' "
            f"at {revision[:7]}: {proc.stderr.decode(errors='replace').strip()}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{dest.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    target = PurePosixPath(skill_path)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        members = tar.getmembers()
        if not members:
            staging.rmdir()
            raise NotFoundError(
                f"skill path '{skill_path}' is empty in source '{source}' at {revision[:7]}"
            )
        for member in members:
            path = PurePosixPath(member.name)
            is_target_entry = path == target or target in path.parents or path in target.parents
            if path.is_absolute() or ".." in path.parts or not is_target_entry:
                raise StateError(f"unexpected path in skill archive: {member.name}")
            if member.issym() or member.islnk():
                raise StateError(f"link in skill archive is not allowed: {member.name}")
        tar.extractall(staging, members=members, filter="data")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(staging / skill_path), str(dest))
    shutil.rmtree(staging, ignore_errors=True)
