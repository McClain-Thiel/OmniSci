# SPDX-License-Identifier: Apache-2.0
"""Shared exceptions. CLI maps these to stable exit codes (see README)."""


class ScienceError(Exception):
    """Base class for all omnisci runtime failures. Exit code 1."""


class NotFoundError(ScienceError):
    """Requested object does not exist. Exit code 3."""


class StateError(ScienceError):
    """Operation not allowed in the current state. Exit code 1."""


class ApprovalRequiredError(ScienceError):
    """A semantic approval must be resolved before retrying an operation."""

    def __init__(self, approval_id: str, action: str, message: str):
        super().__init__(message)
        self.approval_id = approval_id
        self.action = action
        self.message = message

    def payload(self) -> dict[str, str]:
        return {
            "status": "approval_required",
            "approval_id": self.approval_id,
            "action": self.action,
            "message": self.message,
        }
