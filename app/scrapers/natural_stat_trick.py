"""Fetch venue-specific All Strengths xG rates and raw league statistics."""

from __future__ import annotations

import math
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Literal, cast

from lxml import html
from requests_html import HTMLResponse

from app import constants as consts
from app.config import Config, ConfigurationError

if TYPE_CHECKING:
    from requests_html import AsyncHTMLSession

XGData = dict[str, tuple[float, float]]


def normalize_team_names(data: XGData) -> XGData:
    """Map Natural Stat Trick team names to their BetExplorer equivalents.

    Args:
        data: Team names mapped to xG pairs.

    Returns:
        A new dictionary with the two known aliases replaced. The input is unchanged.
    """
    normalized = data.copy()
    for old_name, new_name in (("St Louis Blues", "St. Louis Blues"),):
        if old_name in normalized:
            normalized[new_name] = normalized.pop(old_name)
    return normalized


def _statistics_rows(document: str) -> list[html.HtmlElement]:
    """Return rows from the required, nonempty statistics table."""
    if not document or not document.strip():
        raise ValueError("Natural Stat Trick returned an empty HTML document.")
    tree = html.fromstring(document)
    tables = tree.xpath('//table[@id="teams"]')
    if len(tables) != 1:
        raise ValueError("Expected one Natural Stat Trick statistics table with id 'teams'.")
    rows = tables[0].xpath("./tbody/tr")
    if not rows:
        raise ValueError("The Natural Stat Trick statistics table contains no team rows.")

    return rows


def _parse_xg_data(document: str, minimum_games: int) -> XGData:
    """Parse the team table and reject incomplete or invalid statistics."""
    rows = _statistics_rows(document)
    teams: XGData = {}
    for index, row in enumerate(rows, start=1):
        cells = row.xpath("./td")
        names = row.xpath('./td[@class="lh"]')
        if len(cells) < 3 or len(names) != 1:
            raise ValueError(
                f"Missing team name or games played in Natural Stat Trick row {index}."
            )
        team_name = names[0].text_content().strip()
        if not team_name:
            raise ValueError(f"Empty team name in Natural Stat Trick row {index}.")
        try:
            games_played = int(cells[2].text_content())
        except ValueError as exc:
            raise ValueError(f"Invalid games played in Natural Stat Trick row {index}.") from exc
        if games_played < 0:
            raise ValueError(f"Negative games played in Natural Stat Trick row {index}.")
        if games_played < minimum_games:
            continue
        if len(cells) < 24:
            raise ValueError(f"Missing xG columns in Natural Stat Trick row {index}.")
        try:
            xgf = float(cells[22].text_content())
            xga = float(cells[23].text_content())
        except ValueError as exc:
            raise ValueError(f"Invalid xG values in Natural Stat Trick row {index}.") from exc
        if not all(math.isfinite(value) and value >= 0 for value in (xgf, xga)):
            raise ValueError(f"Non-finite or negative xG values in Natural Stat Trick row {index}.")
        teams[team_name] = (xgf, xga)
    if not teams:
        raise ValueError(
            f"No teams have played at least {minimum_games} games in the requested dataset."
        )
    return teams


