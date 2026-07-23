# SPDX-License-Identifier: Apache-2.0
"""Local storage provider unit tests (spec §13.2)."""

from __future__ import annotations

import hashlib
import io

import pytest
from omnisci.errors import NotFoundError, StateError
from omnisci.storage.local import LocalStorageProvider


@pytest.fixture
def store(tmp_path):
    (tmp_path / "data").mkdir()
    return LocalStorageProvider(tmp_path)


def test_put_stat_checksum(store):
    payload = b"hello science\n"
    meta = store.put("data/a.txt", io.BytesIO(payload), None)
    assert meta.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert meta.size_bytes == len(payload)

    st = store.stat("data/a.txt")
    assert st.checksum_sha256 == meta.checksum_sha256

    with store.open_read("data/a.txt") as fh:
        assert fh.read() == payload


def test_put_checksum_mismatch_rejected(store):
    with pytest.raises(StateError, match="checksum mismatch"):
        store.put("data/b.txt", io.BytesIO(b"x"), "0" * 64)
    assert not (store.project_dir / "data" / "b.txt").exists()


def test_atomic_replace(store):
    store.put("data/c.txt", io.BytesIO(b"v1"), None)
    meta = store.put("data/c.txt", io.BytesIO(b"v2-longer"), None)
    with store.open_read("data/c.txt") as fh:
        assert fh.read() == b"v2-longer"
    assert meta.size_bytes == len(b"v2-longer")
    # no temp files left behind
    assert not list(store.project_dir.glob("data/.tmp-*"))


def test_file_uri_and_relative_path(store):
    store.put("data/d.txt", io.BytesIO(b"z"), None)
    meta = store.stat(f"file://{store.project_dir}/data/d.txt")
    assert meta.size_bytes == 1
    meta2 = store.stat("file://data/d.txt")  # project-relative file URI
    assert meta2.size_bytes == 1


def test_list(store):
    store.put("data/one.txt", io.BytesIO(b"1"), None)
    store.put("data/two.txt", io.BytesIO(b"22"), None)
    page = store.list("data", None)
    names = sorted(o.uri.rsplit("/", 1)[-1] for o in page.objects)
    assert names == ["one.txt", "two.txt"]
    assert page.next_cursor is None


def test_escape_outside_root_rejected(store):
    with pytest.raises(StateError, match="outside approved storage roots"):
        store.put("../evil.txt", io.BytesIO(b"x"), None)
    with pytest.raises(StateError, match="outside approved storage roots"):
        store.stat("/etc/hostname")


def test_allowed_extra_root(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    store = LocalStorageProvider(project, allowed_roots=[str(extra)])
    meta = store.put(f"file://{extra}/ok.txt", io.BytesIO(b"ok"), None)
    assert meta.size_bytes == 2


def test_missing_raises_not_found(store):
    with pytest.raises(NotFoundError):
        store.stat("data/missing.txt")


def test_copy_and_delete(store):
    store.put("data/src.txt", io.BytesIO(b"copy me"), None)
    meta = store.copy("data/src.txt", "data/dst.txt")
    assert meta.size_bytes == 7
    store.delete("data/dst.txt")
    with pytest.raises(NotFoundError):
        store.stat("data/dst.txt")
