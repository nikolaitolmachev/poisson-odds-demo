"""Parse Natural Stat Trick statistics and league baselines entirely offline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from lxml import html
from requests.exceptions import HTTPError

from app import constants
from app.config import Config

# noinspection PyProtectedMember
from app.scrapers.natural_stat_trick import (
    _parse_raw_xg_data,
    _parse_toi_minutes,
    _parse_xg_data,
    get_league_average_xg,
    get_xg_data_for_all_teams,
    normalize_team_names,
)

if TYPE_CHECKING:
    from typing import Literal


@pytest.fixture
def rates_html() -> str:
    """Read a small table with eligible teams and one below the games threshold."""
    return (Path(__file__).parent / "fixtures/xg_teams.html").read_text(encoding="utf-8")


@pytest.fixture
def raw_html(rates_html: str) -> str:
    """Use distinct raw counts and unequal ice times to expose unweighted averages."""
    tree = html.fromstring(rates_html)
    for row, toi, xgf, xga in zip(
        tree.xpath('//table[@id="teams"]/tbody/tr'),
        ("794:46", "120:00", "60:00"),
        (40.0, 8.0, 2.0),
        (35.0, 7.0, 1.0),
    ):
        cells = row.xpath("./td")
        cells[3].text, cells[22].text, cells[23].text = toi, str(xgf), str(xga)
    return html.tostring(tree, encoding="unicode")


def make_session(document: str) -> Mock:
    """Supply an awaitable response without making HTTP requests."""
    response = Mock(html=SimpleNamespace(html=document))
    session = Mock()
    session.get = AsyncMock(return_value=response)
    return session


@pytest.mark.parametrize("side, location", [("home", "H"), ("away", "A")])
def test_rates_use_correct_venue_headers_and_minimum_games(
    rates_html: str,
    side: Literal["home", "away"],
    location: str,
) -> None:
    """Read the required columns and keep proxy configuration local to each request."""
    session = make_session(rates_html)
    session.proxies = {"https": "http://original:8080"}
    result = asyncio.run(
        get_xg_data_for_all_teams(
            session,
            Config(proxy="http://proxy:8080"),
            side=side,
            use_proxy=True,
        )
    )
    assert result == {"St Louis Blues": (2.5, 2.4), "Utah Hockey Club": (2.3, 2.2)}
    session.get.assert_awaited_once_with(
        constants.XG_SITE_NHL_RATES + location,
        headers={
            "User-agent": constants.USER_AGENT,
            "Referer": constants.XG_REFERER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        proxies={"https": "http://proxy:8080"},
    )
    session.get.return_value.raise_for_status.assert_called_once()
    assert session.proxies == {"https": "http://original:8080"}
    assert _parse_xg_data(rates_html, 4) == {"Utah Hockey Club": (2.3, 2.2)}


def test_name_normalization_copies_only_the_current_alias() -> None:
    """Rename St Louis Blues while preserving other names and the input dictionary."""
    data = {
        "St Louis Blues": (2.5, 2.4),
        "Utah Hockey Club": (2.3, 2.2),
        "Boston Bruins": (3.0, 2.0),
    }
    original = data.copy()
    normalized = normalize_team_names(data)
    assert normalized == {
        "St. Louis Blues": (2.5, 2.4),
        "Utah Hockey Club": (2.3, 2.2),
        "Boston Bruins": (3.0, 2.0),
    }
    assert data == original
    assert normalized is not data


def test_raw_columns_and_toi_preserve_seconds(raw_html: str) -> None:
    """Extract raw xGF, xGA, and time on ice without a minimum-games filter."""
    assert _parse_toi_minutes("794:46") == pytest.approx(794 + 46 / 60)
    assert _parse_raw_xg_data(raw_html) == [
        (40.0, 35.0, 794 + 46 / 60),
        (8.0, 7.0, 120.0),
        (2.0, 1.0, 60.0),
    ]


@pytest.mark.parametrize("side, location", [("home", "H"), ("away", "A")])
def test_league_average_is_toi_weighted_and_includes_all_teams(
    raw_html: str,
    side: Literal["home", "away"],
    location: str,
) -> None:
    """Compute a venue baseline from raw sums, including teams below the games minimum."""
    if side == "away":
        raw_html = raw_html.replace(">40.0<", ">20.0<")
    session = make_session(raw_html)
    result = asyncio.run(
        get_league_average_xg(
            session,
            Config(minimal_matches_to_count=100),
            side=side,
        )
    )
    expected = (50 if side == "home" else 30) / (794 + 46 / 60 + 120 + 60) * 60
    assert result == pytest.approx(expected, rel=1e-14)
    assert session.get.await_args.args == (constants.XG_SITE_NHL_RAW + location,)


@pytest.mark.parametrize("value", ["NaN", "-1.0"])
def test_invalid_xg_is_not_returned_as_partial_data(rates_html: str, value: str) -> None:
    """Reject nonfinite or negative xG values in an otherwise valid table."""
    with pytest.raises(ValueError, match="Non-finite or negative xG"):
        _parse_xg_data(rates_html.replace("2.50", value), 3)


def test_missing_statistics_and_zero_toi_are_errors(raw_html: str) -> None:
    """Make missing tables and unusable league denominators explicit failures."""
    with pytest.raises(ValueError, match="Expected one"):
        _parse_xg_data("<html>Statistics unavailable</html>", 3)
    tree = html.fromstring(raw_html)
    for cell in tree.xpath('//table[@id="teams"]/tbody/tr/td[4]'):
        cell.text = "0:00"
    session = make_session(html.tostring(tree, encoding="unicode"))
    with pytest.raises(ValueError, match="total TOI"):
        asyncio.run(get_league_average_xg(session, Config()))


def test_http_error_is_checked_before_parsing() -> None:
    """Do not interpret a failed HTTP response as an empty statistics dataset."""
    session = make_session("not a statistics table")
    session.get.return_value.raise_for_status.side_effect = HTTPError("403 Forbidden")
    with pytest.raises(HTTPError, match="403"):
        asyncio.run(get_xg_data_for_all_teams(session, Config()))
