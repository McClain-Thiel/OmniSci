# SPDX-License-Identifier: Apache-2.0
"""S3 storage provider (spec §13.3).

- Supports ``s3://bucket/key`` URIs.
- Optional ``boto3`` dependency; imported lazily so the package starts without it.
- ``endpoint_url`` enables S3-compatible stores (R2, MinIO, institutional).
- Configurable bucket allowlist and ``bucket/prefix`` prefix restrictions.
- Read-only by default; writes require ``allow_write=True``.
- Credentials come from the standard boto3 chain. Credential material and
  presigned-URL query strings are never logged.
- Records SHA-256 (from the S3 checksum field, or computed on write) separately
  from ETag. Multipart ETags remain opaque metadata and are never treated as
  content hashes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import mimetypes
import tempfile
from typing import BinaryIO

from omnisci.domain.schemas import utcnow
from omnisci.errors import NotFoundError, StateError
from omnisci.storage.base import ObjectMetadata, ObjectPage

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024
# Payloads up to this size are uploaded from memory; larger ones spool to disk.
_SPOOL_THRESHOLD = 16 * 1024 * 1024


def parse_s3_uri(uri: str, *, require_key: bool = True) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``."""
    if not uri.startswith("s3://"):
        raise StateError(f"not an s3 URI: {uri}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise StateError(f"s3 URI missing bucket: {uri}")
    if require_key and not key:
        raise StateError(f"s3 URI missing object key: {uri}")
    return bucket, key


def _response_hashes(resp: dict) -> tuple[str | None, str | None]:
    """Return ``(sha256, etag)`` without conflating the two values."""
    digest = None
    sha = resp.get("ChecksumSHA256")
    if sha:
        try:
            decoded = base64.b64decode(sha, validate=True)
            if len(decoded) == hashlib.sha256().digest_size:
                digest = decoded.hex()
        except (binascii.Error, ValueError):
            pass
    if digest is None:
        metadata_sha = (resp.get("Metadata") or {}).get("sha256")
        if isinstance(metadata_sha, str):
            try:
                decoded = bytes.fromhex(metadata_sha)
            except ValueError:
                decoded = b""
            if len(decoded) == hashlib.sha256().digest_size:
                digest = metadata_sha.lower()
    etag = (resp.get("ETag") or "").strip('"') or None
    return digest, etag


class S3StorageProvider:
    def __init__(
        self,
        *,
        allowed_buckets: list[str] | None = None,
        allowed_prefixes: list[str] | None = None,
        allow_write: bool = False,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client=None,
    ):
        self._allowed_buckets = set(allowed_buckets) if allowed_buckets is not None else None
        self._allowed_prefixes = list(allowed_prefixes) if allowed_prefixes is not None else None
        self._allow_write = allow_write
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._client = client

    # -- policy ---------------------------------------------------------------

    def _check(self, bucket: str, key: str, *, write: bool) -> None:
        if self._allowed_buckets is not None and bucket not in self._allowed_buckets:
            raise StateError(f"bucket not in storage allowlist: {bucket}")
        if self._allowed_prefixes is not None and not any(
            b == bucket and (not prefix or key.startswith(prefix))
            for b, _, prefix in (entry.partition("/") for entry in self._allowed_prefixes)
        ):
            raise StateError(f"key outside allowed prefixes: s3://{bucket}/{key}")
        if write and not self._allow_write:
            raise StateError("storage provider is read-only; writes are disabled")

    def _read(self, uri: str, *, require_key: bool = True) -> tuple[str, str]:
        bucket, key = parse_s3_uri(uri, require_key=require_key)
        self._check(bucket, key, write=False)
        return bucket, key

    def _write(self, uri: str) -> tuple[str, str]:
        bucket, key = parse_s3_uri(uri)
        self._check(bucket, key, write=True)
        return bucket, key

    # -- client ---------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise StateError(
                    "boto3 is required for the s3 storage provider; "
                    "install the optional s3 dependency"
                ) from exc
            self._client = boto3.client(
                "s3", endpoint_url=self._endpoint_url, region_name=self._region_name
            )
        return self._client

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        return code in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}

    def _metadata(self, bucket: str, key: str, resp: dict) -> ObjectMetadata:
        modified = resp.get("LastModified")
        checksum_sha256, etag = _response_hashes(resp)
        return ObjectMetadata(
            uri=f"s3://{bucket}/{key}",
            size_bytes=resp.get("ContentLength", resp.get("Size")),
            checksum_sha256=checksum_sha256,
            etag=etag,
            content_type=resp.get("ContentType") or mimetypes.guess_type(key)[0],
            modified_at=modified.isoformat() if modified is not None else None,
        )

    # -- protocol -------------------------------------------------------------

    def schemes(self) -> set[str]:
        return {"s3"}

    def list(self, uri: str, cursor: str | None = None) -> ObjectPage:
        bucket, prefix = self._read(uri, require_key=False)
        params: dict = {"Bucket": bucket, "Prefix": prefix}
        if cursor is not None:
            params["ContinuationToken"] = cursor
        log.debug("s3 list bucket=%s prefix=%s", bucket, prefix)
        resp = self._get_client().list_objects_v2(**params)
        objects = []
        for entry in resp.get("Contents", []):
            checksum_sha256, etag = _response_hashes(entry)
            objects.append(
                ObjectMetadata(
                    uri=f"s3://{bucket}/{entry['Key']}",
                    size_bytes=entry.get("Size"),
                    checksum_sha256=checksum_sha256,
                    etag=etag,
                    content_type=mimetypes.guess_type(entry["Key"])[0],
                    modified_at=(
                        entry["LastModified"].isoformat() if entry.get("LastModified") else None
                    ),
                )
            )
        next_cursor = resp.get("NextContinuationToken") if resp.get("IsTruncated") else None
        return ObjectPage(objects=objects, next_cursor=next_cursor)

    def stat(self, uri: str) -> ObjectMetadata:
        bucket, key = self._read(uri)
        log.debug("s3 head bucket=%s key=%s", bucket, key)
        try:
            resp = self._get_client().head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                raise NotFoundError(f"no such object: {uri}") from exc
            raise
        return self._metadata(bucket, key, resp)

    def open_read(self, uri: str, byte_range=None) -> BinaryIO:
        bucket, key = self._read(uri)
        params: dict = {"Bucket": bucket, "Key": key}
        if byte_range is not None:
            start, end = byte_range
            params["Range"] = f"bytes={start}-{'' if end is None else end}"
        log.debug("s3 get bucket=%s key=%s range=%s", bucket, key, params.get("Range"))
        try:
            resp = self._get_client().get_object(**params)
        except Exception as exc:
            if self._is_not_found(exc):
                raise NotFoundError(f"no such object: {uri}") from exc
            raise
        return resp["Body"]

    def put(self, uri: str, stream: BinaryIO, checksum: str | None = None) -> ObjectMetadata:
        bucket, key = self._write(uri)
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_THRESHOLD) as spool:
            h = hashlib.sha256()
            for chunk in iter(lambda: stream.read(_CHUNK), b""):
                h.update(chunk)
                spool.write(chunk)
            digest = h.hexdigest()
            if checksum is not None and checksum != digest:
                raise StateError(f"checksum mismatch on put: expected {checksum}, got {digest}")
            spool.seek(0, 2)
            size = spool.tell()
            spool.seek(0)
            body = spool.read() if size <= _SPOOL_THRESHOLD else spool
            log.debug("s3 put bucket=%s key=%s size=%d", bucket, key, size)
            resp = self._get_client().put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ChecksumSHA256=base64.b64encode(h.digest()).decode(),
                ContentType=mimetypes.guess_type(key)[0] or "application/octet-stream",
                Metadata={"sha256": digest},
            )
        meta = self._metadata(bucket, key, resp)
        meta.size_bytes = size
        meta.checksum_sha256 = digest
        meta.modified_at = utcnow()
        return meta

    def copy(self, source: str, destination: str) -> ObjectMetadata:
        src_bucket, src_key = self._read(source)
        dst_bucket, dst_key = self._write(destination)
        log.debug(
            "s3 copy src_bucket=%s src_key=%s dst_bucket=%s dst_key=%s",
            src_bucket,
            src_key,
            dst_bucket,
            dst_key,
        )
        try:
            resp = self._get_client().copy_object(
                Bucket=dst_bucket,
                Key=dst_key,
                CopySource={"Bucket": src_bucket, "Key": src_key},
            )
        except Exception as exc:
            if self._is_not_found(exc):
                raise NotFoundError(f"no such object: {source}") from exc
            raise
        result = resp.get("CopyObjectResult", {})
        checksum_sha256, etag = _response_hashes(result)
        return ObjectMetadata(
            uri=f"s3://{dst_bucket}/{dst_key}",
            checksum_sha256=checksum_sha256,
            etag=etag,
            content_type=mimetypes.guess_type(dst_key)[0],
            modified_at=(
                result["LastModified"].isoformat() if result.get("LastModified") else None
            ),
        )

    def delete(self, uri: str) -> None:
        bucket, key = self._write(uri)
        log.debug("s3 delete bucket=%s key=%s", bucket, key)
        self._get_client().delete_object(Bucket=bucket, Key=key)

    def presign_read(self, uri: str, ttl_seconds: int) -> str:
        bucket, key = self._read(uri)
        # Log the target and TTL only; the signed URL carries credentials.
        log.debug("s3 presign_read bucket=%s key=%s ttl=%d", bucket, key, ttl_seconds)
        return self._get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )

    def presign_write(self, uri: str, ttl_seconds: int) -> str:
        bucket, key = self._write(uri)
        log.debug("s3 presign_write bucket=%s key=%s ttl=%d", bucket, key, ttl_seconds)
        return self._get_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
