"""Run the interactive CLI for NHL match analysis."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from lxml.etree import ParserError
from poisson_odds.poisson import Poisson
from requests.exceptions import RequestException
from requests_html import AsyncHTMLSession
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from app import constants as consts
from app.analysis import analyze_match_odds, calculate_quality_mean
from app.cache import read_xg_cache, save_xg_cache
from app.config import ConfigurationError, load_config
from app.scrapers.betexplorer import BetExplorerScraper
from app.scrapers.natural_stat_trick import get_xg_data_for_all_teams, normalize_team_names

if TYPE_CHECKING:
    from app.config import Config
    from app.scrapers.natural_stat_trick import XGData


async def load_xgs(config: Config, use_proxy: bool) -> tuple[XGData, XGData]:
    """Load cached xG data or refresh both datasets, using stale data if needed.

    Args:
        config: Application settings shared by both requests.
        use_proxy: Whether the user selected proxy use.

    Returns:
        Home and away xG dictionaries with normalized team names.

    Raises:
        ConfigurationError: Proxy use was requested without a configured URL.
        ValueError: Either venue returned no usable xG data.
        RequestException: A statistics request failed.
    """
    cached = read_xg_cache(config.minimal_matches_to_count)
    if cached is not None and cached.is_fresh(datetime.now(timezone.utc)):
        return cached.home, cached.away
    if use_proxy and not config.proxy:
        raise ConfigurationError(
            "Set the PROXY environment variable or enter N at the proxy prompt."
        )
    try:
        session = AsyncHTMLSession()
        home_task = asyncio.create_task(
            get_xg_data_for_all_teams(session, config, use_proxy=use_proxy)
        )
        away_task = asyncio.create_task(
            get_xg_data_for_all_teams(session, config, side="away", use_proxy=use_proxy)
        )
        try:
            home, away = await asyncio.gather(home_task, away_task)
        finally:
            for task in (home_task, away_task):
                task.cancel()
            await asyncio.gather(home_task, away_task, return_exceptions=True)
            await session.close()
        if not home or not away:
            raise ValueError("Home or away xG data is missing.")
    except ConfigurationError:
        raise
    except (RequestException, Urllib3HTTPError, OSError, ValueError, IndexError, ParserError):
        if cached is None:
            raise
        print(
            "Warning: Unable to refresh xG data. Using cached data retrieved at "
            f"{cached.retrieved_at.isoformat()} (UTC)."
        )
        return cached.home, cached.away
    home = normalize_team_names(home)
    away = normalize_team_names(away)
    try:
        save_xg_cache(home, away, config.minimal_matches_to_count)
    except OSError:
        print("Warning: Unable to save xG cache. Continuing with fresh data.")
    return home, away


def analyze_all_matches(config: Config, home: XGData, away: XGData) -> bool:
    """Analyze upcoming NHL matches one at a time.

    Args:
        config: Validated application settings.
        home: Cached home xG data.
        away: Cached away xG data.

    Returns:
        True if every match was processed successfully or no matches were found.
    """
    print("=" * 100)
    with BetExplorerScraper() as scraper:
        try:
            urls = scraper.get_upcoming_match_urls()
        except (RequestException, Urllib3HTTPError):
            print("Unable to load NHL match data.")
            print("=" * 100)
            return False
        except ValueError:
            print("Invalid BetExplorer fixtures response.")
            print("=" * 100)
            return False
        if urls:
            count = "1 NHL match" if len(urls) == 1 else f"{len(urls)} NHL matches"
            print(f"Found {count}.")
            print("=" * 100)
            successful = True
            for url in urls:
                if not analyze_single_match(url, config, home, away, scraper=scraper):
                    successful = False
                print("=" * 100)
            return successful
        print("No upcoming NHL matches with bookmaker odds were found.")
        print("=" * 100)
        return True


def analyze_single_match(
    url: str,
    config: Config,
    home: XGData,
    away: XGData,
    *,
    scraper: BetExplorerScraper | None = None,
) -> bool:
    """Analyze one NHL match and report expected scraping or file errors.

    Args:
        url: BetExplorer match URL.
        config: Validated application settings.
        home: Cached home xG data.
        away: Cached away xG data.
        scraper: Shared scraper for all-matches mode. If omitted, a scraper is
            created and closed for this operation.

    Returns:
        Whether the match was analyzed successfully.
    """
    try:
        if scraper is None:
            with BetExplorerScraper() as owned_scraper:
                return _analyze_single_match(url, config, home, away, owned_scraper)
        return _analyze_single_match(url, config, home, away, scraper)
    except (RequestException, Urllib3HTTPError):
        print("Unable to load NHL match data.")
        return False
    except OSError:
        print("Unable to access application resources or save the report.")
        return False


def _analyze_single_match(
    url: str, config: Config, home: XGData, away: XGData, scraper: BetExplorerScraper
) -> bool:
    """Retrieve an NHL match, calculate team strength, and compare market odds.

    Args:
        url: BetExplorer match URL.
        config: Validated application settings.
        home: Cached home xG data.
        away: Cached away xG data.
        scraper: HTTP scraper owned by the caller.
    """
    print("Loading NHL match data...")
    try:
        nhl_match = scraper.get_match(url)
    except ValueError:
        print("Invalid BetExplorer match URL or response.")
        return False
    if nhl_match is None:
        print("Unable to load NHL match data.")
        return False
    print(nhl_match)
    if not (
        nhl_match.bookmaker_moneyline is not None
        or nhl_match.bookmaker_handicaps
        or nhl_match.bookmaker_totals
    ):
        print("No bookmaker odds are available for this NHL match.")
        return False

    nhl_match.team_a.xgf, nhl_match.team_a.xga = home.get(nhl_match.team_a.name, (0.0, 0.0))
    nhl_match.team_b.xgf, nhl_match.team_b.xga = away.get(nhl_match.team_b.name, (0.0, 0.0))
    if (
        nhl_match.team_a.xgf > 0.0
        and nhl_match.team_a.xga > 0.0
        and nhl_match.team_b.xga > 0.0
        and nhl_match.team_b.xgf > 0.0
    ):
        print("Calculating model odds...")

        # TODO: change strategy here
        quality_home = calculate_quality_mean(nhl_match.team_a.xgf, nhl_match.team_b.xga)
        quality_away = calculate_quality_mean(nhl_match.team_b.xgf, nhl_match.team_a.xga)

        print(f"Team strength: {quality_home} - {quality_away}")
        poisson = Poisson(quality_home, quality_away)
        analyze_match_odds(nhl_match, poisson, config)
        return True
    else:
        if nhl_match.team_a.xgf <= 0 or nhl_match.team_a.xga <= 0:
            print("Insufficient xG data for the home team.")
        if nhl_match.team_b.xgf <= 0 or nhl_match.team_b.xga <= 0:
            print("Insufficient xG data for the away team.")
    return False


def _is_match_url(url: str) -> bool:
    """Check the scheme, BetExplorer hostname, and nonempty NHL match path."""
    try:
        parsed = urlsplit(url)
        prefix = consts.NHL_MATCH_PATH_PREFIX
        return (
            parsed.scheme in ("http", "https")
            and parsed.hostname in consts.BETEXPLORER_HOSTNAMES
            and parsed.path.startswith(prefix)
            and bool(parsed.path.removeprefix(prefix).strip("/"))
        )
    except ValueError:
        return False


def main() -> int:
    """Load configuration and run the interactive menu.

    Returns:
        Zero on success, or one if any requested operation failed.
    """
    config = load_config()
    status = 0
    home: XGData | None = None
    away: XGData | None = None
    print("Welcome to Poisson Odds Demo!")
    print("=" * 100)
    use_proxy = input("Use a proxy to load xG data? (Y/N): ").lower() == "y"
    while True:
        print("=" * 100)
        print("""Choose an option:
        1 - Analyze all upcoming NHL matches.
        2 - Analyze an NHL match by URL.
        Enter any other value to exit.""")
        choice = input("Choice: ")
        if choice in ("1", "2"):
            if home is None or away is None:
                try:
                    home, away = asyncio.run(load_xgs(config, use_proxy))
                except (RequestException, Urllib3HTTPError, ValueError, IndexError, ParserError):
                    print("Unable to load xG data.")
                    return 1
            if choice == "1":
                if not analyze_all_matches(config, home, away):
                    status = 1
            else:
                print("=" * 100)
                url = input("NHL match URL: ")
                print("=" * 100)
                if _is_match_url(url):
                    if not analyze_single_match(url, config, home, away):
                        status = 1
                else:
                    print("Invalid URL. Enter an NHL match URL from BetExplorer.")
                    status = 1
                print("=" * 100)
        else:
            print("=" * 100)
            print("Goodbye!")
            return status


def run() -> int:
    """Run the interactive CLI and return a process exit status.

    Returns:
        Zero on success, or one for configuration or application failures.
    """
    try:
        return main()
    except ConfigurationError as exc:
        print(f"Invalid configuration: {exc}")
    except Exception as exc:
        print("Application error: " + str(exc))
    return 1
