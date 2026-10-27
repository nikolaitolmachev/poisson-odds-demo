"""Source URLs, request settings, and model constants."""

BETEXPLORER_BASE = "https://www.betexplorer.com"
BETEXPLORER_HOSTNAMES = ("betexplorer.com", "www.betexplorer.com")
NHL_MATCH_PATH_PREFIX = "/hockey/usa/nhl/"
NHL_LEAGUE = BETEXPLORER_BASE + NHL_MATCH_PATH_PREFIX
NHL_FIXTURES = NHL_LEAGUE + "fixtures/"
BETEXPLORER_ODDS_URL = BETEXPLORER_BASE + "/match-odds/{event_id}/0/{market}/odds/?lang=en"

XG_SITE_NHL = (
    "https://www.naturalstattrick.com/teamtable.php?sit=5v5&score=all&rate=y&team=all&loc="
)
XG_REFERER = "https://www.naturalstattrick.com/teamtable.php"

USER_AGENT_SHORT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
BETEXPLORER_HEADERS = {"User-agent": USER_AGENT_SHORT}

MINIMAL_ODDS_TO_GET_LINE = 1.50

# Ordered by selection priority; the first available bookmaker wins.
BOOKMAKERS = (
    (575, "BetInAsia"),
    (417, "1xBet"),
    (1011, "Megapari"),
    (909, "Bets.io"),
)

XG_SCALE_FACTOR = 0.75
XG_OFFSET = 0.05
