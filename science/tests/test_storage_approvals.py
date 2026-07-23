# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from io import BytesIO

import pytest
from omnisci.errors import ApprovalRequiredError, StateError
from omnisci.service import ScienceService
from omnisci.storage.base import ObjectMetadata, ObjectPage


class FakeRemoteStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def list(self, uri: str, _cursor: str | None = None) -> ObjectPage:
        return ObjectPage(
            objects=[ObjectMetadata(uri=key) for key in self.objects if key.startswith(uri)]
        )

    def stat(self, uri: str) -> ObjectMetadata:
        return ObjectMetadata(uri=uri, size_bytes=len(self.objects[uri]))

    def open_read(self, uri: str, _byte_range=None):
        return BytesIO(self.objects[uri])

    def put(self, uri: str, stream, _checksum=None) -> ObjectMetadata:
        payload = stream.read()
        self.objects[uri] = payload
        return ObjectMetadata(uri=uri, size_bytes=len(payload))

    def presign_read(self, uri: str, _ttl_seconds: int) -> str:
        return f"https://read.invalid/{uri}"

    def presign_write(self, uri: str, _ttl_seconds: int) -> str:
        return f"https://write.invalid/{uri}"


@pytest.fixture
def remote_service(tmp_path):
    service = ScienceService.init_project(tmp_path / "project")
    service.storage_config["providers"] = {
        "s3": {
            "allowed_prefixes": ["science/results/", "science/inputs/"],
            "allow_write": True,
        }
    }
    remote = FakeRemoteStorage()
    service.storage_providers["s3"] = remote
    (service.project_dir / "result.json").write_text('{"ok": true}\n')
    return service, remote


def test_storage_write_prefix_approval_is_reusable_within_prefix(remote_service):
    service, remote = remote_service
    first_uri = "s3://science/results/first.json"

    with pytest.raises(ApprovalRequiredError) as required:
        service.storage_put("result.json", first_uri)

    approval = service.get_approval(required.value.approval_id)
    assert approval.action == "storage.write:s3"
    assert approval.scope == "s3://science/results/"
    service.resolve_approval(
        approval.id,
        "approved",
        actor="researcher",
        scope_kind="prefix",
    )

    service.storage_put("result.json", first_uri)
    service.storage_put("result.json", "s3://science/results/second.json")

    assert remote.objects[first_uri] == b'{"ok": true}\n'
    assert len(service.list_approvals()) == 1


def test_storage_read_and_write_actions_are_independent(remote_service):
    service, remote = remote_service
    uri = "s3://science/inputs/cohort.csv"
    remote.objects[uri] = b"sample,value\na,1\n"

    with pytest.raises(ApprovalRequiredError) as read_required:
        service.storage_stat(uri)
    read_approval = service.get_approval(read_required.value.approval_id)
    assert read_approval.action == "storage.read:s3"
    assert read_approval.scope == "s3://science/inputs/"

    with pytest.raises(ApprovalRequiredError) as write_required:
        service.storage_put("result.json", "s3://science/inputs/derived.json")
    write_approval = service.get_approval(write_required.value.approval_id)
    assert write_approval.action == "storage.write:s3"
    assert write_approval.id != read_approval.id


def test_one_time_storage_approval_is_consumed(remote_service):
    service, _remote = remote_service
    uri = "s3://science/results/once.json"
    with pytest.raises(ApprovalRequiredError) as required:
        service.storage_put("result.json", uri)
    service.resolve_approval(required.value.approval_id, "approved", actor="researcher")

    service.storage_put("result.json", uri)
    assert service.get_approval(required.value.approval_id).consumed_at is not None
    with pytest.raises(ApprovalRequiredError):
        service.storage_put("result.json", uri)


def test_revoked_prefix_approval_requires_a_new_approval(remote_service):
    service, _remote = remote_service
    uri = "s3://science/results/revoked.json"
    with pytest.raises(ApprovalRequiredError) as required:
        service.storage_put("result.json", uri)
    approval = service.resolve_approval(
        required.value.approval_id,
        "approved",
        actor="researcher",
        scope_kind="prefix",
    )

    service.storage_put("result.json", uri)
    revoked = service.revoke_approval(
        approval.id,
        actor="researcher",
        reason="Dataset access was withdrawn",
    )

    assert revoked.decision.value == "revoked"
    assert revoked.revoked_by == "researcher"
    assert revoked.revoked_at is not None
    with pytest.raises(ApprovalRequiredError) as new_required:
        service.storage_put("result.json", "s3://science/results/after-revoke.json")
    assert new_required.value.approval_id != approval.id


def test_consumed_one_time_approval_cannot_be_revoked(remote_service):
    service, _remote = remote_service
    uri = "s3://science/results/consumed.json"
    with pytest.raises(ApprovalRequiredError) as required:
        service.storage_put("result.json", uri)
    approval = service.resolve_approval(
        required.value.approval_id,
        "approved",
        actor="researcher",
    )
    service.storage_put("result.json", uri)

    with pytest.raises(StateError, match="already consumed"):
        service.revoke_approval(approval.id, actor="researcher")
