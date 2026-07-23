# SPDX-License-Identifier: Apache-2.0
"""Compute provider discovery through the PRD-defined entry-point group."""

from __future__ import annotations

from importlib.metadata import entry_points

ENTRY_POINT_GROUP = "omnigent_science.compute"


def load_provider_factories(group: str = ENTRY_POINT_GROUP) -> dict[str, object]:
    """Return installed provider classes/factories keyed by entry-point name.

    Configured factories are constructed by :class:`ScienceService` with the
    keyword arguments ``project_dir``, ``runs_dir`` and ``config``.
    """
    return {entry_point.name: entry_point.load() for entry_point in entry_points(group=group)}
