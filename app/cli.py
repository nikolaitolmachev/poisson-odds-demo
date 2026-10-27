"""Interactive CLI preserving the original menu and calculation workflow."""

from __future__ import annotations

import asyncio

from poisson_odds.poisson import Poisson
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests_html import AsyncHTMLSession

from app import constants as consts
from app.analysis import calculate_quality, looking_for_value
from app.config import Config, ConfigurationError, load_config
from app.scrapers.betexplorer import Scraper
from app.scrapers.natural_stat_trick import XGData, get_xg_data_for_all_teams


async def load_xgs(config: Config, use_proxy: bool) -> tuple[XGData, XGData]:
    """Load and normalize home and away xG using the existing two requests.

    Args:
        config: Application settings shared by both requests.
        use_proxy: Whether the user selected proxy use.

    Returns:
        Home and away xG dictionaries with the two legacy name replacements.
    """
    if use_proxy and not config.proxy:
        raise ConfigurationError("Set PROXY in .env or answer N to the proxy prompt.")
    session = AsyncHTMLSession()
    try:
        home, away = await asyncio.gather(
            get_xg_data_for_all_teams(session, config, use_proxy=use_proxy),
            get_xg_data_for_all_teams(session, config, side="away", use_proxy=use_proxy),
        )
    finally:
        await session.close()
    for data in (home, away):
        for old_name, new_name in (
            ("Utah Hockey Club", "Utah"),
            ("St Louis Blues", "St. Louis Blues"),
        ):
            if old_name in data:
                data[new_name] = data.pop(old_name)
    return home, away


def analyze_all_matches(config: Config, home: XGData, away: XGData) -> None:
    """Analyze all upcoming matches with the original sequential workflow.

    Args:
        config: Validated application settings.
        home: Cached home xG data.
        away: Cached away xG data.
    """
    print("=" * 100)
    urls = Scraper.get_urls_all_upcoming_matches()
    if urls:
        count = "1 match was" if len(urls) == 1 else f"{len(urls)} matches were"
        print(f"Succesfull, {count} founded.")
        print("=" * 100)
        for url in urls:
            analyze_single_match(url, config, home, away)
            print("=" * 100)
    else:
        print(consts.ERROR_NO_UPCOMING_MATCHES_ALL)
        print("=" * 100)


def analyze_single_match(url: str, config: Config, home: XGData, away: XGData) -> None:
    """Scrape a match and apply the unchanged strength and market calculations.

    Args:
        url: BetExplorer match URL.
        config: Validated application settings.
        home: Cached home xG data.
        away: Cached away xG data.
    """
    print("Scraping...")
    nhl_match = Scraper.get_main_data_for_match(url)
    print(nhl_match)
    if nhl_match is None:
        print(consts.EXCEPTION_WITH_SCRAPING_WEB_PAGE)
        return
    if not (
        nhl_match.bookmaker_moneyline is not None
        or nhl_match.bookmaker_handicaps
        or nhl_match.bookmaker_totals
    ):
        print(consts.ERROR_NO_ANY_LINES_ODDS_FOR_MATCH)
        return

    nhl_match.team_a.xGF, nhl_match.team_a.xGA = home.get(nhl_match.team_a.name, (0.0, 0.0))
    nhl_match.team_b.xGF, nhl_match.team_b.xGA = away.get(nhl_match.team_b.name, (0.0, 0.0))
    if (
        nhl_match.team_a.xGF > 0.0
        and nhl_match.team_a.xGA > 0.0
        and nhl_match.team_b.xGA > 0.0
        and nhl_match.team_b.xGF > 0.0
    ):
        print("Calculating...")
        quality_home = calculate_quality(nhl_match.team_a.xGF, nhl_match.team_b.xGA)
        quality_away = calculate_quality(nhl_match.team_b.xGF, nhl_match.team_a.xGA)
        print(f"Team quality: {quality_home} - {quality_away}")
        poisson = Poisson(quality_home, quality_away)
        looking_for_value(nhl_match, poisson, config)
    else:
        if not nhl_match.team_a.xGF and not nhl_match.team_a.xGA:
            print(consts.ERROR_NO_XG_DATA_FOR_HOME)
        if not nhl_match.team_b.xGA and not nhl_match.team_b.xGF:
            print(consts.ERROR_NO_XG_DATA_FOR_AWAY)


def main() -> None:
    """Load configuration once and run the existing interactive menu."""
    config = load_config()
    home: XGData | None = None
    away: XGData | None = None
    print("Welcome to poisson_odds Demo!")
    print("=" * 100)
    use_proxy = input("Do you want to use proxy to get xG data? Y/N: ").lower() == "y"
    while True:
        print("=" * 100)
        print("""Choose the next:
        1 - To analyze all upcoming matches.
        2 - To analyze a single match by a URL.
        or another symbol to exit.""")
        choice = input("Your choice = ")
        if choice in ("1", "2"):
            if home is None or away is None:
                try:
                    home, away = asyncio.run(load_xgs(config, use_proxy))
                except RequestsConnectionError:
                    print(consts.EXCEPTION_WITH_SCRAPING_WEB_PAGE)
                    return
            if choice == "1":
                analyze_all_matches(config, home, away)
            else:
                print("=" * 100)
                url = input("Write a URL of match: ")
                print("=" * 100)
                if consts.NHL_LEAGUE in url:
                    analyze_single_match(url, config, home, away)
                else:
                    print(consts.ERROR_WRONG_URL)
                print("=" * 100)
        else:
            print("=" * 100)
            print("Bye Bye!")
            break


def run() -> None:
    """Run the CLI with the legacy error message and pause before exiting."""
    try:
        main()
    except ConfigurationError as exc:
        print(f"Invalid environment configuration: {exc}")
    except Exception as exc:
        print("Something is going wrong: " + str(exc))
    input("Press any button to exit...")
