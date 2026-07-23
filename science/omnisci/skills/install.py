# SPDX-License-Identifier: Apache-2.0
"""Install / upgrade / rollback of skills (spec §10.2–10.3, §16, §20).

Installs materialize an immutable snapshot of the skill directory at the
pinned revision under ``.omnisci/skills/<name>@<revision>/`` and record
provenance in the lockfile. Format validation requires a ``SKILL.md``
(Agent Skills directory format). The license is detected from the skill's
own LICENSE file — never inherited from the source repository (spec §16) —
and an UNKNOWN or disallowed license blocks installation (spec §20) unless
``--allow-unknown-license`` is passed, which is recorded in the lockfile.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from omnisci.errors import NotFoundError, StateError
from omnisci.skills import registry
from omnisci.skills.lockfile import LockedSkill, load_lockfile, save_lockfile

SKILLS_DIR = "skills"

# Permissive licenses accepted without an override (spec §16/§20).
ALLOWED_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "CC0-1.0",
        "CC-BY-4.0",
        "Unlicense",
    }
)

_LICENSE_STEMS = {"license", "licence", "copying"}


def detect_license(skill_dir: Path) -> str:
    """SPDX-ish id detected from the skill's own LICENSE file, else UNKNOWN."""
    text = ""
    for candidate in sorted(skill_dir.iterdir()):
        if candidate.is_file() and candidate.name.split(".")[0].lower() in _LICENSE_STEMS:
            text = candidate.read_text(errors="replace")
            break
    if not text:
        return "UNKNOWN"
    head = text[:6000]
    upper = head.upper()
    if "Apache License" in head and "Version 2.0" in head:
        return "Apache-2.0"
    if "GNU AFFERO GENERAL PUBLIC LICENSE" in upper:
        return "AGPL"
    if "GNU LESSER GENERAL PUBLIC LICENSE" in upper:
        return "LGPL"
    if "GNU GENERAL PUBLIC LICENSE" in upper:
        return "GPL"
    if "MIT License" in head:
        return "MIT"
    if "Redistribution and use in source and binary forms" in head:
        if "Neither the name" in head:
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "ISC License" in head or (
        "Permission to use, copy, modify, and/or distribute this software" in head
    ):
        return "ISC"
    if "CC0 1.0 Universal" in head:
        return "CC0-1.0"
    if "Creative Commons Attribution 4.0" in head:
        return "CC-BY-4.0"
    if "free and unencumbered software released into the public domain" in head:
        return "Unlicense"
    return "UNKNOWN"


def _check_license(name: str, license: str, allow_unknown_license: bool) -> bool:
    """Enforce the license gate (spec §20). Returns True if an override was used."""
    if license == "UNKNOWN":
        if allow_unknown_license:
            return True
        raise StateError(
            f"skill '{name}': license is UNKNOWN; no recognizable license in "
            "the skill's own LICENSE "
            "file; installation blocked (spec §20). Pass --allow-unknown-license "
            "to override (recorded in the lockfile)."
        )
    if license not in ALLOWED_LICENSES:
        raise StateError(
            f"skill '{name}': license '{license}' is not in the allowed set "
            f"({', '.join(sorted(ALLOWED_LICENSES))}); installation blocked (spec §20)."
        )
    return False


