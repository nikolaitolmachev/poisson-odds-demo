"""Represent teams and NHL matches and append match reports to a file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal


@dataclass(eq=False)
class Team:
    """A team and its venue-specific expected goals.

    Attributes:
        name: Source team name.
        xgf: Expected goals for; zero when not assigned.
        xga: Expected goals against; zero when not assigned.
    """

    name: str
    xgf: float = 0.0
    xga: float = 0.0

    def __str__(self) -> str:
        """Return the team name, including xG when both values are nonzero."""
        return f"{self.name} ({self.xgf}|{self.xga})" if self.xgf and self.xga else f"{self.name}"


@dataclass(eq=False)
class NHLMatch:
    """An NHL match with bookmaker markets from poisson_odds.

    Attributes:
        date: Source kickoff date and time as a formatted string.
        team_a: Home team.
        team_b: Away team.
        url: BetExplorer match URL.
        bookmaker_moneyline: Three-way odds, or None if no rows were found.
        bookmaker_handicaps: Selected handicap markets, owned by this match.
        bookmaker_totals: Selected total markets, owned by this match.
    """

    date: str
    team_a: Team
    team_b: Team
    url: str
    bookmaker_moneyline: BookmakerMoneyline | None
    bookmaker_handicaps: list[BookmakerHandicap] = field(default_factory=list)
    bookmaker_totals: list[BookmakerTotal] = field(default_factory=list)

    def __str__(self) -> str:
        """Return the match date and teams, ending with a period."""
        return f"{self.date} {self.team_a} - {self.team_b}."

    def save_to_stats(self, message: str, path: Path) -> None:
        """Append the match and report without creating parent directories.

        Args:
            message: Report text, including any existing trailing newline.
            path: Report output path supplied by the caller from Config.stats_file.
        """
        result = f"{self}\n{message}\n"
        with path.open("a", encoding="utf-8") as file:
            file.write(result)
