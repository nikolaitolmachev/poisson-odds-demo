"""Read validated xG data and save it atomically to a JSON cache."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from app.config import _application_directory

if TYPE_CHECKING:
    from app.scrapers.natural_stat_trick import XGData

CACHE_TTL_HOURS = 12
CACHE_FILENAME = "xg_cache.json"


@dataclass(frozen=True)
class XGCache:
    """Home and away xG rates, league baselines, and their retrieval time.

    Cache loading and saving validate the data; direct construction does not.

    Attributes:
        retrieved_at: Original retrieval time in UTC.
        home: Home (xGF/60, xGA/60) pairs keyed by normalized team name.
        away: Away (xGF/60, xGA/60) pairs keyed by normalized team name.
        league_averages: TOI-weighted league xGF/60 baselines for home and away.
    """

    retrieved_at: datetime
    home: XGData
    away: XGData
    league_averages: dict[str, float]

    def is_fresh(self, now: datetime) -> bool:
        """Return whether the snapshot is less than CACHE_TTL_HOURS hours old.

        Args:
            now: Current timezone-aware UTC time.

        Returns:
            True if the age is nonnegative and below the cache time limit.
        """
        return timedelta(0) <= now - self.retrieved_at < timedelta(hours=CACHE_TTL_HOURS)


def _validate_dataset(data: object) -> XGData | None:
    """Restore finite, nonnegative xG pairs without accepting booleans as numbers."""
    if not isinstance(data, dict) or not data:
        return None
    result: XGData = {}
    for name, pair in data.items():
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        if any(type(value) not in (int, float) for value in pair):
            return None
        try:
            xgf, xga = float(pair[0]), float(pair[1])
        except OverflowError:
            return None
        if not all(math.isfinite(value) and value >= 0 for value in (xgf, xga)):
            return None
        result[name] = (xgf, xga)
    return result


def _validate_league_averages(data: object) -> dict[str, float] | None:
    """Require both finite, nonnegative venue baselines without accepting booleans."""
    if not isinstance(data, dict):
        return None
    result: dict[str, float] = {}
    for side in ("home", "away"):
        value = data.get(side)
        if type(value) not in (int, float):
            return None
        try:
            value = float(value)
        except OverflowError:
            return None
        if not math.isfinite(value) or value < 0:
            return None
        result[side] = value
    return result


def read_xg_cache(minimal_matches_to_count: int) -> XGCache | None:
    """Read a usable snapshot, including expired data for possible fallback.

    Args:
        minimal_matches_to_count: Required sample size for the current run.

    Returns:
        A validated snapshot, or None if the data is unreadable, invalid, or
        uses a different minimum number of games, or lacks valid league baselines.
    """
    path = _application_directory() / CACHE_FILENAME
    try:
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    minimum = payload.get("minimal_matches_to_count")
    if type(minimum) is not int or minimum != minimal_matches_to_count:
        return None
    timestamp = payload.get("retrieved_at")
    if not isinstance(timestamp, str):
        return None
    try:
        retrieved_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if retrieved_at.utcoffset() != timedelta(0):
        return None
    if retrieved_at > datetime.now(timezone.utc):
        return None
    home = _validate_dataset(payload.get("home"))
    away = _validate_dataset(payload.get("away"))
    league_averages = _validate_league_averages(payload.get("league_averages"))
    if home is None or away is None or league_averages is None:
        return None
    return XGCache(retrieved_at, home, away, league_averages)


def save_xg_cache(
    home: XGData, away: XGData, minimal_matches_to_count: int, league_averages: dict[str, float]
) -> None:
    """Atomically save complete, valid xG data in the application directory.

    Invalid datasets are not cached. No parent directories are created.
    Temporary-file cleanup is best effort and does not hide write errors.

    Args:
        home: Home xG pairs after team-name normalization.
        away: Away xG pairs after team-name normalization.
        minimal_matches_to_count: Minimum number of games used to filter both datasets.
        league_averages: Complete home and away baselines from the same retrieval.

    Raises:
        OSError: Cache creation, writing, or replacement failed. The caller may continue
            using the freshly retrieved data.
    """
    if (
        _validate_dataset(home) is None
        or _validate_dataset(away) is None
        or _validate_league_averages(league_averages) is None
    ):
        return
    path = _application_directory() / CACHE_FILENAME
    payload = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "minimal_matches_to_count": minimal_matches_to_count,
        "home": home,
        "away": away,
        "league_averages": league_averages,
    }
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".xg_cache-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass  # Preserve the original write error if cleanup also fails.
