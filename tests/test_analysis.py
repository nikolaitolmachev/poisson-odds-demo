"""Check scoring strategies, value boundaries, and saved market comparisons."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal, Poisson

from app.analysis import (
    analyze_match_odds,
    calculate_difference_between_odds,
    calculate_minimum_bookmaker_odds,
    calculate_quality_mean,
    calculate_quality_normalized,
)
from app.config import Config
from app.models import NHLMatch, Team

if TYPE_CHECKING:
    from pathlib import Path


def test_quality_strategies_keep_full_precision() -> None:
    """Estimate scoring rates without rounding before the Poisson calculation."""
    assert calculate_quality_mean(2.1234, 3.4567) == pytest.approx(2.79005, abs=1e-12)
    assert calculate_quality_normalized(3.2, 2.8, 3.3) == pytest.approx(
        2.715151515151515, abs=1e-12
    )
    with pytest.raises(ZeroDivisionError):
        calculate_quality_normalized(3.2, 2.8, 0.0)


@pytest.mark.parametrize(
    "model, book, expected",
    [(2.0, 2.6, 30.0), (2.0, 1.5, -25.0), (1.418, 2.11, 48.801)],
)
def test_odds_difference(model: float, book: float, expected: float) -> None:
    """Keep the relative odds difference and its three-decimal precision."""
    assert calculate_difference_between_odds(model, book) == expected


@pytest.mark.parametrize(
    "model, threshold, expected",
    [
        (2.0, 0, "2.00"),
        (1.8, 30, "2.34"),
        (2.25, 30, "2.93"),
        (1.234567, 30, "1.61"),
        (1.000003, 30, "1.30"),
    ],
)
def test_minimum_odds_meet_the_rounded_threshold(
    model: float, threshold: int, expected: str
) -> None:
    """Round upward to cents while preserving the existing percentage boundary."""
    minimum = calculate_minimum_bookmaker_odds(model, threshold)
    assert f"{minimum:.2f}" == expected
    assert calculate_difference_between_odds(model, float(minimum)) >= threshold
    assert calculate_difference_between_odds(model, float(minimum - Decimal("0.01"))) < threshold


@pytest.mark.parametrize("book, qualifies", [(2.59998, False), (2.599999, True), (2.6, True)])
def test_value_threshold_is_inclusive_after_rounding(
    book: float, qualifies: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Accept a rounded boundary without changing the underlying value formula."""
    model = Mock(spec=Poisson)
    model.moneyline = BookmakerMoneyline(2.0, 4.0, 2.0)
    match = NHLMatch(
        "2026-11-01", Team("Home"), Team("Away"), "url", BookmakerMoneyline(book, 4.0, 2.0)
    )
    config = Config(stats_file=tmp_path / "stats.txt")
    analyze_match_odds(match, model, config)
    output = capsys.readouterr().out
    assert ("*****Potential value found!*****" in output) == qualifies
    assert ("Minimum odds (1X2, 1): @2.60" in output) == qualifies
    assert config.stats_file.exists() == qualifies


@pytest.mark.parametrize("market", ["moneyline", "handicap", "total"])
def test_market_reports_identify_qualifying_sides(
    market: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep comparisons and minimum odds identical in the console and appended report."""
    model = Mock(spec=Poisson)
    model.moneyline = BookmakerMoneyline(1.8, 2.25, 3.1)
    model.calculate_handicap_odds_by_poisson.return_value = {
        -1.5: BookmakerHandicap(-1.5, 1.8, 2.25)
    }
    model.calculate_total_odds_by_poisson.return_value = {6.5: BookmakerTotal(6.5, 1.8, 2.25)}
    match = NHLMatch("2026-11-01", Team("Montréal"), Team("Away"), "url", None)
    if market == "moneyline":
        match.bookmaker_moneyline = BookmakerMoneyline(2.5, 3.2, 4.2)
        expected = [
            "Minimum odds (1X2, 1): @2.34",
            "Minimum odds (1X2, X): @2.93",
            "Minimum odds (1X2, 2): @4.03",
        ]
        differences = ["38.889", "42.222", "35.484"]
    elif market == "handicap":
        match.bookmaker_handicaps = [BookmakerHandicap(-1.5, 2.5, 1.55)]
        expected = ["Minimum odds (Handicap -1.5, home): @2.34"]
        differences = ["38.889", "-31.111"]
    else:
        match.bookmaker_totals = [BookmakerTotal(6.5, 2.5, 3.0)]
        expected = [
            "Minimum odds (Total 6.5, Over): @2.34",
            "Minimum odds (Total 6.5, Under): @2.93",
        ]
        differences = ["38.889", "33.333"]
    config = Config(stats_file=tmp_path / "stats.txt")
    config.stats_file.write_text("Previous report\n", encoding="utf-8")
    analyze_match_odds(match, model, config)
    output = capsys.readouterr().out
    assert [line for line in output.splitlines() if line.startswith("Minimum odds")] == expected
    assert output.count("*****Potential value found!*****") == 1
    comparison = next(line for line in output.splitlines() if "Odds difference =" in line)
    for difference in differences:
        assert difference in comparison
    assert "\t" not in comparison
    assert config.stats_file.read_text(encoding="utf-8") == (
        f"Previous report\n{match}\n{comparison}\n" + "\n".join(expected) + "\n" + "=" * 100 + "\n"
    )


def test_multiple_lines_keep_order_and_skip_unavailable_model_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report all qualifying lines without substituting odds for missing model markets."""
    model = Mock(spec=Poisson)
    model.moneyline = BookmakerMoneyline(2.0, 4.0, 2.0)
    model.calculate_handicap_odds_by_poisson.return_value = {
        -1.5: BookmakerHandicap(-1.5, 2.0, 2.0),
        0.0: BookmakerHandicap(0.0, 2.0, 2.0),
    }
    match = NHLMatch(
        "2026-11-01",
        Team("Home"),
        Team("Away"),
        "url",
        None,
        [
            BookmakerHandicap(-1.5, 2.6, 2.0),
            BookmakerHandicap(0.0, 2.0, 2.8),
            BookmakerHandicap(9.5, 4.0, 4.0),
        ],
    )
    config = Config(stats_file=tmp_path / "stats.txt")
    analyze_match_odds(match, model, config)
    output = capsys.readouterr().out
    expected = [
        "Minimum odds (Handicap -1.5, home): @2.60",
        "Minimum odds (Handicap 0.0, away): @2.60",
    ]
    assert [line for line in output.splitlines() if line.startswith("Minimum odds")] == expected
    assert "9.5" not in output
    assert "No bookmaker odds are available for the moneyline market." in output
    assert "No bookmaker odds are available for the total market." in output
    assert output.count("*****Potential value found!*****") == 2


def test_integer_poisson_markets_account_for_push_probability() -> None:
    """Smoke-test the current integer-line API, whose fair odds exclude returned stakes."""
    model = Poisson(3.0, 2.5)
    handicap = model.calculate_handicap_odds_by_poisson()[0.0]
    expected_home = (1 - 1 / model.moneyline.odds_draw) * model.moneyline.odds_home
    assert handicap.odds_home == pytest.approx(expected_home, abs=0.0005)
    totals = model.calculate_total_odds_by_poisson()
    under_win = 1 / totals[5.5].odds_under
    over_win = 1 / totals[6.5].odds_over
    assert totals[6.0].odds_under == pytest.approx((under_win + over_win) / under_win, abs=0.0005)
