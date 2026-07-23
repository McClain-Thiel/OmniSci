from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_app_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OMNISCI_INFRASTRUCTURE_CONFIG",
        str(tmp_path / "app-config" / "infrastructure.yaml"),
    )
