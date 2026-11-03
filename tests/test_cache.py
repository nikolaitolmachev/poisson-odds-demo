"""Check complete cache snapshots, refreshes, and nonfatal filesystem failures."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from requests.exceptions import Timeout

from app import cache, cli
from app.config import Config

if TYPE_CHECKING:
    from pathlib import Path

    from app.cache import XGCache


@pytest.fixture
def refresh(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, AsyncMock, AsyncMock]:
    """Mock the four source requests while exercising real cache orchestration."""
    session = Mock()
    session.close = AsyncMock()
    rates = AsyncMock(side_effect=[{"St Louis Blues": (3.2, 2.8)}, {"Away": (2.7, 3.1)}])
    averages = AsyncMock(side_effect=[3.3, 3.0])
    monkeypatch.setattr(cli, "AsyncHTMLSession", Mock(return_value=session))
    monkeypatch.setattr(cli, "get_xg_data_for_all_teams", rates)
    monkeypatch.setattr(cli, "get_league_average_xg", averages)
    return session, rates, averages


def test_cache_round_trip_restores_tuples(tmp_path: Path, xg_snapshot: XGCache) -> None:
    """Persist both datasets and baselines together and invalidate a changed sample size."""
    cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
    payload = json.loads((tmp_path / cache.CACHE_FILENAME).read_text())
    assert payload["home"]["Home"] == [3.2, 2.8]
    loaded = cache.read_xg_cache(3)
    assert loaded is not None
    assert loaded.home == xg_snapshot.home
    assert loaded.away == xg_snapshot.away
    assert loaded.league_averages == xg_snapshot.league_averages
    assert isinstance(loaded.home["Home"], tuple)
    assert loaded.is_fresh(datetime.now(timezone.utc))
    assert cache.read_xg_cache(4) is None


def test_cache_expires_at_twelve_hours(xg_snapshot: XGCache) -> None:
    """Keep the exact TTL boundary and reject timestamps in the future."""
    retrieved = xg_snapshot.retrieved_at
    assert xg_snapshot.is_fresh(retrieved + timedelta(hours=12, microseconds=-1))
    assert not xg_snapshot.is_fresh(retrieved + timedelta(hours=12))
    assert not xg_snapshot.is_fresh(retrieved - timedelta(seconds=1))


@pytest.mark.parametrize("invalid", ["json", "dataset", "baselines", "timestamp"])
def test_invalid_cache_is_a_miss(invalid: str, tmp_path: Path, xg_snapshot: XGCache) -> None:
    """Reject corrupt, incomplete, or invalid snapshots instead of serving partial data."""
    assert cache.read_xg_cache(3) is None
    cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
    path = tmp_path / cache.CACHE_FILENAME
    payload = json.loads(path.read_text())
    if invalid == "dataset":
        payload["home"] = {"Home": [-1, 2.8]}
    elif invalid == "baselines":
        del payload["league_averages"]
    elif invalid == "timestamp":
        payload["retrieved_at"] = "not a timestamp"
    path.write_text("{" if invalid == "json" else json.dumps(payload))
    assert cache.read_xg_cache(3) is None


def test_complete_refresh_is_normalized_and_reused_without_http(
    refresh: tuple[Mock, AsyncMock, AsyncMock], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetch all four datasets once, then use the complete fresh cache without a session."""
    session, rates, averages = refresh
    loaded = asyncio.run(cli.load_xgs(Config(), False))
    assert loaded.home == {"St. Louis Blues": (3.2, 2.8)}
    assert loaded.away == {"Away": (2.7, 3.1)}
    assert loaded.league_averages == {"home": 3.3, "away": 3.0}
    assert rates.await_count == averages.await_count == 2
    session.close.assert_awaited_once()
    path = tmp_path / cache.CACHE_FILENAME
    original = path.read_bytes()
    factory = Mock(side_effect=AssertionError("Fresh cache must avoid HTTP"))
    monkeypatch.setattr(cli, "AsyncHTMLSession", factory)
    cached = asyncio.run(cli.load_xgs(Config(), True))
    assert cached.home == loaded.home
    assert cached.retrieved_at == cache.read_xg_cache(3).retrieved_at
    factory.assert_not_called()
    assert path.read_bytes() == original


