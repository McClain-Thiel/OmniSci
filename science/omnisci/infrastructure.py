# SPDX-License-Identifier: Apache-2.0
"""Application-level compute and storage connector configuration."""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import yaml

from omnisci.errors import StateError

CONFIG_ENV_VAR = "OMNISCI_INFRASTRUCTURE_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".omnisci" / "infrastructure.yaml"
DEFAULT_COMPUTE_CONFIG = {"default_provider": "local", "providers": {}}
DEFAULT_STORAGE_CONFIG = {
    "default_provider": "local",
    "allowed_roots": [],
    "providers": {},
}


class InfrastructureConfigStore:
    """Persist reusable connector definitions outside individual projects."""

    def __init__(self, path: str | Path | None = None):
        configured_path = path or os.environ.get(CONFIG_ENV_VAR)
        self.path = Path(configured_path).expanduser() if configured_path else DEFAULT_CONFIG_PATH

    def load(self) -> dict:
        if not self.path.is_file():
            return self._defaults()
        raw = yaml.safe_load(self.path.read_text()) or {}
        if not isinstance(raw, dict):
            raise StateError(f"invalid OmniSci infrastructure config: {self.path}")
        version = raw.get("version", 1)
        if version != 1:
            raise StateError(f"unsupported OmniSci infrastructure config version: {version}")
        compute_config = raw.get("compute_config", DEFAULT_COMPUTE_CONFIG)
        storage_config = raw.get("storage_config", DEFAULT_STORAGE_CONFIG)
        self._validate_config(compute_config, "compute")
        self._validate_config(storage_config, "storage")
        return {
            "version": 1,
            "compute_config": copy.deepcopy(compute_config),
            "storage_config": copy.deepcopy(storage_config),
        }

    def update(
        self,
        *,
        compute_config: dict | None = None,
        storage_config: dict | None = None,
    ) -> dict:
        if compute_config is None and storage_config is None:
            raise ValueError("infrastructure update requires compute_config or storage_config")
        current = self.load()
        if compute_config is not None:
            self._validate_config(compute_config, "compute")
            current["compute_config"] = copy.deepcopy(compute_config)
        if storage_config is not None:
            self._validate_config(storage_config, "storage")
            current["storage_config"] = copy.deepcopy(storage_config)
        self._write(current)
        return current

    @classmethod
    def _validate_config(cls, config: object, kind: str) -> None:
        if not isinstance(config, dict):
            raise TypeError(f"{kind}_config must be a mapping")
        providers = config.get("providers") or {}
        if not isinstance(providers, dict):
            raise StateError(f"{kind}_config.providers must be a mapping")
        for provider_id, provider_config in providers.items():
            if not isinstance(provider_id, str) or not provider_id:
                raise StateError(f"{kind} provider ids must be non-empty strings")
            if not isinstance(provider_config, dict):
                raise StateError(f"{kind} provider config must be a mapping: {provider_id}")
            cls._reject_inline_credentials(
                provider_config,
                f"{kind}_config.providers.{provider_id}",
            )
            transport_ref = provider_config.get("transport_ref")
            if transport_ref is not None and transport_ref not in providers:
                raise StateError(
                    f"{kind} provider '{provider_id}' references missing "
                    f"transport: {transport_ref}"
                )
        default_provider = config.get("default_provider", "local")
        if not isinstance(default_provider, str) or not default_provider:
            raise StateError(f"{kind}_config.default_provider must be a provider id")
        if default_provider != "local" and default_provider not in providers:
            raise StateError(f"default {kind} provider is not configured: {default_provider}")
        if kind == "storage":
            allowed_roots = config.get("allowed_roots", [])
            if not isinstance(allowed_roots, list) or not all(
                isinstance(root, str) for root in allowed_roots
            ):
                raise StateError("storage_config.allowed_roots must be a list of paths")

    @classmethod
    def _reject_inline_credentials(cls, value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold().replace("-", "_")
                is_reference = normalized.endswith(("_profile", "_ref", "_reference"))
                if not is_reference and any(
                    fragment in normalized
                    for fragment in ("password", "secret", "token", "access_key", "credential")
                ):
                    raise StateError(
                        f"inline credentials are not allowed in app configuration: {path}.{key}"
                    )
                cls._reject_inline_credentials(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._reject_inline_credentials(child, f"{path}[{index}]")

    def _write(self, config: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(self.path)

    @staticmethod
    def _defaults() -> dict:
        return {
            "version": 1,
            "compute_config": copy.deepcopy(DEFAULT_COMPUTE_CONFIG),
            "storage_config": copy.deepcopy(DEFAULT_STORAGE_CONFIG),
        }
