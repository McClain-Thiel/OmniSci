# SPDX-License-Identifier: Apache-2.0
"""S3 storage provider unit tests (spec §13.3, §20).

Uses botocore's Stubber only — no network, no moto. The module is skipped
when the optional ``boto3`` dependency is not installed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest

boto3 = pytest.importorskip("boto3")

from botocore.config import Config  # noqa: E402
from botocore.response import StreamingBody  # noqa: E402
from botocore.stub import Stubber  # noqa: E402
from omnisci.errors import NotFoundError, StateError  # noqa: E402
from omnisci.storage import registry  # noqa: E402
from omnisci.storage.s3 import S3StorageProvider, parse_s3_uri  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ACCESS_KEY = "AKIAFAKEEXAMPLEKEY"
SECRET_KEY = "fake-secret-key-material"


def make_client():
    # Explicit dummy credentials so no real credential chain is consulted and
    # the redaction test has material to look for. No network is ever touched.
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        # Pin sigv4 so assertions don't depend on ambient AWS config.
        config=Config(signature_version="s3v4"),
    )


@pytest.fixture
def provider():
    p = S3StorageProvider(client=make_client())
    with Stubber(p._client) as stub:
        yield p, stub
        stub.assert_no_pending_responses()


@pytest.fixture
def write_provider():
    p = S3StorageProvider(client=make_client(), allow_write=True)
    with Stubber(p._client) as stub:
        yield p, stub
        stub.assert_no_pending_responses()


# -- URI parsing ---------------------------------------------------------------


def test_parse_s3_uri():
    assert parse_s3_uri("s3://bucket/path/to/obj.txt") == ("bucket", "path/to/obj.txt")
    assert parse_s3_uri("s3://bucket/", require_key=False) == ("bucket", "")


def test_parse_s3_uri_rejects_bad_input():
    with pytest.raises(StateError, match="not an s3 URI"):
        parse_s3_uri("file:///tmp/x")
    with pytest.raises(StateError, match="missing bucket"):
        parse_s3_uri("s3://")
    with pytest.raises(StateError, match="missing object key"):
        parse_s3_uri("s3://bucket/")


# -- bucket/prefix policy -------------------------------------------------------


def test_bucket_allowlist_denies_other_buckets():
    p = S3StorageProvider(client=make_client(), allowed_buckets=["data"])
    with pytest.raises(StateError, match="allowlist"):
        p.stat("s3://other-bucket/x.txt")


def test_prefix_policy_allows_and_denies():
    p = S3StorageProvider(
        client=make_client(),
        allowed_prefixes=["data/raw/", "data/processed/"],
    )
    with pytest.raises(StateError, match="outside allowed prefixes"):
        p.stat("s3://data/public/x.txt")
    with pytest.raises(StateError, match="outside allowed prefixes"):
        p.stat("s3://elsewhere/raw/x.txt")

    with Stubber(p._client) as stub:
        stub.add_response(
            "head_object",
            {"ContentLength": 1, "LastModified": NOW},
            {"Bucket": "data", "Key": "raw/x.txt"},
        )
        meta = p.stat("s3://data/raw/x.txt")
        assert meta.size_bytes == 1


def test_unrestricted_by_default(provider):
    p, stub = provider
    stub.add_response(
        "head_object",
        {"ContentLength": 2, "LastModified": NOW},
        {"Bucket": "anything", "Key": "any/key"},
    )
    assert p.stat("s3://anything/any/key").size_bytes == 2


# -- read-only enforcement ------------------------------------------------------


def test_read_only_by_default(provider):
    p, _stub = provider  # no responses queued: a write must fail before any call
    with pytest.raises(StateError, match="read-only"):
        p.put("s3://data/a.txt", io.BytesIO(b"x"), None)
    with pytest.raises(StateError, match="read-only"):
        p.delete("s3://data/a.txt")
    with pytest.raises(StateError, match="read-only"):
        p.copy("s3://data/a.txt", "s3://data/b.txt")
    with pytest.raises(StateError, match="read-only"):
        p.presign_write("s3://data/a.txt", 60)


# -- stubbed round trips --------------------------------------------------------


def test_list_with_cursor(provider):
    p, stub = provider
    stub.add_response(
        "list_objects_v2",
        {
            "Contents": [
                {
                    "Key": "raw/a.txt",
                    "Size": 3,
                    "LastModified": NOW,
                    "ETag": f'"{hashlib.md5(b"abc").hexdigest()}"',
                },
            ],
            "IsTruncated": True,
            "NextContinuationToken": "tok-2",
        },
        {"Bucket": "data", "Prefix": "raw/"},
    )
    page = p.list("s3://data/raw/", None)
    assert page.next_cursor == "tok-2"
    assert page.objects[0].uri == "s3://data/raw/a.txt"
    assert page.objects[0].size_bytes == 3
    assert page.objects[0].checksum_sha256 is None
    assert page.objects[0].etag == hashlib.md5(b"abc").hexdigest()

    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "raw/b.txt", "Size": 1, "LastModified": NOW}], "IsTruncated": False},
        {"Bucket": "data", "Prefix": "raw/", "ContinuationToken": "tok-2"},
    )
    page2 = p.list("s3://data/raw/", "tok-2")
    assert page2.next_cursor is None
    assert [o.uri for o in page2.objects] == ["s3://data/raw/b.txt"]


def test_stat_records_sha256_and_simple_etag(provider):
    p, stub = provider
    sha = hashlib.sha256(b"payload").digest()
    stub.add_response(
        "head_object",
        {
            "ContentLength": 7,
            "LastModified": NOW,
            "ContentType": "text/plain",
            "ETag": '"deadbeef"',
            "ChecksumSHA256": base64.b64encode(sha).decode(),
        },
        {"Bucket": "data", "Key": "a.txt"},
    )
    meta = p.stat("s3://data/a.txt")
    assert meta.checksum_sha256 == sha.hex()  # SHA-256 wins over the ETag
    assert meta.etag == "deadbeef"
    assert meta.content_type == "text/plain"
    assert meta.modified_at == NOW.isoformat()

    stub.add_response(
        "head_object",
        {
            "ContentLength": 7,
            "LastModified": NOW,
            "ETag": f'"{hashlib.md5(b"payload").hexdigest()}"',
        },
        {"Bucket": "data", "Key": "a.txt"},
    )
    meta2 = p.stat("s3://data/a.txt")
    assert meta2.checksum_sha256 is None
    assert meta2.etag == hashlib.md5(b"payload").hexdigest()


def test_stat_missing_raises_not_found(provider):
    p, stub = provider
    stub.add_client_error("head_object", service_error_code="404")
    with pytest.raises(NotFoundError):
        p.stat("s3://data/nope.txt")


def test_put_stat_round_trip(write_provider):
    p, stub = write_provider
    payload = b"hello s3\n"
    digest = hashlib.sha256(payload).digest()
    stub.add_response(
        "put_object",
        {"ETag": '"ignored"'},
        {
            "Bucket": "data",
            "Key": "raw/a.txt",
            "Body": payload,
            "ChecksumSHA256": base64.b64encode(digest).decode(),
            "ContentType": "text/plain",
            "Metadata": {"sha256": digest.hex()},
        },
    )
    meta = p.put("s3://data/raw/a.txt", io.BytesIO(payload), None)
    assert meta.checksum_sha256 == digest.hex()
    assert meta.size_bytes == len(payload)

    stub.add_response(
        "get_object",
        {
            "Body": StreamingBody(io.BytesIO(payload), len(payload)),
            "ContentLength": len(payload),
            "LastModified": NOW,
        },
        {"Bucket": "data", "Key": "raw/a.txt"},
    )
    with p.open_read("s3://data/raw/a.txt") as fh:
        assert fh.read() == payload


def test_stat_uses_persisted_sha256_metadata(provider):
    p, stub = provider
    digest = hashlib.sha256(b"payload").hexdigest()
    stub.add_response(
        "head_object",
        {
            "ContentLength": 7,
            "LastModified": NOW,
            "ContentType": "application/json",
            "Metadata": {"sha256": digest},
        },
        {"Bucket": "data", "Key": "result.json"},
    )

    meta = p.stat("s3://data/result.json")

    assert meta.checksum_sha256 == digest
    assert meta.content_type == "application/json"


def test_put_checksum_mismatch_rejected(write_provider):
    p, _stub = write_provider  # nothing queued: mismatch must fail pre-upload
    with pytest.raises(StateError, match="checksum mismatch"):
        p.put("s3://data/b.txt", io.BytesIO(b"x"), "0" * 64)


def test_open_read_byte_range(provider):
    p, stub = provider
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(b"2345"), 4), "ContentLength": 4, "LastModified": NOW},
        {"Bucket": "data", "Key": "r.bin", "Range": "bytes=2-5"},
    )
    with p.open_read("s3://data/r.bin", (2, 5)) as fh:
        assert fh.read() == b"2345"


def test_copy(write_provider):
    p, stub = write_provider
    etag = hashlib.md5(b"copied").hexdigest()
    stub.add_response(
        "copy_object",
        {"CopyObjectResult": {"ETag": f'"{etag}"', "LastModified": NOW}},
        {"Bucket": "data", "Key": "b.txt", "CopySource": {"Bucket": "data", "Key": "a.txt"}},
    )
    meta = p.copy("s3://data/a.txt", "s3://data/b.txt")
    assert meta.uri == "s3://data/b.txt"
    assert meta.checksum_sha256 is None
    assert meta.etag == etag


def test_delete(write_provider):
    p, stub = write_provider
    stub.add_response("delete_object", {}, {"Bucket": "data", "Key": "gone.txt"})
    p.delete("s3://data/gone.txt")


# -- presign --------------------------------------------------------------------


def test_presign_read_and_write(write_provider):
    p, _ = write_provider
    url = p.presign_read("s3://data/a.txt", 300)
    parts = urlsplit(url)
    assert parts.netloc.startswith("data.s3.")
    assert parts.path == "/a.txt"
    assert "X-Amz-Expires=300" in parts.query
    assert "X-Amz-Signature=" in parts.query

    wurl = p.presign_write("s3://data/b.txt", 60)
    assert "X-Amz-Expires=60" in urlsplit(wurl).query


# -- multipart ETag (spec §20 acceptance) ---------------------------------------


def test_multipart_etag_is_not_a_content_hash(provider):
    p, stub = provider
    stub.add_response(
        "head_object",
        {"ContentLength": 10, "LastModified": NOW, "ETag": '"5d41402abc4b2a76b9719d911017c592-3"'},
        {"Bucket": "data", "Key": "big.bin"},
    )
    meta = p.stat("s3://data/big.bin")
    assert meta.checksum_sha256 is None
    assert meta.etag == "5d41402abc4b2a76b9719d911017c592-3"


def test_list_multipart_etag_is_not_a_content_hash(provider):
    p, stub = provider
    stub.add_response(
        "list_objects_v2",
        {
            "Contents": [
                {"Key": "big.bin", "Size": 10, "LastModified": NOW, "ETag": '"abc123-12"'}
            ],
            "IsTruncated": False,
        },
        {"Bucket": "data", "Prefix": ""},
    )
    page = p.list("s3://data", None)
    assert page.objects[0].checksum_sha256 is None
    assert page.objects[0].etag == "abc123-12"


# -- credential redaction (spec §13.3) -------------------------------------------


def test_logs_never_contain_credentials_or_signed_query(provider, caplog):
    p, stub = provider
    stub.add_response(
        "head_object",
        {"ContentLength": 1, "LastModified": NOW},
        {"Bucket": "data", "Key": "a.txt"},
    )
    with caplog.at_level(logging.DEBUG, logger="omnisci.storage.s3"):
        p.stat("s3://data/a.txt")
        url = p.presign_read("s3://data/a.txt", 120)

    query = urlsplit(url).query
    assert query  # the URL is genuinely signed, so the check is meaningful
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert ACCESS_KEY not in rendered
    assert SECRET_KEY not in rendered
    assert query not in rendered
    assert "X-Amz-Signature" not in rendered
    assert url not in rendered


# -- registry (spec §13.1) -------------------------------------------------------


def test_registry_group_and_scheme_lookup():
    assert registry.ENTRY_POINT_GROUP == "omnigent_science.storage"
    p = S3StorageProvider(client=make_client())
    assert registry.provider_for_scheme("s3", [p]) is p
    with pytest.raises(StateError, match="no storage provider"):
        registry.provider_for_scheme("gs", [p])


def test_registry_loads_entry_points(monkeypatch):
    class _FakeEP:
        name = "s3"

        def load(self):
            return S3StorageProvider

    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: [_FakeEP()])
    loaded = registry.load_providers()
    assert loaded == {"s3": S3StorageProvider}
