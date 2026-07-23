# SPDX-License-Identifier: Apache-2.0
"""Skills registry tests (spec §10, §20) against a local bare git source.

The fake skill source is a real git repository created in ``tmp_path`` with
the git CLI — no network. It carries three skills: ``scanpy`` (MIT),
``gpltool`` (GPL, disallowed) and ``mystery`` (no LICENSE file).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from omnisci.cli.main import main
from omnisci.errors import NotFoundError, StateError
from omnisci.service import ScienceService
from omnisci.skills.lockfile import load_lockfile

MIT_TEXT = """MIT License

Copyright (c) 2026 Test

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""

GPL_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007
"""

SKILL_MD = "---\nname: {name}\ndescription: test skill {name}\n---\n# {name}\n"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _write_skill(work: Path, name: str, license_text: str | None, body: str = "") -> None:
    skill_dir = work / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD.format(name=name) + body)
    if license_text is not None:
        (skill_dir / "LICENSE").write_text(license_text)


@pytest.fixture
def source_work(tmp_path):
    """Working repo behind the fake source; tests can add commits to it."""
    work = tmp_path / "src-work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _write_skill(work, "scanpy", MIT_TEXT)
    _write_skill(work, "gpltool", GPL_TEXT)
    _write_skill(work, "mystery", None)
    _git(work, "add", ".")
    _git(work, "commit", "-m", "v1")
    return work


@pytest.fixture
def source_url(source_work):
    # The working repository acts as the upstream so commits made by update
    # tests are visible to a later fetch from the science cache.
    return str(source_work)


@pytest.fixture
def svc(project_dir, source_url):
    service = ScienceService.init_project(project_dir, research_goal="g")
    (service.state_dir / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "local": {
                        "kind": "git",
                        "url": source_url,
                        "ref": "main",
                        "layout": "agent-skills",
                    }
                }
            }
        )
    )
    return service


def _commit_v2(source_work: Path) -> str:
    _write_skill(source_work, "scanpy", MIT_TEXT, body="updated in v2\n")
    _git(source_work, "add", ".")
    _git(source_work, "commit", "-m", "v2")
    return _git(source_work, "rev-parse", "HEAD")


# -- sync -----------------------------------------------------------------


def test_sync_resolves_refs_without_touching_installed_skills(svc):
    resolved = svc.skills_sync()
    assert set(resolved) == {"local"}
    assert len(resolved["local"]) == 40  # full commit sha
    assert not (svc.state_dir / "sources.lock.yaml").exists()
    assert not (svc.state_dir / "skills").exists()
    # the cache is a bare repo, no working tree / submodules
    assert (svc.state_dir / "cache" / "sources" / "local.git" / "HEAD").exists()


def test_sync_unknown_source_is_not_found(svc):
    with pytest.raises(NotFoundError):
        svc.skills_sync(source="nope")


# -- search / list ----------------------------------------------------------


def test_search_after_sync(svc):
    assert svc.skills_search() == []  # nothing synced yet
    svc.skills_sync()
    all_skills = svc.skills_search()
    assert {s["name"] for s in all_skills} == {"scanpy", "gpltool", "mystery"}
    only = svc.skills_search("SCAN")
    assert [s["name"] for s in only] == ["scanpy"]
    assert only[0]["path"] == "skills/scanpy"
    assert only[0]["source"] == "local"


# -- install ----------------------------------------------------------------


def test_install_records_lockfile_and_snapshot(svc):
    entry = svc.skill_install("scanpy")
    assert entry.source == "local"
    assert len(entry.revision) == 40
    assert entry.path == "skills/scanpy"
    assert entry.content_hash.startswith("sha256:")
    assert entry.license == "MIT"
    assert entry.installed_at

    snapshot = svc.state_dir / "skills" / f"scanpy@{entry.revision[:7]}"
    assert (snapshot / "SKILL.md").is_file()
    assert (snapshot / "LICENSE").is_file()
    assert not (snapshot / ".git").exists()  # plain files, no submodule

    locked = load_lockfile(svc.state_dir)
    assert locked["scanpy"].revision == entry.revision
    # installing again is a state error (use upgrade instead)
    with pytest.raises(StateError):
        svc.skill_install("scanpy")


def test_install_unknown_skill_is_not_found(svc):
    with pytest.raises(NotFoundError):
        svc.skill_install("does-not-exist")


def test_disallowed_license_blocks_installation(svc):
    with pytest.raises(StateError, match="GPL"):
        svc.skill_install("gpltool")
    assert "gpltool" not in load_lockfile(svc.state_dir)
    # --allow-unknown-license does not rescue a disallowed license
    with pytest.raises(StateError):
        svc.skill_install("gpltool", allow_unknown_license=True)
    assert not list((svc.state_dir / "skills").glob("gpltool@*"))  # no orphan


def test_unknown_license_blocked_unless_overridden(svc):
    with pytest.raises(StateError, match="UNKNOWN"):
        svc.skill_install("mystery")
    assert "mystery" not in load_lockfile(svc.state_dir)

    entry = svc.skill_install("mystery", allow_unknown_license=True)
    assert entry.license == "UNKNOWN"
    assert entry.license_override is True
    raw = yaml.safe_load((svc.state_dir / "sources.lock.yaml").read_text())
    assert raw["skills"]["mystery"]["license_override"] is True


# -- enable / disable --------------------------------------------------------


def test_enable_disable_are_project_flags(svc, project_dir):
    svc.skill_install("scanpy")
    with pytest.raises(NotFoundError):
        svc.skill_enable("mystery")  # not installed

    out = svc.skill_enable("scanpy")
    assert out["enabled_skills"] == ["scanpy"]
    # persisted in workspace SQLite, not a project manifest
    reloaded = ScienceService(project_dir)
    assert reloaded._enabled_skills() == ["scanpy"]

    out = svc.skill_disable("scanpy")
    assert out["enabled_skills"] == []
    assert ScienceService(project_dir)._enabled_skills() == []


# -- update behavior (spec §10.3) --------------------------------------------


def test_sync_does_not_mutate_pinned_project(svc, source_work):
    entry = svc.skill_install("scanpy")
    _commit_v2(source_work)
    svc.skills_sync()  # fetches upstream — must not touch the pin
    locked = load_lockfile(svc.state_dir)
    assert locked["scanpy"].revision == entry.revision
    assert locked["scanpy"].content_hash == entry.content_hash


def test_upgrade_and_rollback(svc, source_work):
    old = svc.skill_install("scanpy")
    new_sha = _commit_v2(source_work)

    upgraded = svc.skill_upgrade("scanpy")
    assert upgraded.revision == new_sha
    assert upgraded.content_hash != old.content_hash
    assert upgraded.previous is not None
    assert upgraded.previous["revision"] == old.revision
    # the new snapshot was installed beside the existing one
    assert (svc.state_dir / "skills" / f"scanpy@{old.revision[:7]}").is_dir()
    new_snapshot = svc.state_dir / "skills" / f"scanpy@{new_sha[:7]}"
    assert (new_snapshot / "SKILL.md").read_text().endswith("updated in v2\n")

    # upgrading again with no new upstream commits is a no-op error
    with pytest.raises(StateError, match="already at the latest"):
        svc.skill_upgrade("scanpy")

    rolled_back = svc.skill_rollback("scanpy")
    assert rolled_back.revision == old.revision
    assert rolled_back.content_hash == old.content_hash
    assert rolled_back.previous is None
    assert load_lockfile(svc.state_dir)["scanpy"].revision == old.revision

    with pytest.raises(StateError, match="no previous revision"):
        svc.skill_rollback("scanpy")


def test_upgrade_uninstalled_is_not_found(svc):
    with pytest.raises(NotFoundError):
        svc.skill_upgrade("scanpy")


# -- CLI ----------------------------------------------------------------------


def _cli(project_dir, *argv) -> tuple[int, object]:

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--project", str(project_dir), *argv, "--json"])
    out = buf.getvalue()
    return rc, (json.loads(out) if out.strip() else None)


def test_cli_skills_roundtrip(project_dir, source_url):
    ScienceService.init_project(project_dir, research_goal="g")
    state_dir = project_dir / ".omnisci"
    (state_dir / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "local": {
                        "kind": "git",
                        "url": source_url,
                        "ref": "main",
                        "layout": "agent-skills",
                    }
                }
            }
        )
    )

    rc, out = _cli(project_dir, "skills", "sync")
    assert rc == 0
    assert set(out["synced"]) == {"local"}

    rc, out = _cli(project_dir, "skills", "search", "scan")
    assert rc == 0
    assert [s["name"] for s in out] == ["scanpy"]

    rc, out = _cli(project_dir, "skills", "install", "scanpy")
    assert rc == 0
    assert out["license"] == "MIT"
    assert out["path"] == "skills/scanpy"

    rc, out = _cli(project_dir, "skills", "list")
    assert rc == 0
    assert out["sources"]["local"]["synced_revision"] == out["installed"][0]["revision"]
    assert out["installed"][0]["name"] == "scanpy"
    assert out["installed"][0]["enabled"] is False

    rc, out = _cli(project_dir, "skills", "enable", "scanpy")
    assert rc == 0
    assert out["enabled_skills"] == ["scanpy"]

    rc, out = _cli(project_dir, "skills", "disable", "scanpy")
    assert rc == 0
    assert out["enabled_skills"] == []


def test_cli_license_block_exit_code(project_dir, source_url):
    ScienceService.init_project(project_dir, research_goal="g")
    (project_dir / ".omnisci" / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "local": {
                        "kind": "git",
                        "url": source_url,
                        "ref": "main",
                        "layout": "agent-skills",
                    }
                }
            }
        )
    )
    rc, out = _cli(project_dir, "skills", "install", "gpltool")
    assert rc == 1  # runtime failure: license gate (spec §20)
    assert "GPL" in out["error"]

    rc, out = _cli(project_dir, "skills", "install", "mystery", "--allow-unknown-license")
    assert rc == 0
    assert out["license"] == "UNKNOWN"
    assert out["license_override"] is True

    rc, out = _cli(project_dir, "skills", "enable", "not-installed")
    assert rc == 3  # not found
