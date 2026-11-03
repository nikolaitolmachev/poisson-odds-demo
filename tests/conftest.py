"""Keep tests isolated from user configuration and live websites."""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from app import config as config_module
from app.cache import XGCache

if TYPE_CHECKING:
    from pathlib import Path

CONFIG_VARIABLES = (
    "MINIMAL_MATCHES_TO_COUNT",
    "MODEL_VALUE_DIFFERENCE",
    "PROXY",
    "STATS_FILE",
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate configuration and generated files, and reject real connections."""
    for name in CONFIG_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "__file__", str(tmp_path / "app" / "config.py"))

    def reject_external_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject_external_call)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_external_call)
    monkeypatch.setattr(socket, "create_connection", reject_external_call)


@pytest.fixture
def xg_snapshot() -> XGCache:
    """Supply distinct venue rates and baselines for orchestration tests."""
    return XGCache(
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        {"Home": (3.2, 2.8)},
        {"Away": (2.7, 3.1)},
        {"home": 3.3, "away": 3.0},
    )
