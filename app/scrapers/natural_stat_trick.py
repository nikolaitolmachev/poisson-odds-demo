"""Fetch and validate venue-specific Natural Stat Trick xG statistics."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal, cast

from lxml import html

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
    for old_name, new_name in (
        ("St Louis Blues", "St. Louis Blues"),
    ):
        if old_name in normalized:
            normalized[new_name] = normalized.pop(old_name)
    return normalized


def _parse_xg_data(document: str, minimum_games: int) -> XGData:
    """Parse the team table and reject incomplete or invalid statistics."""
    if not document or not document.strip():
        raise ValueError("Natural Stat Trick returned an empty HTML document.")
    tree = html.fromstring(document)
    tables = tree.xpath('//table[@id="teams"]')
    if len(tables) != 1:
        raise ValueError("Expected one Natural Stat Trick statistics table with id 'teams'.")
    rows = tables[0].xpath("./tbody/tr")
    if not rows:
        raise ValueError("The Natural Stat Trick statistics table contains no team rows.")

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


async def get_xg_data_for_all_teams(
    asession: AsyncHTMLSession,
    config: Config,
    side: Literal["home", "away"] = "home",
    use_proxy: bool = False,
) -> XGData:
    """Fetch home or away xG data without changing shared HTTP session settings.

    Args:
        asession: HTTP session owned by the caller.
        config: Validated minimum sample size and optional proxy.
        side: Home or away statistics to request.
        use_proxy: Whether to apply the configured proxy to this request.

    Returns:
        Source team names mapped to (xgf, xga) pairs for teams meeting the
        minimum number of games.
        Use normalize_team_names() to align team names with BetExplorer.

    Raises:
        ConfigurationError: Proxy use was requested without a configured URL.
        requests.exceptions.HTTPError: The source returned an unsuccessful HTTP status.
        ValueError: Statistics are missing, malformed, or contain invalid numbers, or
            no teams meet the configured minimum number of games.
    """
    if use_proxy and not config.proxy:
        raise ConfigurationError(
            "Set the PROXY environment variable or enter N at the proxy prompt."
        )
    proxies = {"https": config.proxy} if use_proxy else None
    header = {
        "User-agent": consts.USER_AGENT_SHORT,
        "Referer": consts.XG_REFERER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    url = consts.XG_SITE_NHL + "H" if side == "home" else consts.XG_SITE_NHL + "A"
    # requests-html inherits the synchronous get() annotation but returns a Future.
    response = await cast(
        "Awaitable[HTMLResponse]", asession.get(url, headers=header, proxies=proxies)
    )
    response.raise_for_status()
    return _parse_xg_data(response.html.html, config.minimal_matches_to_count)
