"""Verify the current HTTP scraper, market filters, and bookmaker selection offline."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from requests.exceptions import HTTPError

from app import constants
from app.scrapers.betexplorer import BetExplorerScraper, client
from app.scrapers.betexplorer.matches import parse_match_metadata, parse_match_urls
from app.scrapers.betexplorer.odds import parse_handicaps, parse_totals, select_lines

URL = constants.NHL_LEAGUE + "home-away/AbCd1234/"


def cells(values: tuple[str, ...], inactive: bool = False) -> str:
    """Build a small source-shaped odds row."""
    state = " inactive" if inactive else ""
    return "".join(
        f'<td class="table-main__detail-odds{state}" data-odd="{value}"></td>' for value in values
    )


def line_row(bookmaker: int, line: str, values: tuple[str, ...], inactive: bool = False) -> str:
    """Build one two-outcome market row."""
    return (
        f'<tr data-bid="{bookmaker}">'
        f'<td class="table-main__doubleparameter">{line}</td>'
        f"{cells(values, inactive)}</tr>"
    )


def test_discovery_requires_three_listed_values_and_deduplicates_urls() -> None:
    """Count listed odds without incorrectly claiming numeric validation at discovery."""
    row = (
        f'<tr><td></td><td><a class="in-match" href="{URL}">Match</a></td>'
        '<td class="table-main__odds"><button data-odd="invalid"></button>'
        '<button data-odd="3"></button><button data-odd="4"></button></td></tr>'
    )
    document = '<table class="table-main--leaguefixtures">' + row * 2 + "</table>"
    assert parse_match_urls(document) == [URL]
    assert parse_match_urls(document.replace('data-odd="4"', 'data-odd=""')) == []


def test_metadata_fallback_preserves_known_json_team() -> None:
    """Fill only missing metadata from HTML without replacing a known JSON-LD team."""
    payload = {"@type": "SportsEvent", "homeTeam": {"name": " JSON Home "}}
    document = (
        '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        '<div id="match-date" data-dt="27,10,2026,19,30"></div>'
        '<span class="list-breadcrumb__item__in">HTML Home - HTML Away</span>'
    )
    metadata = parse_match_metadata(document)
    assert (metadata.date, metadata.home, metadata.away) == (
        "2026-10-27 19:30",
        "JSON Home",
        "HTML Away",
    )


@pytest.mark.parametrize("market", ["handicap", "total"])
def test_lines_follow_current_filters_priority_and_sorting(market: str) -> None:
    """Select unique whole and half lines, including the exact minimum odds boundary."""
    first, second = (item[0] for item in constants.BOOKMAKERS[:2])
    rows = "".join(
        [
            line_row(second, "6.5", ("1.9", "2.1")),
            line_row(first, "6.5", ("2.0", "2.0")),
            line_row(second, "5.5", ("1.5", "2.1")),
            line_row(first, "6", ("1.30", "2.0")),
            line_row(first, "7", ("1.29", "2.0")),
            line_row(first, "7.5", ("2.0", "2.0"), inactive=True),
            line_row(first, "6.25", ("2.0", "2.0")),
        ]
    )
    payload = json.dumps({"odds": f"<table>{rows}</table>"})
    if market == "handicap":
        handicaps = select_lines(parse_handicaps(payload))
        selected_lines = [line.line for line in handicaps]
        selected_odds = [(line.odds_home, line.odds_away) for line in handicaps]
    else:
        totals = select_lines(parse_totals(payload))
        selected_lines = [line.line for line in totals]
        selected_odds = [(line.odds_over, line.odds_under) for line in totals]
    assert selected_lines == [5.5, 6.0, 6.5]
    assert selected_odds == [(1.5, 2.1), (1.3, 2.0), (2.0, 2.0)]


def test_http_match_assembly_uses_current_api_and_caller_owned_session() -> None:
    """Assemble all markets over one supplied session and preserve request arguments."""
    first, second = (item[0] for item in constants.BOOKMAKERS[:2])
    metadata = (
        '<div id="match-date" data-dt="27,10,2026,19,30"></div>'
        '<span class="list-breadcrumb__item__in">Home - Away</span>'
    )
    moneyline = json.dumps(
        {
            "odds": "<table>"
            + "".join(
                f'<tr data-bid="{book}" data-originid="1">{cells(values)}</tr>'
                for book, values in [(second, ("2.2", "3.2", "4.2")), (first, ("2", "3", "4"))]
            )
            + "</table>"
        }
    )
    two_way = json.dumps({"odds": "<table>" + line_row(first, "6", ("2", "2")) + "</table>"})
    session = Mock()
    session.get.side_effect = [Mock(text=text) for text in (metadata, moneyline, two_way, two_way)]
    with BetExplorerScraper(session, timeout=7.5) as scraper:
        match = scraper.get_match(URL)
    assert str(match) == "2026-10-27 19:30 Home - Away."
    assert str(match.bookmaker_moneyline) == "1-X-2: 2.0-3.0-4.0"
    assert [line.line for line in match.bookmaker_handicaps] == [6.0]
    assert [line.line for line in match.bookmaker_totals] == [6.0]
    assert [call.args[0] for call in session.get.call_args_list] == [
        URL,
        *(
            constants.BETEXPLORER_ODDS_URL.format(event_id="AbCd1234", market=market)
            for market in ("1x2", "ah", "ou")
        ),
    ]
    for call in session.get.call_args_list:
        assert call.kwargs == {"headers": {"User-agent": constants.USER_AGENT}, "timeout": 7.5}
    session.close.assert_not_called()


def test_owned_session_closes_when_http_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate an HTTP failure instead of treating it as missing bookmaker markets."""
    session = Mock()
    session.get.return_value.raise_for_status.side_effect = HTTPError("403 Forbidden")
    monkeypatch.setattr(client, "Session", Mock(return_value=session))
    with pytest.raises(HTTPError, match="403"):
        with BetExplorerScraper() as scraper:
            scraper.get_match(URL)
    session.close.assert_called_once()


def test_invalid_odds_response_is_distinct_from_empty_markets() -> None:
    """Reject malformed responses while allowing valid HTML with no bookmaker rows."""
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_totals("not JSON")
    assert parse_totals(json.dumps({"odds": "<table></table>"})) == {}
