"""Check model identity and append-only UTF-8 reports."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from poisson_odds import BookmakerTotal

from app.models import NHLMatch, Team

if TYPE_CHECKING:
    from pathlib import Path


def test_models_use_identity_and_independent_market_lists() -> None:
    """Keep mutable match markets independent and avoid value-based model equality."""
    first = NHLMatch("2026-11-01", Team("Home"), Team("Away"), "url", None)
    second = NHLMatch("2026-11-01", Team("Home"), Team("Away"), "url", None)
    assert first != second
    assert first.team_a != second.team_a
    first.bookmaker_totals.append(BookmakerTotal(6.0, 2.0, 2.0))
    assert second.bookmaker_totals == []
    assert first.bookmaker_handicaps is not second.bookmaker_handicaps


def test_report_appends_utf8_with_current_team_format(tmp_path: Path) -> None:
    """Preserve existing content, two-decimal xG rates, and report separators."""
    match = NHLMatch("2026-11-01", Team("Montréal", 2.5, 2.4), Team("Away"), "url", None)
    path = tmp_path / "selected.txt"
    path.write_bytes("Earlier report\n".encode("utf-8"))
    match.save_to_stats("First", path)
    match.save_to_stats("Second", path)
    header = "2026-11-01 Montréal (2.50|2.40) - Away."
    expected = f"{header}\nFirst\n{header}\nSecond\n"
    assert path.read_bytes() == b"Earlier report\n" + expected.replace("\n", os.linesep).encode(
        "utf-8"
    )
