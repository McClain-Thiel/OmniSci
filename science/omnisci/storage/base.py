# SPDX-License-Identifier: Apache-2.0
"""Storage provider protocol (spec §13.1, verbatim shape)."""

from __future__ import annotations

from typing import BinaryIO, Protocol

from pydantic import BaseModel, Field

from omnisci.domain.schemas import utcnow


class ObjectMetadata(BaseModel):
    uri: str
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    etag: str | None = None
    content_type: str | None = None
    modified_at: str | None = None


class ObjectPage(BaseModel):
    objects: list[ObjectMetadata] = Field(default_factory=list)
    next_cursor: str | None = None


class StoredObject(BaseModel):
    """Returned by ``open_read`` wrappers that want to carry metadata."""

    metadata: ObjectMetadata
    opened_at: str = Field(default_factory=utcnow)


class StorageProvider(Protocol):
    def schemes(self) -> set[str]: ...
    def list(self, uri: str, cursor: str | None) -> ObjectPage: ...
    def stat(self, uri: str) -> ObjectMetadata: ...
    def open_read(self, uri: str, byte_range=None) -> BinaryIO: ...
    def put(self, uri: str, stream: BinaryIO, checksum: str | None) -> ObjectMetadata: ...
    def copy(self, source: str, destination: str) -> ObjectMetadata: ...
    def delete(self, uri: str) -> None: ...
    def presign_read(self, uri: str, ttl_seconds: int) -> str: ...
    def presign_write(self, uri: str, ttl_seconds: int) -> str: ...
