# SPDX-License-Identifier: Apache-2.0
"""Skill source management (spec §10).

- ``registry`` — source registry (``.omnisci/sources.yaml``) and git
  fetch/resolve against a bare cache (``sync`` never touches installed
  skills).
- ``lockfile`` — pinned install records (``.omnisci/sources.lock.yaml``).
- ``install`` — install / upgrade / rollback with format validation and
  license gating (spec §16, §20).
"""
