# SPDX-License-Identifier: Apache-2.0
"""Skills lockfile (spec §10.2): ``.omnisci/sources.lock.yaml``.

```yaml
skills:
  scanpy:
    source: k_dense
    revision: 03608ab...
    path: skills/scanpy
    content_hash: sha256:...
    license: MIT
    installed_at: 2026-07-22T00:00:00+00:00
```

``license_override`` records an explicit ``--allow-unknown-license``
override (spec §20); ``previous`` holds the prior pinned entry so a project
can roll back after an upgrade (spec §10.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from omnisci.domain.schemas import utcnow

LOCKFILE = "sources.lock.yaml"


class LockedSkill(BaseModel):
    source: str
    revision: str
    path: str
    content_hash: str
    license: str
    installed_at: str = Field(default_factory=utcnow)
    license_override: bool = False
    previous: dict[str, Any] | None = None

    def prior(self) -> LockedSkill:
        """The previous pinned entry (without its own rollback chain)."""
        assert self.previous is not None
        return LockedSkill.model_validate(self.previous)


def load_lockfile(science_dir: Path) -> dict[str, LockedSkill]:
    path = science_dir / LOCKFILE
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {
        name: LockedSkill.model_validate(raw) for name, raw in (data.get("skills") or {}).items()
    }


def save_lockfile(science_dir: Path, skills: dict[str, LockedSkill]) -> None:
    data = {
        "skills": {name: entry.model_dump(mode="json") for name, entry in sorted(skills.items())}
    }
    (science_dir / LOCKFILE).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