@pytest.mark.parametrize("failure", [Timeout("source unavailable"), ValueError("invalid table")])
def test_failed_refresh_uses_stale_data_without_rewriting_it(
    failure: Exception,
    refresh: tuple[Mock, AsyncMock, AsyncMock],
    tmp_path: Path,
    xg_snapshot: XGCache,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Retain the original data and UTC timestamp after an unsuccessful refresh."""
    cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
    path = tmp_path / cache.CACHE_FILENAME
    payload = json.loads(path.read_text())
    payload["retrieved_at"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    path.write_text(json.dumps(payload))
    original = path.read_bytes()
    session, rates, _ = refresh
    rates.side_effect = failure
    loaded = asyncio.run(cli.load_xgs(Config(), False))
    assert loaded.home == xg_snapshot.home
    assert loaded.away == xg_snapshot.away
    assert loaded.league_averages == xg_snapshot.league_averages
    assert loaded.retrieved_at.isoformat() == payload["retrieved_at"]
    assert path.read_bytes() == original
    assert payload["retrieved_at"] in capsys.readouterr().out
    session.close.assert_awaited_once()


def test_failed_refresh_without_cache_preserves_error(
    refresh: tuple[Mock, AsyncMock, AsyncMock],
) -> None:
    """Propagate a source failure when no valid fallback is available."""
    session, rates, _ = refresh
    rates.side_effect = Timeout("source unavailable")
    with pytest.raises(Timeout, match="source unavailable"):
        asyncio.run(cli.load_xgs(Config(), False))
    session.close.assert_awaited_once()


def test_failed_cache_replacement_keeps_fresh_data_and_old_file(
    refresh: tuple[Mock, AsyncMock, AsyncMock],
    tmp_path: Path,
    xg_snapshot: XGCache,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warn without retrying requests or corrupting the cache when atomic replacement fails."""
    cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
    path = tmp_path / cache.CACHE_FILENAME
    original = path.read_bytes()
    monkeypatch.setattr(cli, "read_xg_cache", lambda _: None)
    monkeypatch.setattr(cache.os, "replace", Mock(side_effect=PermissionError("read only")))
    loaded = asyncio.run(cli.load_xgs(Config(), False))
    assert loaded.home == {"St. Louis Blues": (3.2, 2.8)}
    assert loaded.league_averages == {"home": 3.3, "away": 3.0}
    assert refresh[1].await_count == refresh[2].await_count == 2
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    assert capsys.readouterr().out == (
        "Warning: Unable to save xG cache. Continuing with fresh data.\n"
    )


def test_incomplete_refresh_does_not_replace_valid_cache(
    refresh: tuple[Mock, AsyncMock, AsyncMock], tmp_path: Path, xg_snapshot: XGCache
) -> None:
    """Keep a complete snapshot when a newly fetched raw dataset fails."""
    cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
    path = tmp_path / cache.CACHE_FILENAME
    original = path.read_bytes()
    refresh[2].side_effect = ValueError("raw statistics missing")
    # A changed sample size prevents using the old data as a fallback.
    with pytest.raises(ValueError, match="raw statistics missing"):
        asyncio.run(cli.load_xgs(Config(minimal_matches_to_count=4), False))
    assert path.read_bytes() == original


def test_cleanup_failure_does_not_mask_the_original_write_error(
    tmp_path: Path,
    xg_snapshot: XGCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the original error and existing cache when writing and cleanup both fail."""
    cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
    path = tmp_path / cache.CACHE_FILENAME
    original = path.read_bytes()
    write_error = OSError("disk full")
    with monkeypatch.context() as patch:
        patch.setattr(cache.json, "dump", Mock(side_effect=write_error))
        patch.setattr(type(path), "unlink", Mock(side_effect=PermissionError("cleanup denied")))
        with pytest.raises(OSError) as caught:
            cache.save_xg_cache(xg_snapshot.home, xg_snapshot.away, 3, xg_snapshot.league_averages)
        assert caught.value is write_error
        assert path.read_bytes() == original
    for temporary in tmp_path.glob(".xg_cache-*.tmp"):
        temporary.unlink()
