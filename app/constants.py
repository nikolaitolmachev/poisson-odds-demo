"""Source URLs, request settings, and model constants."""

BETEXPLORER_HOSTNAMES = ("betexplorer.com", "www.betexplorer.com")
NHL_MATCH_PATH_PREFIX = "/hockey/usa/nhl/"
NHL_LEAGUE = "https://www.betexplorer.com/hockey/usa/nhl/"
SUFFIX_FOR_HANDICAPS = "#ah"
SUFFIX_FOR_TOTALS = "#ou"

XG_SITE_NHL = (
    "https://www.naturalstattrick.com/teamtable.php?sit=5v5&score=all&rate=y&team=all&loc="
)

USER_AGENT_SHORT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
USER_AGENT_FULL = "User-agent=" + USER_AGENT_SHORT

NEEDED_BOOKIES = ("BetInAsia", "Pinnacle", "1xBet", "bet365", "BetVictor", "Unibet")
MINIMAL_ODDS_TO_GET_LINE = 1.50

XG_SCALE_FACTOR = 0.75
XG_OFFSET = 0.05
