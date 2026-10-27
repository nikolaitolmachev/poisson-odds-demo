"""HTTP requests and public BetExplorer scraping methods."""

from __future__ import annotations

from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal
from requests import Session

from app import constants as consts
from app.models import NHLMatch, Team
from app.scrapers.betexplorer.matches import (
    MatchMetadata,
    event_id_from_url,
    parse_match_metadata,
    parse_match_urls,
)
from app.scrapers.betexplorer.odds import (
    parse_handicaps,
    parse_moneyline,
    parse_totals,
    select_lines,
    select_moneyline,
)


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
        return parse_match_urls(self._get(consts.NHL_FIXTURES))

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
        event_id_from_url(url)
        return parse_match_metadata(self._get(url))

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
        event_id = event_id_from_url(url)
        payload = self._get(consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market="1x2"))
        return parse_moneyline(payload)

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
        event_id = event_id_from_url(url)
        payload = self._get(consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market="ah"))
        return parse_handicaps(payload)

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
        event_id = event_id_from_url(url)
        payload = self._get(consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market="ou"))
        return parse_totals(payload)

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
            bookmaker_moneyline=select_moneyline(self.get_moneyline(url)),
            bookmaker_handicaps=select_lines(self.get_handicaps(url)),
            bookmaker_totals=select_lines(self.get_totals(url)),
        )
