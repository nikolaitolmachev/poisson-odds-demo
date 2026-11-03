"""Exercise CLI strategies, report generation, and recoverable match failures."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from poisson_odds import BookmakerMoneyline, Poisson
from requests.exceptions import Timeout

from app import cli
from app.config import Config
from app.models import NHLMatch, Team
from app.scrapers.betexplorer import BetExplorerScraper

if TYPE_CHECKING:
    from pathlib import Path

    from app.cache import XGCache

URL = "https://www.betexplorer.com/hockey/usa/nhl/home-away/AbCd1234/"


@pytest.fixture
def scraper(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock the external match source while retaining CLI session ownership."""
    source = MagicMock(spec=BetExplorerScraper)
    source.__enter__.return_value = source
    source.__exit__.return_value = False
    source.get_upcoming_match_urls.return_value = [URL, URL.replace("AbCd1234", "EfGh5678")]
    source.get_match.side_effect = lambda url: NHLMatch(
        "2026-11-01 19:00",
        Team("Home"),
        Team("Away"),
        url,
        BookmakerMoneyline(10.0, 10.0, 10.0),
    )
    monkeypatch.setattr(cli, "BetExplorerScraper", Mock(return_value=source))
    return source


@pytest.mark.parametrize("selection, mode", [("", "2"), ("2", "1")])
def test_cli_runs_selected_strategy_and_saves_report(
    selection: str,
    mode: str,
    scraper: MagicMock,
    xg_snapshot: XGCache,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run both menu modes through real scoring, Poisson markets, and report persistence."""
    answers = iter(["n", selection, mode, *([URL] if mode == "2" else []), "exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    fetch = AsyncMock(return_value=xg_snapshot)
    monkeypatch.setattr(cli, "load_xgs", fetch)
    poisson = Mock(wraps=Poisson)
    monkeypatch.setattr(cli, "Poisson", poisson)
    assert cli.run() == 0
    output = capsys.readouterr().out
    report = (tmp_path / "stats.txt").read_text(encoding="utf-8")
    if selection == "":
        assert "Quality calculation strategy: Mean (arithmetic average)" in output
        expected = ((3.2 + 3.1) / 2, (2.7 + 2.8) / 2)
    else:
        assert "Quality calculation strategy: Normalized (league-adjusted)" in output
        expected = (3.2 * 3.1 / 3.3, 2.7 * 2.8 / 3.0)
    matches = 1 if mode == "2" else 2
    assert poisson.call_count == scraper.get_match.call_count == matches
    for invocation in poisson.call_args_list:
        assert invocation.args == expected
    assert "*****Potential value found!*****" in output
    assert "Minimum odds (1X2, 1): @" in report
    assert report.count("2026-11-01 19:00 Home (3.20|2.80) - Away (2.70|3.10).") == matches
    fetch.assert_awaited_once()
    scraper.__enter__.assert_called_once()
    scraper.__exit__.assert_called_once()
    if mode == "1":
        scraper.get_upcoming_match_urls.assert_called_once()
    else:
        scraper.get_upcoming_match_urls.assert_not_called()


@pytest.mark.parametrize(
    "averages", [None, {"home": 0.0, "away": 3.0}, {"home": 3.3, "away": float("nan")}]
)
@pytest.mark.usefixtures("scraper")
def test_normalized_requires_positive_finite_baselines(
    averages: dict[str, float] | None,
    xg_snapshot: XGCache,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject unavailable baselines without falling back to Mean or constructing a model."""
    poisson = Mock()
    monkeypatch.setattr(cli, "Poisson", poisson)
    assert not cli.analyze_single_match(
        URL,
        Config(),
        xg_snapshot.home,
        xg_snapshot.away,
        strategy="normalized",
        league_averages=averages,
    )
    poisson.assert_not_called()
    assert "Valid league-average xG data is required" in capsys.readouterr().out


def test_all_matches_continues_after_network_failure(
    scraper: MagicMock,
    xg_snapshot: XGCache,
    tmp_path: Path,
) -> None:
    """Reuse one session and process the next match after a recoverable request failure."""
    match = NHLMatch(
        "2026-11-01", Team("Home"), Team("Away"), URL, BookmakerMoneyline(10.0, 10.0, 10.0)
    )
    scraper.get_match.side_effect = [Timeout("unavailable"), match]
    assert not cli.analyze_all_matches(Config(), xg_snapshot.home, xg_snapshot.away)
    assert scraper.get_match.call_count == 2
    assert (tmp_path / "stats.txt").is_file()
    scraper.__enter__.assert_called_once()
    scraper.__exit__.assert_called_once()


def test_empty_upcoming_list_is_successful(scraper: MagicMock) -> None:
    """Treat a successful empty fixture response as a normal result."""
    scraper.get_upcoming_match_urls.return_value = []
    assert cli.analyze_all_matches(Config(), {}, {})
    scraper.get_match.assert_not_called()


def test_fixture_request_failure_returns_unsuccessful(scraper: MagicMock) -> None:
    """Keep a failed fixture request distinct from a successful empty list."""
    scraper.get_upcoming_match_urls.side_effect = Timeout("source unavailable")
    assert not cli.analyze_all_matches(Config(), {}, {})
    scraper.get_match.assert_not_called()
    scraper.__exit__.assert_called_once()


def test_missing_bookmaker_markets_have_a_distinct_message(
    scraper: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report an empty market response without blaming xG data or a network error."""
    scraper.get_match.side_effect = None
    scraper.get_match.return_value = NHLMatch("2026-11-01", Team("Home"), Team("Away"), URL, None)
    assert not cli.analyze_single_match(URL, Config(), {}, {})
    assert "No bookmaker odds are available for this NHL match." in capsys.readouterr().out


@pytest.mark.usefixtures("scraper")
def test_missing_xg_prevents_calculation(
    xg_snapshot: XGCache,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report a single nonpositive xG field without passing it to Poisson."""
    poisson = Mock()
    monkeypatch.setattr(cli, "Poisson", poisson)
    assert not cli.analyze_single_match(URL, Config(), {"Home": (3.2, 0.0)}, xg_snapshot.away)
    poisson.assert_not_called()
    assert "Insufficient xG data for the home team." in capsys.readouterr().out


@pytest.mark.parametrize(
    "url, valid",
    [
        (URL + "?market=ah#odds", True),
        (URL.replace("www.betexplorer.com", "example.com"), False),
        (URL.replace("/hockey/usa/nhl/", "/football/"), False),
        ("https://www.betexplorer.com/hockey/usa/nhl/", False),
    ],
)
def test_match_url_domain_and_path(url: str, valid: bool) -> None:
    """Keep the essential domain and NHL path checks without restricting URL extras."""
    assert cli._is_match_url(url) is valid


def test_invalid_strategy_retries_and_allows_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Handle invalid strategy input without loading data or trapping the user."""
    answers = iter(["n", "invalid", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    load = AsyncMock()
    monkeypatch.setattr(cli, "load_xgs", load)
    assert cli.run() == 0
    assert "Invalid quality calculation strategy." in capsys.readouterr().out
    load.assert_not_called()


def test_invalid_configuration_returns_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return a nonzero exit status for invalid configuration before prompting."""
    (tmp_path / ".env").write_text("MINIMAL_MATCHES_TO_COUNT=0\n")
    assert cli.run() == 1
    assert "Invalid configuration:" in capsys.readouterr().out
