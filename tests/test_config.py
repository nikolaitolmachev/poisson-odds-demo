"""Check configuration defaults, precedence, validation, and application paths."""

from __future__ import annotations

import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.config import Config, ConfigurationError, load_config


def test_missing_env_uses_frozen_defaults(tmp_path: Path) -> None:
    """Load safe defaults without creating report files or directories."""
    config = load_config()
    assert config.minimal_matches_to_count == 3
    assert config.model_value_difference == 30
    assert config.proxy == ""
    assert config.stats_file == tmp_path / "stats.txt"
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(FrozenInstanceError):
        setattr(config, "model_value_difference", 10)


def test_environment_overrides_dotenv_without_mutating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve explicit zero thresholds and give process variables priority."""
    (tmp_path / ".env").write_text(
        "MINIMAL_MATCHES_TO_COUNT=4\nMODEL_VALUE_DIFFERENCE=30\nSTATS_FILE=selected.txt\n"
    )
    monkeypatch.setenv("MODEL_VALUE_DIFFERENCE", "0")
    config = load_config()
    assert config.minimal_matches_to_count == 4
    assert config.model_value_difference == 0
    assert config.stats_file == tmp_path / "selected.txt"
    assert "MINIMAL_MATCHES_TO_COUNT" not in os.environ


@pytest.mark.parametrize(
    "assignment",
    [
        "MINIMAL_MATCHES_TO_COUNT=0",
        "MODEL_VALUE_DIFFERENCE=invalid",
        'STATS_FILE="unterminated',
        "STATS_FILE=${OTHER}",
    ],
)
def test_invalid_configuration_cannot_fall_back_to_defaults(
    assignment: str,
    tmp_path: Path,
) -> None:
    """Reject invalid values, malformed syntax, and unsupported interpolation."""
    (tmp_path / ".env").write_text(assignment, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config()


@pytest.mark.parametrize("frozen", [False, True])
def test_configuration_paths_do_not_depend_on_working_directory(
    frozen: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anchor dotenv and relative reports to the source root or executable directory."""
    executable = tmp_path / "executable"
    executable.mkdir()
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable / "demo.exe"))
    app_dir = executable if frozen else tmp_path
    (app_dir / ".env").write_text("STATS_FILE=reports/selected.txt\n")
    elsewhere = tmp_path / "working-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_config().stats_file == app_dir / "reports/selected.txt"
    assert Config(stats_file=Path("direct.txt")).stats_file == app_dir / "direct.txt"
    absolute = tmp_path / "absolute.txt"
    assert Config(stats_file=absolute).stats_file == absolute
    assert not (app_dir / "reports").exists()
    assert list(elsewhere.iterdir()) == []


def test_proxy_credentials_are_hidden_in_repr_and_errors() -> None:
    """Keep accepted proxies intact while rejecting invalid ports without leaking secrets."""
    proxy = "http://user:secret@localhost:8080"
    config = Config(proxy=proxy)
    assert config.proxy == proxy
    assert "secret" not in repr(config)
    with pytest.raises(ConfigurationError) as caught:
        Config(proxy="http://user:secret@localhost:99999")
    assert "secret" not in str(caught.value)