def _content_hash(skill_dir: Path) -> str:
    """Deterministic sha256 over relative paths and file contents."""
    digest = hashlib.sha256()
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        digest.update(path.relative_to(skill_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def _snapshot_dir(science_dir: Path, name: str, revision: str) -> Path:
    return science_dir / SKILLS_DIR / f"{name}@{revision[:7]}"


def _find_skill(science_dir: Path, name: str, source: str | None) -> dict:
    """Locate a skill by name in the (synced) sources; syncs on first use."""
    sources = registry.load_sources(science_dir)
    if source is not None:
        if source not in sources:
            raise NotFoundError(f"unknown skill source: {source}")
        sources = {source: sources[source]}
    matches = []
    for src_name, src in sources.items():
        if not registry.cache_repo(science_dir, src_name).exists():
            registry.sync_source(science_dir, src_name, src)
        matches.extend(
            s for s in registry.list_source_skills(science_dir, src_name) if s["name"] == name
        )
    if not matches:
        raise NotFoundError(
            f"skill '{name}' not found in "
            + ("source " + repr(source) if source else "any configured source")
        )
    if len(matches) > 1:
        choices = ", ".join(m["source"] for m in matches)
        raise StateError(f"skill '{name}' found in multiple sources ({choices}); pass --source")
    return matches[0]


def _materialize(
    science_dir: Path, source: str, revision: str, skill_path: str, name: str
) -> Path:
    snapshot = _snapshot_dir(science_dir, name, revision)
    if not snapshot.exists():
        registry.archive_skill(science_dir, source, revision, skill_path, snapshot)
    if not (snapshot / "SKILL.md").is_file():
        raise StateError(
            f"skill '{name}' is not a valid Agent Skills directory: no SKILL.md at {skill_path}"
        )
    return snapshot


def _prepare(
    science_dir: Path,
    name: str,
    source: str,
    revision: str,
    skill_path: str,
    allow_unknown_license: bool,
) -> tuple[str, bool, str]:
    """Materialize + validate a snapshot; returns (license, override, hash).

    A failed validation removes a snapshot created by this call so a blocked
    install leaves no orphaned files behind.
    """
    snapshot_dir = _snapshot_dir(science_dir, name, revision)
    created = not snapshot_dir.exists()
    try:
        snapshot = _materialize(science_dir, source, revision, skill_path, name)
        license = detect_license(snapshot)
        override = _check_license(name, license, allow_unknown_license)
        return license, override, _content_hash(snapshot)
    except StateError:
        if created:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise


def install_skill(
    science_dir: Path,
    name: str,
    source: str | None = None,
    allow_unknown_license: bool = False,
) -> LockedSkill:
    skills = load_lockfile(science_dir)
    if name in skills:
        raise StateError(
            f"skill '{name}' is already installed at "
            f"{skills[name].revision[:7]}; use 'science skills upgrade'"
        )
    match = _find_skill(science_dir, name, source)
    license, override, content_hash = _prepare(
        science_dir,
        name,
        match["source"],
        match["revision"],
        match["path"],
        allow_unknown_license,
    )
    entry = LockedSkill(
        source=match["source"],
        revision=match["revision"],
        path=match["path"],
        content_hash=content_hash,
        license=license,
        license_override=override,
    )
    skills[name] = entry
    save_lockfile(science_dir, skills)
    return entry


def upgrade_skill(
    science_dir: Path,
    name: str,
    allow_unknown_license: bool = False,
) -> LockedSkill:
    """Move a skill to the source ref's current revision (spec §10.3).

    Fetches the source first (cache only — the active project is never
    mutated by a fetch), re-runs format and license validation, installs the
    new snapshot beside the existing one, and keeps the prior pin for
    rollback.
    """
    skills = load_lockfile(science_dir)
    entry = skills.get(name)
    if entry is None:
        raise NotFoundError(f"skill '{name}' is not installed")
    sources = registry.load_sources(science_dir)
    if entry.source not in sources:
        raise StateError(
            f"source '{entry.source}' for skill '{name}' is no longer in sources.yaml"
        )
    new_revision = registry.sync_source(science_dir, entry.source, sources[entry.source])
    if new_revision == entry.revision:
        raise StateError(
            f"skill '{name}' is already at the latest revision of "
            f"'{entry.source}' ({new_revision[:7]})"
        )
    license, override, content_hash = _prepare(
        science_dir,
        name,
        entry.source,
        new_revision,
        entry.path,
        allow_unknown_license,
    )
    new_entry = LockedSkill(
        source=entry.source,
        revision=new_revision,
        path=entry.path,
        content_hash=content_hash,
        license=license,
        license_override=override,
        previous=entry.model_dump(mode="json", exclude={"previous"}),
    )
    skills[name] = new_entry
    save_lockfile(science_dir, skills)
    return new_entry


def rollback_skill(science_dir: Path, name: str) -> LockedSkill:
    """Restore the previous pinned revision (spec §10.3)."""
    skills = load_lockfile(science_dir)
    entry = skills.get(name)
    if entry is None:
        raise NotFoundError(f"skill '{name}' is not installed")
    if entry.previous is None:
        raise StateError(f"skill '{name}' has no previous revision to roll back to")
    restored = entry.prior()
    _materialize(science_dir, restored.source, restored.revision, restored.path, name)
    skills[name] = restored
    save_lockfile(science_dir, skills)
    return restored
