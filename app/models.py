"""Typed team and match models preserving the legacy public attributes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal


class Team:
    """A team and its venue-specific expected goals.

    Attributes:
        name: Source team name.
        xGF: Expected goals for; zero when not assigned.
        xGA: Expected goals against; zero when not assigned.
    """

    def __init__(self, name: str) -> None:
        self.__name = name
        self.__xGF = 0.0
        self.__xGA = 0.0

    @property
    def name(self) -> str:
        return self.__name

    @property
    def xGF(self) -> float:
        return self.__xGF

    @xGF.setter
    def xGF(self, xGF: float) -> None:
        self.__xGF = xGF

    @property
    def xGA(self) -> float:
        return self.__xGA

    @xGA.setter
    def xGA(self, xGA: float) -> None:
        self.__xGA = xGA

    def __str__(self) -> str:
        result_with_xG = f"{self.__name} ({self.__xGF}|{self.__xGA})"
        result_without_xG = f"{self.__name}"
        return result_with_xG if self.__xGF and self.__xGA else result_without_xG


class NHLMatch:
    """A match with the original three-way, handicap, and total markets.

    Attributes:
        date: Source kickoff date and time as a formatted string.
        team_a: Home team.
        team_b: Away team.
        url: BetExplorer match URL.
        bookmaker_moneyline: Three-way odds, or None if no rows were found.
        bookmaker_handicaps: Selected handicap markets.
        bookmaker_totals: Selected total markets.
    """

    def __init__(
        self,
        date: str,
        team_a: Team,
        team_b: Team,
        url: str,
        bookmaker_moneyline: BookmakerMoneyline | None,
    ) -> None:
        self.__date = date
        self.__team_a = team_a
        self.__team_b = team_b
        self.__url = url

        self.__bookmaker_moneyline = bookmaker_moneyline  # BookmakerMoneyline
        self.__bookmaker_handicaps: list[BookmakerHandicap] = []
        self.__bookmaker_totals: list[BookmakerTotal] = []

    @property
    def date(self) -> str:
        return self.__date

    @property
    def team_a(self) -> Team:
        return self.__team_a

    @property
    def team_b(self) -> Team:
        return self.__team_b

    @property
    def url(self) -> str:
        return self.__url

    @property
    def bookmaker_moneyline(self) -> BookmakerMoneyline | None:
        return self.__bookmaker_moneyline

    @property
    def bookmaker_handicaps(self) -> list[BookmakerHandicap]:
        return self.__bookmaker_handicaps

    @bookmaker_handicaps.setter
    def bookmaker_handicaps(self, bookmaker_handicaps: list[BookmakerHandicap]) -> None:
        self.__bookmaker_handicaps = bookmaker_handicaps

    @property
    def bookmaker_totals(self) -> list[BookmakerTotal]:
        return self.__bookmaker_totals

    @bookmaker_totals.setter
    def bookmaker_totals(self, bookmaker_totals: list[BookmakerTotal]) -> None:
        self.__bookmaker_totals = bookmaker_totals

    def __str__(self) -> str:
        return "{} {} - {}.".format(self.__date, self.__team_a, self.__team_b)

    def save_to_stats(self, message: str, path: Path) -> None:
        """Append the match and selected markets using the existing text format.

        Args:
            message: Description of markets meeting the value threshold.
            path: Resolved Config.stats_file; its parent directory must already exist.
        """
        result = self.__str__() + "\n" + message + "\n"

        with path.open("a", encoding="utf-8") as f:
            f.write(result)
