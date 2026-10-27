"""Load xG using the original Natural Stat Trick URL and table columns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from lxml import html

from app import constants as consts
from app.config import Config, ConfigurationError

if TYPE_CHECKING:
    from requests_html import AsyncHTMLSession

XGData = dict[str, tuple[float, float]]


async def get_xg_data_for_all_teams(
    asession: AsyncHTMLSession,
    config: Config,
    side: Literal["home", "away"] = "home",
    use_proxy: bool = False,
) -> XGData:
    """Fetch venue-specific xG with the existing extraction and sample filter.

    Args:
        asession: HTTP session owned by the caller.
        config: Validated minimum sample size and optional proxy.
        side: Home or away statistics to request.
        use_proxy: Whether to apply the configured proxy, as chosen in the CLI.

    Returns:
        Team names mapped to xgf and xga for teams meeting the sample minimum.

    Raises:
        ConfigurationError: Proxy use was requested without a configured URL.
    """
    teams: XGData = {}
    if use_proxy:
        if not config.proxy:
            raise ConfigurationError("Set PROXY in .env or answer N to the proxy prompt.")
        asession.proxies.update({"https": config.proxy})

    header = {"User-agent": consts.USER_AGENT_SHORT}
    url = consts.XG_SITE_NHL + "H" if side == "home" else consts.XG_SITE_NHL + "A"
    request = await asession.get(url, headers=header)
    tree = html.fromstring(request.html.html)
    teams_table = tree.xpath('//table[@id="teams"]/tbody/tr')
    for elem in teams_table:
        team_name = elem.xpath('./td[@class="lh"]/text()')[0]
        games_played = int(elem.xpath("./td[3]/text()")[0])
        if games_played >= config.minimal_matches_to_count:
            xgf = float(elem.xpath("./td[23]/text()")[0])
            xga = float(elem.xpath("./td[24]/text()")[0])
            teams[team_name] = (xgf, xga)
    return teams
