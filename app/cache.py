"""Read validated xG snapshots and replace their JSON cache atomically."""

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
    """A complete, validated snapshot with its original UTC retrieval time."""

    retrieved_at: datetime
    home: XGData
    away: XGData

    def is_fresh(self, now: datetime) -> bool:
        """Return whether the snapshot is less than CACHE_TTL hours old.

        Args:
            now: Current timezone-aware UTC time.
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


def read_xg_cache(minimal_matches_to_count: int) -> XGCache | None:
    """Read a usable snapshot, including expired data for possible fallback.

    Args:
        minimal_matches_to_count: Required sample size for the current run.

    Returns:
        A validated snapshot, or None for unreadable, invalid or mismatched data.
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
    if home is None or away is None:
        return None
    return XGCache(retrieved_at, home, away)


def save_xg_cache(home: XGData, away: XGData, minimal_matches_to_count: int) -> None:
    """Atomically save complete, valid xG data in the application directory.

    Invalid datasets are not cached. No parent directories are created.
    Temporary-file cleanup is best effort and does not hide write errors.

    Args:
        home: Home xG pairs after team-name normalization.
        away: Away xG pairs after team-name normalization.
        minimal_matches_to_count: Sample size used to retrieve both datasets.

    Raises:
        OSError: Cache creation, writing or replacement failed; the caller may continue
            using the freshly retrieved data.
    """
    if _validate_dataset(home) is None or _validate_dataset(away) is None:
        return
    path = _application_directory() / CACHE_FILENAME
    payload = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "minimal_matches_to_count": minimal_matches_to_count,
        "home": home,
        "away": away,
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
