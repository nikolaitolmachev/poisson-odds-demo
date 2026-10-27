"""Original strength and odds formulas with separate analysis for each market."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import constants as consts

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TypeVar

    from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal
    from poisson_odds.poisson import Poisson

    from app.config import Config
    from app.models import NHLMatch

    TwoWayMarket = TypeVar("TwoWayMarket", BookmakerHandicap, BookmakerTotal)


def calculate_quality(xgf_team: float, xga_opponent: float) -> float:
    """Calculate the original team-strength formula without changing rounding.

    Args:
        xgf_team: Venue-specific expected goals for the team.
        xga_opponent: Venue-specific expected goals against its opponent.

    Returns:
        The legacy model parameter rounded to three decimal places.

    Notes:
        Inputs are not validated. Positive xG can produce a negative parameter;
        NaN and infinity can propagate. Validity for Poisson remains unchecked.
    """
    quality = (
        (consts.XG_DIGRESSION_PREFIX * xgf_team + consts.XG_DIGRESSION_SUFFIX)
        * (consts.XG_DIGRESSION_PREFIX * xga_opponent + consts.XG_DIGRESSION_SUFFIX)
    ) - 1

    return round(quality, 3)


def calculate_difference_between_odds(user_odds: float, bookmaker_odds: float) -> float:
    """Calculate the original relative odds difference in percent.

    Args:
        user_odds: Decimal odds produced by the model.
        bookmaker_odds: Decimal odds offered by the bookmaker.

    Returns:
        Relative difference rounded to three decimal places.

    Raises:
        ZeroDivisionError: Model odds are zero.

    Notes:
        Odds are not validated. Negative values can yield meaningless results;
        NaN and infinity can propagate. NaN never meets the value threshold.
    """
    return round((bookmaker_odds / user_odds - 1) * 100, 3)


def _announce_value(differences: tuple[float, ...], threshold: int) -> bool:
    """Print the existing warning if any rounded difference meets the threshold."""
    qualifies = any(difference >= threshold for difference in differences)
    if qualifies:
        print("*****There is probably the value!*****")
    return qualifies


def _analyze_moneyline(
    bookmaker: BookmakerMoneyline | None,
    model: BookmakerMoneyline,
    threshold: int,
) -> str:
    """Print moneyline comparisons and return qualifying report text.

    Args:
        bookmaker: Selected three-way odds, or None if unavailable.
        model: Previously retrieved model moneyline.
        threshold: Inclusive threshold for rounded percentage differences.

    Returns:
        The qualifying comparison with its trailing newline, or an empty string.
    """
    print("Looking for a value for moneyline...")
    if bookmaker is None:
        print("There is no odds for moneyline from bookmaker.")
        return ""

    home = calculate_difference_between_odds(model.odds_home, bookmaker.odds_home)
    draw = calculate_difference_between_odds(model.odds_draw, bookmaker.odds_draw)
    away = calculate_difference_between_odds(model.odds_away, bookmaker.odds_away)
    qualifies = _announce_value((home, draw, away), threshold)
    message = f"{bookmaker} || Poisson: {model} \t=> Value = ({home} %) - ({draw} %) - ({away} %)"
    print(message)
    return message + "\n" if qualifies else ""


def _analyze_two_way_lines(
    bookmakers: list[TwoWayMarket],
    model_lines: dict[float, TwoWayMarket],
    threshold: int,
    get_odds: Callable[[TwoWayMarket], tuple[float, float]],
) -> str:
    """Compare two-outcome lines in bookmaker order and collect qualifying reports.

    Args:
        bookmakers: Selected handicap or total lines in their existing order.
        model_lines: Corresponding model markets keyed by line.
        threshold: Inclusive threshold for rounded percentage differences.
        get_odds: Extract both odds in home/away or over/under order.

    Returns:
        Qualifying comparisons with trailing newlines; unavailable lines are skipped.
    """
    messages: list[str] = []
    for bookmaker in bookmakers:
        model = model_lines.get(bookmaker.line)
        if model is None:
            continue
        model_first, model_second = get_odds(model)
        bookmaker_first, bookmaker_second = get_odds(bookmaker)
        first = calculate_difference_between_odds(model_first, bookmaker_first)
        second = calculate_difference_between_odds(model_second, bookmaker_second)
        qualifies = _announce_value((first, second), threshold)
        message = (
            f"{bookmaker} \t|| Poisson: {model_first} "
            f"/ {model_second} \t=> Value = {first} % / {second} %"
        )
        print(message)
        if qualifies:
            messages.append(message + "\n")
    return "".join(messages)


def _analyze_handicaps(
    bookmakers: list[BookmakerHandicap], user_model: Poisson, threshold: int
) -> str:
    """Print handicap comparisons in bookmaker order and collect qualifying lines.

    Args:
        bookmakers: Selected handicap lines in their existing processing order.
        user_model: Model providing the existing handicap-odds mapping.
        threshold: Inclusive threshold for rounded percentage differences.

    Returns:
        Qualifying comparisons with trailing newlines; unavailable lines are skipped.
    """
    print("Looking for a value for handicaps...")
    if not bookmakers:
        print("There are no any handicaps from bookmaker.")
        return ""
    return _analyze_two_way_lines(
        bookmakers,
        user_model.calculate_handicap_odds_by_Poisson(),
        threshold,
        lambda line: (line.odds_home, line.odds_away),
    )


def _analyze_totals(bookmakers: list[BookmakerTotal], user_model: Poisson, threshold: int) -> str:
    """Print total comparisons in bookmaker order and collect qualifying lines.

    Args:
        bookmakers: Selected total lines in their existing processing order.
        user_model: Model providing the existing total-odds mapping.
        threshold: Inclusive threshold for rounded percentage differences.

    Returns:
        Qualifying comparisons with trailing newlines; unavailable lines are skipped.
    """
    print("Looking for a value for totals...")
    if not bookmakers:
        print("There are no any totals from bookmaker.")
        return ""

    return _analyze_two_way_lines(
        bookmakers,
        user_model.calculate_total_odds_by_Poisson(),
        threshold,
        lambda line: (line.odds_over, line.odds_under),
    )


def analyze_match_odds(nhl_match: NHLMatch, model: Poisson, config: Config) -> None:
    """Print comparisons in market order and append qualifying lines to stats.

    Args:
        nhl_match: Match containing the selected bookmaker markets.
        model: Existing poisson_odds model, using the 0.1.3 API.
        config: Validated threshold and explicit output path.
    """
    # Preserve the original property access before any headings, even without odds.
    moneyline = model.moneyline
    threshold = config.model_value_difference
    message = "".join(
        [
            _analyze_moneyline(nhl_match.bookmaker_moneyline, moneyline, threshold),
            _analyze_handicaps(nhl_match.bookmaker_handicaps, model, threshold),
            _analyze_totals(nhl_match.bookmaker_totals, model, threshold),
        ]
    )
    if message:
        nhl_match.save_to_stats(message + "=" * 100, config.stats_file)
