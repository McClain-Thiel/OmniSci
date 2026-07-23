# SPDX-License-Identifier: Apache-2.0
"""Local filesystem storage provider (spec §13.2).

- Accepts ``file://`` URIs and project-relative paths.
- Restricts access to project-approved roots (default: the project dir).
- SHA-256 checksums on write/stat.
- Atomic file replacement for writes (tmp file + ``os.replace``).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from omnisci.errors import NotFoundError, StateError
from omnisci.storage.base import ObjectMetadata, ObjectPage

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalStorageProvider:
    def __init__(self, project_dir: Path, allowed_roots: list[str] | None = None):
        self.project_dir = Path(project_dir).resolve()
        roots = [self.project_dir]
        for root in allowed_roots or []:
            roots.append(Path(root).expanduser().resolve())
        self._roots = roots

    # -- path resolution ------------------------------------------------------

    def _resolve(self, uri: str) -> Path:
        if uri.startswith("file://"):
            raw = uri[len("file://") :]
            p = Path(raw)
            if not p.is_absolute():
                p = self.project_dir / raw
        else:
            p = Path(uri)
            if not p.is_absolute():
                p = self.project_dir / uri
        rp = p.resolve()
        if not any(rp == root or rp.is_relative_to(root) for root in self._roots):
            raise StateError(f"path outside approved storage roots: {uri}")
        return rp

    def _metadata(self, path: Path, checksum: bool = True) -> ObjectMetadata:
        st = path.stat()
        return ObjectMetadata(
            uri=f"file://{path}",
            size_bytes=st.st_size,
            checksum_sha256=sha256_file(path) if checksum and path.is_file() else None,
            content_type=mimetypes.guess_type(str(path))[0],
            modified_at=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        )

    # -- protocol -------------------------------------------------------------

    def schemes(self) -> set[str]:
        return {"file"}

    def list(self, uri: str, _cursor: str | None = None) -> ObjectPage:
        path = self._resolve(uri)
        if not path.exists():
            raise NotFoundError(f"no such path: {uri}")
        if path.is_file():
            return ObjectPage(objects=[self._metadata(path, checksum=False)])
        objects = []
        for child in sorted(path.iterdir()):
            if child.is_dir():
                objects.append(
                    ObjectMetadata(uri=f"file://{child}", content_type="inode/directory")
                )
            else:
                objects.append(self._metadata(child, checksum=False))
        return ObjectPage(objects=objects, next_cursor=None)

    def stat(self, uri: str) -> ObjectMetadata:
        path = self._resolve(uri)
        if not path.exists():
            raise NotFoundError(f"no such path: {uri}")
        return self._metadata(path)

    def open_read(self, uri: str, byte_range=None) -> BinaryIO:
        path = self._resolve(uri)
        if not path.is_file():
            raise NotFoundError(f"no such file: {uri}")
        fh = open(path, "rb")  # noqa: SIM115 -- ownership passes to the caller
        if byte_range is not None:
            start, end = byte_range
            fh.seek(start)
            if end is not None:
                return _RangedReader(fh, end - start + 1)
        return fh

    def put(self, uri: str, stream: BinaryIO, checksum: str | None = None) -> ObjectMetadata:
        path = self._resolve(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                for chunk in iter(lambda: stream.read(_CHUNK), b""):
                    h.update(chunk)
                    tmp.write(chunk)
            digest = h.hexdigest()
            if checksum is not None and checksum != digest:
                raise StateError(f"checksum mismatch on put: expected {checksum}, got {digest}")
            os.replace(tmp_name, path)  # atomic replacement
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return self._metadata(path)

    def copy(self, source: str, destination: str) -> ObjectMetadata:
        src = self._resolve(source)
        if not src.is_file():
            raise NotFoundError(f"no such file: {source}")
        with open(src, "rb") as fh:
            return self.put(destination, fh, None)

    def delete(self, uri: str) -> None:
        path = self._resolve(uri)
        if not path.exists():
            raise NotFoundError(f"no such path: {uri}")
        if path.is_dir():
            raise StateError(f"refusing to delete a directory: {uri}")
        path.unlink()

    def presign_read(self, uri: str, _ttl_seconds: int) -> str:
        # Local provider has no signed URLs; the resolved file URI is returned.
        path = self._resolve(uri)
        if not path.exists():
            raise NotFoundError(f"no such path: {uri}")
        return f"file://{path}"

    def presign_write(self, uri: str, _ttl_seconds: int) -> str:
        path = self._resolve(uri)
        return f"file://{path}"


class _RangedReader:
    def __init__(self, fh: BinaryIO, remaining: int):
        self._fh = fh
        self._remaining = remaining

    def read(self, n: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if n < 0 or n > self._remaining:
            n = self._remaining
        data = self._fh.read(n)
        self._remaining -= len(data)
        return data

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
