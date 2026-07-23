# SPDX-License-Identifier: Apache-2.0
"""Storage provider discovery (spec §13.1).

Providers register under the ``omnigent_science.storage`` entry-point group:

.. code-block:: toml

    [project.entry-points."omnigent_science.storage"]
    s3 = "omnisci.storage.s3:S3StorageProvider"
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points

from omnisci.errors import StateError
from omnisci.storage.base import StorageProvider

ENTRY_POINT_GROUP = "omnigent_science.storage"


def load_providers(group: str = ENTRY_POINT_GROUP) -> dict[str, object]:
    """Load entry points registered under the storage group.

    Returns ``{entry_point_name: loaded object}``. Loaded objects are typically
    :class:`StorageProvider` classes or factories; construction/config is left
    to the caller.
    """
    return {ep.name: ep.load() for ep in entry_points(group=group)}


def provider_for_scheme(scheme: str, providers: Iterable[StorageProvider]) -> StorageProvider:
    """Pick the first provider instance that handles ``scheme``."""
    for provider in providers:
        if scheme in provider.schemes():
            return provider
    raise StateError(f"no storage provider for scheme: {scheme}")