async def _fetch_statistics(
    asession: AsyncHTMLSession,
    config: Config,
    side: Literal["home", "away"],
    use_proxy: bool,
    base_url: str,
) -> str:
    """Fetch rates or raw statistics with the same headers and proxy handling."""
    if use_proxy and not config.proxy:
        raise ConfigurationError(
            "Set the PROXY environment variable or enter N at the proxy prompt."
        )
    proxies = {"https": config.proxy} if use_proxy else None
    header = {
        "User-agent": consts.USER_AGENT,
        "Referer": consts.XG_REFERER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    url = base_url + ("H" if side == "home" else "A")
    # requests-html inherits the synchronous get() annotation but returns a Future.
    response = await cast(
        Awaitable[HTMLResponse], asession.get(url, headers=header, proxies=proxies)
    )
    response.raise_for_status()
    return response.html.html


def _parse_toi_minutes(toi: str) -> float:
    """Convert nonnegative minutes:seconds ice time to minutes."""
    try:
        minutes, seconds = map(int, toi.split(":"))
    except ValueError as exc:
        raise ValueError("Invalid Natural Stat Trick TOI; expected minutes:seconds.") from exc
    if minutes < 0 or not 0 <= seconds < 60:
        raise ValueError("Invalid Natural Stat Trick TOI; expected nonnegative minutes:seconds.")
    return minutes + seconds / 60


def _parse_raw_xg_data(document: str) -> list[tuple[float, float, float]]:
    """Extract raw (xGF, xGA, TOI minutes) rows without a minimum-games filter."""
    result: list[tuple[float, float, float]] = []
    for index, row in enumerate(_statistics_rows(document), start=1):
        cells = row.xpath("./td")
        if len(cells) < 24:
            raise ValueError(f"Missing raw xG or TOI columns in Natural Stat Trick row {index}.")
        try:
            toi_minutes = _parse_toi_minutes(cells[3].text_content().strip())
            xgf = float(cells[22].text_content())
            xga = float(cells[23].text_content())
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid raw xG or TOI in Natural Stat Trick row {index}.") from exc
        if not all(math.isfinite(value) and value >= 0 for value in (xgf, xga, toi_minutes)):
            raise ValueError(
                f"Non-finite or negative raw statistics in Natural Stat Trick row {index}."
            )
        result.append((xgf, xga, toi_minutes))
    return result


async def get_xg_data_for_all_teams(
    asession: AsyncHTMLSession,
    config: Config,
    side: Literal["home", "away"] = "home",
    use_proxy: bool = False,
) -> XGData:
    """Fetch home or away xG rates across all game situations.

    Note:
        All Strengths includes special teams, empty-net situations, and
        overtime. These rates are not restricted to regulation time.

        To approximate regulation-only rates, retrieve raw xGF, xGA, and
        time on ice (rate=n) for both All Strengths and 3v3, subtract
        the 3v3 values, and recalculate the rates per 60 minutes.

        This does not fully exclude overtime, since overtime can also
        include other manpower situations.

    Args:
        asession: HTTP session owned by the caller.
        config: Validated minimum sample size and optional proxy.
        side: Home or away statistics to request.
        use_proxy: Whether to apply the configured proxy to this request.

    Returns:
        Source team names mapped to (xgf, xga) pairs of xGF/60 and xGA/60
        rates for teams meeting the minimum number of games.
        Use normalize_team_names() to align team names with BetExplorer.

    Raises:
        ConfigurationError: Proxy use was requested without a configured URL.
        requests.exceptions.HTTPError: The source returned an unsuccessful HTTP status.
        ValueError: Statistics are missing, malformed, or contain invalid numbers, or
            no teams meet the configured minimum number of games.
    """
    document = await _fetch_statistics(asession, config, side, use_proxy, consts.XG_SITE_NHL_RATES)
    return _parse_xg_data(document, config.minimal_matches_to_count)


async def get_league_average_xg(
    asession: AsyncHTMLSession,
    config: Config,
    side: Literal["home", "away"] = "home",
    use_proxy: bool = False,
) -> float:
    """Calculate the All Strengths league xGF/60 baseline for one venue.

    All teams contribute raw xGF and ice time, regardless of the configured
    minimum number of games. No intermediate values are rounded.

    Args:
        asession: HTTP session shared with the team statistics requests.
        config: Settings providing the optional proxy URL.
        side: Home or away statistics to request.
        use_proxy: Whether to apply the configured proxy to this request.

    Returns:
        Total raw xGF divided by total TOI in minutes, multiplied by 60.

    Raises:
        ConfigurationError: Proxy use was requested without a configured URL.
        requests.exceptions.HTTPError: The source returned an unsuccessful HTTP status.
        ValueError: Raw statistics are invalid or total ice time is not positive.
    """
    document = await _fetch_statistics(asession, config, side, use_proxy, consts.XG_SITE_NHL_RAW)
    rows = _parse_raw_xg_data(document)
    total_toi = sum(row[2] for row in rows)
    if not math.isfinite(total_toi) or total_toi <= 0:
        raise ValueError("Natural Stat Trick total TOI must be positive and finite.")
    average = sum(row[0] for row in rows) / total_toi * 60
    if not math.isfinite(average) or average < 0:
        raise ValueError("Natural Stat Trick league xGF/60 must be nonnegative and finite.")
    return average
