"""Discover upcoming BetExplorer NHL matches with complete moneyline odds."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar
from urllib.parse import urljoin, urlsplit

from lxml import html
from lxml.etree import ParserError
from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal
from requests import Session

from app import constants as consts
from app.models import NHLMatch, Team

_MONEYLINE_OUTCOME_COUNT = 3
_BOOKMAKER_IDS = frozenset(bookmaker_id for bookmaker_id, _ in consts.BOOKMAKERS)
LineMarket = TypeVar("LineMarket", BookmakerHandicap, BookmakerTotal)


@dataclass(frozen=True)
class MatchMetadata:
    """Date and teams parsed from a BetExplorer match page.

    Attributes:
        date: Kickoff date and time formatted as ``YYYY-MM-DD HH:MM``.
        home: Home team name exactly as provided by BetExplorer.
        away: Away team name exactly as provided by BetExplorer.
    """

    date: str
    home: str
    away: str


class BetExplorerScraper:
    """Fetch BetExplorer pages over HTTP without a browser."""

    def __init__(self, session: Session | None = None, timeout: float = 20.0) -> None:
        """Initialize the scraper.

        Args:
            session: Optional caller-owned HTTP session. A new session is created if omitted.
            timeout: Request timeout in seconds.
        """
        self._session = session or Session()
        self._timeout = timeout

    def _get(self, url: str) -> str:
        """Fetch a BetExplorer page with the configured headers and timeout.

        Args:
            url: Absolute BetExplorer URL.

        Returns:
            Decoded response body.

        Raises:
            requests.RequestException: The request failed or returned an error status.
        """
        response = self._session.get(
            url,
            headers=consts.BETEXPLORER_HEADERS,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.text

    def get_upcoming_match_urls(self) -> list[str]:
        """Return absolute URLs for upcoming matches with complete 1X2 odds.

        Returns:
            Unique BetExplorer match URLs in fixture order.

        Raises:
            requests.RequestException: The fixtures page could not be retrieved.
            ValueError: The fixtures page is empty, malformed, or has no usable table.
        """
        return _parse_match_urls(self._get(consts.NHL_FIXTURES))

    def get_match_metadata(self, url: str) -> MatchMetadata:
        """Return the date and teams for a BetExplorer NHL match URL.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Metadata parsed from the match page.

        Raises:
            requests.RequestException: The match page could not be retrieved.
            ValueError: The URL or page metadata is invalid.
        """
        _event_id_from_url(url)
        return _parse_match_metadata(self._get(url))

    def get_moneyline(self, url: str) -> dict[int, BookmakerMoneyline]:
        """Return active 1X2 odds for bookmakers listed in ``BOOKMAKERS``.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Bookmaker IDs mapped to three-way moneyline odds. Empty when no
            whitelisted bookmaker has a complete active row.

        Raises:
            requests.RequestException: The odds request failed.
            ValueError: The URL or odds response is invalid.
        """
        event_id = _event_id_from_url(url)
        payload = self._get(
            consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market="1x2")
        )
        return _parse_moneyline(payload)

    def get_handicaps(self, url: str) -> dict[int, list[BookmakerHandicap]]:
        """Return active Asian handicap rows for ``BOOKMAKERS``.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Bookmaker IDs mapped to valid handicap lines in source order.

        Raises:
            requests.RequestException: The odds request failed.
            ValueError: The URL or odds response is invalid.
        """
        event_id = _event_id_from_url(url)
        payload = self._get(
            consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market="ah")
        )
        return _parse_handicaps(payload)

    def get_totals(self, url: str) -> dict[int, list[BookmakerTotal]]:
        """Return active totals rows for bookmakers listed in ``BOOKMAKERS``.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Bookmaker IDs mapped to valid over/under lines in source order.

        Raises:
            requests.RequestException: The odds request failed.
            ValueError: The URL or odds response is invalid.
        """
        event_id = _event_id_from_url(url)
        payload = self._get(
            consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market="ou")
        )
        return _parse_totals(payload)

    def get_match(self, url: str) -> NHLMatch:
        """Return a match with markets selected by ``BOOKMAKERS`` priority.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Match metadata and the highest-priority available markets.

        Raises:
            requests.RequestException: A BetExplorer request failed.
            ValueError: The URL or any source response is invalid.
        """
        metadata = self.get_match_metadata(url)
        return NHLMatch(
            date=metadata.date,
            team_a=Team(metadata.home),
            team_b=Team(metadata.away),
            url=url,
            bookmaker_moneyline=_select_moneyline(self.get_moneyline(url)),
            bookmaker_handicaps=_select_lines(self.get_handicaps(url)),
            bookmaker_totals=_select_lines(self.get_totals(url)),
        )


def _parse_match_urls(document: str) -> list[str]:
    """Extract unique NHL match URLs that have three moneyline odds.

    Args:
        document: BetExplorer NHL fixtures HTML.

    Returns:
        Match URLs in source order without duplicates.

    Raises:
        ValueError: The document is empty, not HTML, or lacks one fixtures table.
    """
    if not document or not document.strip():
        raise ValueError("BetExplorer returned an empty fixtures page.")
    try:
        tree = html.fromstring(document)
    except ParserError as exc:
        raise ValueError("BetExplorer fixtures page is not valid HTML.") from exc

    tables = tree.xpath(
        '//table[contains(concat(" ", normalize-space(@class), " "), '
        '" table-main--leaguefixtures ")]'
    )
    if len(tables) != 1:
        raise ValueError("Expected exactly one BetExplorer NHL fixtures table.")

    urls: list[str] = []
    seen: set[str] = set()
    for row in tables[0].xpath(".//tr"):
        links = row.xpath(
            './td[2]/a[contains(concat(" ", normalize-space(@class), " "), " in-match ")]'
        )
        if not links:
            continue
        odds = [
            button
            for button in row.xpath(
                './/td[contains(@class, "table-main__odds")]//button[@data-odd]'
            )
            if (button.get("data-odd") or "").strip()
        ]
        if len(odds) != _MONEYLINE_OUTCOME_COUNT:
            continue
        href = links[0].get("href")
        if not href:
            raise ValueError("BetExplorer match row has no URL.")
        url = urljoin(consts.BETEXPLORER_BASE + "/", href)
        _event_id_from_url(url)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _parse_moneyline(payload: str) -> dict[int, BookmakerMoneyline]:
    """Parse active 1X2 rows for whitelisted bookmakers.

    Args:
        payload: BetExplorer odds endpoint response body.

    Returns:
        Bookmaker IDs mapped to complete three-way moneyline rows.

    Raises:
        ValueError: The payload is empty, not JSON, or lacks an HTML ``odds`` value.
    """
    tree = _load_odds_tree(payload, "moneyline")
    moneylines: dict[int, BookmakerMoneyline] = {}
    for row in tree.xpath('//tr[@data-bid and @data-originid="1"]'):
        bookmaker_id = _row_bookmaker_id(row)
        if bookmaker_id is None:
            continue
        cells = row.xpath('./td[contains(@class, "table-main__detail-odds")]')
        odds = _active_odds(cells, _MONEYLINE_OUTCOME_COUNT)
        if odds is None:
            continue
        moneylines[bookmaker_id] = BookmakerMoneyline(*odds)
    return moneylines


def _parse_handicaps(payload: str) -> dict[int, list[BookmakerHandicap]]:
    """Parse active Asian handicap rows for whitelisted bookmakers.

    Args:
        payload: BetExplorer odds endpoint response body.

    Returns:
        Bookmaker IDs mapped to valid handicap lines in source order.

    Raises:
        ValueError: The payload is empty, not JSON, or lacks an HTML ``odds`` value.
    """
    tree = _load_odds_tree(payload, "handicap")
    handicaps_by_bookmaker: dict[int, list[BookmakerHandicap]] = {}
    for bookmaker_id, line, odds in _iter_line_rows(tree):
        handicaps_by_bookmaker.setdefault(bookmaker_id, []).append(
            BookmakerHandicap(line, *odds)
        )
    return handicaps_by_bookmaker


def _parse_totals(payload: str) -> dict[int, list[BookmakerTotal]]:
    """Parse active totals rows for whitelisted bookmakers.

    Args:
        payload: BetExplorer odds endpoint response body.

    Returns:
        Bookmaker IDs mapped to valid over/under lines in source order.

    Raises:
        ValueError: The payload is empty, not JSON, or lacks an HTML ``odds`` value.
    """
    tree = _load_odds_tree(payload, "totals")
    totals_by_bookmaker: dict[int, list[BookmakerTotal]] = {}
    for bookmaker_id, line, odds in _iter_line_rows(tree):
        totals_by_bookmaker.setdefault(bookmaker_id, []).append(
            BookmakerTotal(line, *odds)
        )
    return totals_by_bookmaker


def _load_odds_tree(payload: str, market: str) -> html.HtmlElement:
    """Decode a BetExplorer odds response and return its HTML tree.

    Args:
        payload: BetExplorer odds endpoint response body.
        market: Market label used in validation messages.

    Returns:
        Parsed odds HTML.

    Raises:
        ValueError: The payload is empty, not JSON, or lacks valid HTML.
    """
    if not payload or not payload.strip():
        raise ValueError(f"BetExplorer returned an empty {market} response.")
    try:
        response = json.loads(payload)
    except ValueError as exc:
        raise ValueError(f"BetExplorer {market} response is not valid JSON.") from exc
    odds_html = response.get("odds") if isinstance(response, dict) else None
    if not isinstance(odds_html, str) or "<" not in odds_html:
        raise ValueError(f"BetExplorer {market} response has no odds HTML.")
    try:
        return html.fromstring(odds_html)
    except ParserError as exc:
        raise ValueError(f"BetExplorer {market} HTML is not valid.") from exc


def _iter_line_rows(tree: html.HtmlElement) -> Iterator[tuple[int, float, list[float]]]:
    """Yield valid two-way line rows with whitelisted bookmaker IDs.

    Args:
        tree: Parsed BetExplorer odds HTML.

    Yields:
        Bookmaker ID, line, and the two validated odds values.
    """
    for row in tree.xpath("//tr[@data-bid]"):
        bookmaker_id = _row_bookmaker_id(row)
        if bookmaker_id is None:
            continue
        line_values = row.xpath(
            './td[contains(@class, "table-main__doubleparameter")]/text()'
        )
        if len(line_values) != 1:
            continue
        try:
            line = float(line_values[0].strip())
        except ValueError:
            continue
        if not math.isfinite(line) or not _is_whole_or_half_line(line):
            continue
        cells = row.xpath('./td[contains(@class, "table-main__detail-odds")]')
        odds = _active_odds(
            cells,
            2,
            minimum=consts.MINIMAL_ODDS_TO_GET_LINE,
        )
        if odds is None:
            continue
        yield bookmaker_id, line, odds


def _row_bookmaker_id(row: html.HtmlElement) -> int | None:
    """Return a whitelisted bookmaker ID from a row, or None."""
    try:
        bookmaker_id = int(row.get("data-bid", ""))
    except ValueError:
        return None
    return bookmaker_id if bookmaker_id in _BOOKMAKER_IDS else None


def _active_odds(
    cells: list[html.HtmlElement],
    expected_count: int,
    minimum: float | None = None,
) -> list[float] | None:
    """Return validated active odds, or None when the row is unusable.

    Args:
        cells: Odds cells from a single bookmaker row.
        expected_count: Required number of odds values.
        minimum: Optional inclusive minimum for every odds value.

    Returns:
        Finite positive odds values, or None for an inactive or malformed row.
    """
    if len(cells) != expected_count:
        return None
    if any("inactive" in (cell.get("class") or "") for cell in cells):
        return None
    values = [(cell.get("data-odd") or "").strip() for cell in cells]
    if not all(values):
        return None
    try:
        odds = [float(value) for value in values]
    except ValueError:
        return None
    if not all(math.isfinite(value) and value > 0 for value in odds):
        return None
    if minimum is not None and any(value < minimum for value in odds):
        return None
    return odds


def _is_whole_or_half_line(line: float) -> bool:
    """Return whether a line uses a whole- or half-goal value."""
    doubled = line * 2
    return math.isclose(doubled, round(doubled), abs_tol=1e-9)


def _select_moneyline(
    moneylines: dict[int, BookmakerMoneyline],
) -> BookmakerMoneyline | None:
    """Return the highest-priority available moneyline.

    Args:
        moneylines: Parsed moneyline rows keyed by bookmaker ID.

    Returns:
        The first available moneyline in ``BOOKMAKERS`` priority order, or None.
    """
    for bookmaker_id, _ in consts.BOOKMAKERS:
        moneyline = moneylines.get(bookmaker_id)
        if moneyline is not None:
            return moneyline
    return None


def _select_lines(markets: dict[int, list[LineMarket]]) -> list[LineMarket]:
    """Return unique lines in bookmaker priority order, sorted by line.

    Args:
        markets: Parsed handicap or total rows keyed by bookmaker ID.

    Returns:
        Unique lines selected from the first bookmakers in ``BOOKMAKERS`` order.
    """
    selected: list[LineMarket] = []
    seen: set[float] = set()
    for bookmaker_id, _ in consts.BOOKMAKERS:
        for market in markets.get(bookmaker_id, []):
            if market.line in seen:
                continue
            seen.add(market.line)
            selected.append(market)
    return sorted(selected, key=lambda market_: market_.line)


def _event_id_from_url(url: str) -> str:
    """Validate a BetExplorer NHL match URL and return its event ID.

    Args:
        url: Candidate absolute match URL.

    Returns:
        Eight-character BetExplorer event ID.

    Raises:
        ValueError: The URL is not an absolute BetExplorer NHL match URL.
    """
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in consts.BETEXPLORER_HOSTNAMES
        or not parsed.path.startswith(consts.NHL_MATCH_PATH_PREFIX)
    ):
        raise ValueError(f"Invalid BetExplorer NHL match URL: {url}")
    event_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if len(event_id) != 8 or not event_id.isalnum():
        raise ValueError(f"Invalid BetExplorer event ID in URL: {url}")
    return event_id


def _parse_match_metadata(document: str) -> MatchMetadata:
    """Parse match date and teams from JSON-LD, with HTML fallbacks.

    Args:
        document: BetExplorer match page HTML.

    Returns:
        Date, home team, and away team from the match page.

    Raises:
        ValueError: The document is empty, not HTML, or lacks usable metadata.
    """
    if not document or not document.strip():
        raise ValueError("BetExplorer returned an empty match page.")
    try:
        tree = html.fromstring(document)
    except ParserError as exc:
        raise ValueError("BetExplorer match page is not valid HTML.") from exc

    date_value = ""
    home = ""
    away = ""
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(script.text_content())
        except (TypeError, ValueError):
            continue
        if not _is_sports_event(payload):
            continue
        date_value = _parse_start_date(payload.get("startDate"))
        home = _nested_text(payload.get("homeTeam"), "name")
        away = _nested_text(payload.get("awayTeam"), "name")
        break

    if not date_value:
        dates = tree.xpath('//*[@id="match-date"]/@data-dt')
        if dates:
            try:
                date_value = datetime.strptime(
                    dates[0].strip(), "%d,%m,%Y,%H,%M"
                ).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass

    if not home or not away:
        names = tree.xpath('//span[contains(@class, "list-breadcrumb__item__in")]/text()')
        if names:
            parts = names[0].strip().split(" - ")
            if len(parts) == 2:
                home, away = (part.strip() for part in parts)

    missing = [
        label
        for label, value in (("date", date_value), ("home team", home), ("away team", away))
        if not value
    ]
    if missing:
        raise ValueError(f"BetExplorer match page is missing: {', '.join(missing)}.")
    return MatchMetadata(date_value, home, away)


def _is_sports_event(payload: object) -> bool:
    """Return whether a JSON-LD payload describes a sports event."""
    if not isinstance(payload, dict):
        return False
    event_type = payload.get("@type")
    if event_type == "SportsEvent":
        return True
    return isinstance(event_type, list) and "SportsEvent" in event_type


def _parse_start_date(value: object) -> str:
    """Format an ISO 8601 start date in the user's local timezone."""
    if not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _nested_text(payload: object, key: str) -> str:
    """Return a stripped nested string value, or an empty string."""
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


if __name__ == "__main__":
    scraper = BetExplorerScraper()
    match_urls = scraper.get_upcoming_match_urls()
    print(f"{len(match_urls)} matches")
    print(*match_urls, sep="\n")
    if match_urls:
        match = scraper.get_match(match_urls[0])
        print("\nFirst match:")
        print(match)
        print("Moneyline:", match.bookmaker_moneyline)
        print("Handicaps:", [str(line) for line in match.bookmaker_handicaps])
        print("Totals:", [str(line) for line in match.bookmaker_totals])
