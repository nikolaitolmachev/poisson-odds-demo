"""HTTP requests and public BetExplorer scraping methods."""

from __future__ import annotations

from typing import TYPE_CHECKING

from requests import Session

from app import constants as consts
from app.models import NHLMatch, Team
from app.scrapers.betexplorer.matches import (
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

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Literal

    from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal

    from app.scrapers.betexplorer.matches import MatchMetadata


class BetExplorerScraper:
    """Fetch BetExplorer pages over HTTP without a browser.

    Use a with statement or call close() to release an internally created
    HTTP session. A session supplied by the caller remains caller-owned.
    """

    def __init__(self, session: Session | None = None, timeout: float = 20.0) -> None:
        """Initialize the scraper.

        Args:
            session: Optional caller-owned HTTP session. A new session is created if omitted.
            timeout: Request timeout in seconds.
        """
        self._owns_session = session is None
        self._session = Session() if session is None else session
        self._timeout = timeout

    def __enter__(self) -> BetExplorerScraper:
        """Return the scraper for use in a with statement."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close an owned session without suppressing exceptions."""
        self.close()

    def close(self) -> None:
        """Close the HTTP session only if it was created by this scraper."""
        if self._owns_session:
            self._session.close()

    def _get(self, url: str) -> str:
        """Fetch a BetExplorer page with the configured headers and timeout.

        Args:
            url: Absolute BetExplorer URL.

        Returns:
            Decoded response body.

        Raises:
            requests.RequestException: The request failed or returned an error status.
        """
        headers = {"User-agent": consts.USER_AGENT}
        response = self._session.get(
            url,
            headers=headers,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.text

    def _get_odds_payload(self, url: str, market: Literal["1x2", "ah", "ou"]) -> str:
        """Fetch an odds response for a match and market.

        Args:
            url: Absolute BetExplorer NHL match URL.
            market: BetExplorer market identifier.

        Returns:
            Decoded odds response body.

        Raises:
            ValueError: The match URL is invalid.
            requests.RequestException: The odds request failed.
        """
        event_id = event_id_from_url(url)
        return self._get(consts.BETEXPLORER_ODDS_URL.format(event_id=event_id, market=market))

    def get_upcoming_match_urls(self) -> list[str]:
        """Return upcoming match URLs with three listed 1X2 odds values.

        Discovery checks for three nonempty odds values in the fixture HTML.
        Their numeric values and bookmaker availability are not validated here.

        Returns:
            Unique BetExplorer match URLs in fixture order.

        Raises:
            requests.RequestException: The fixtures page could not be retrieved.
            ValueError: The fixtures page, table, or a selected match URL is invalid.
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
        """Return active moneyline odds for bookmakers listed in BOOKMAKERS.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Bookmaker IDs mapped to three-way moneyline odds. Empty if no listed
            bookmaker has a complete active row.

        Raises:
            requests.RequestException: The odds request failed.
            ValueError: The URL or odds response is invalid.
        """
        return parse_moneyline(self._get_odds_payload(url, "1x2"))

    def get_handicaps(self, url: str) -> dict[int, list[BookmakerHandicap]]:
        """Return active Asian handicap odds for bookmakers listed in BOOKMAKERS.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Bookmaker IDs mapped to valid handicap lines in source order.

        Raises:
            requests.RequestException: The odds request failed.
            ValueError: The URL or odds response is invalid.
        """
        return parse_handicaps(self._get_odds_payload(url, "ah"))

    def get_totals(self, url: str) -> dict[int, list[BookmakerTotal]]:
        """Return active total odds for bookmakers listed in BOOKMAKERS.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Bookmaker IDs mapped to valid over/under lines in source order.

        Raises:
            requests.RequestException: The odds request failed.
            ValueError: The URL or odds response is invalid.
        """
        return parse_totals(self._get_odds_payload(url, "ou"))

    def get_match(self, url: str) -> NHLMatch:
        """Return an NHL match with markets selected in BOOKMAKERS priority order.

        Args:
            url: Absolute BetExplorer NHL match URL.

        Returns:
            Match metadata and markets selected by bookmaker priority. Each
            handicap or total line uses the first available bookmaker for that line.

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
