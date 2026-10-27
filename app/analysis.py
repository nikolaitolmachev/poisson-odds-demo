"""Legacy strength and value calculations, with explicit configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import constants as consts
from app.config import Config
from app.models import NHLMatch

if TYPE_CHECKING:
    from poisson_odds.poisson import Poisson


def calculate_quality(xgf_team: float, xga_opponent: float) -> float:
    """Calculate the original team-strength formula without changing rounding.

    Args:
        xgf_team: Venue-specific expected goals for the team.
        xga_opponent: Venue-specific expected goals against its opponent.

    Returns:
        The legacy model parameter rounded to three decimal places.
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
    """

    result = round((bookmaker_odds / user_odds - 1) * 100, 3)
    return result


def looking_for_value(nhl_match: NHLMatch, user_model: Poisson, config: Config) -> None:
    """Print market comparisons and append qualifying markets to the stats file.

    Args:
        nhl_match: Match containing the selected bookmaker markets.
        user_model: Existing poisson_odds model, using the 0.1.3 API.
        config: Validated threshold and output path.
    """

    # info about teams quality
    message_for_stat = ""
    was_value_ml, was_value_handi, was_value_total = False, False, False

    # get Poisson lines/odds
    poisson_moneyline = user_model.moneyline

    # find a value for bookmaker moneyline
    print("Looking for a value for moneyline...")
    if nhl_match.bookmaker_moneyline is None:
        print("There is no odds for moneyline from bookmaker.")
    else:
        diff_home_win = calculate_difference_between_odds(
            poisson_moneyline.odds_home, nhl_match.bookmaker_moneyline.odds_home
        )

        diff_draw = calculate_difference_between_odds(
            poisson_moneyline.odds_draw, nhl_match.bookmaker_moneyline.odds_draw
        )

        diff_away_win = calculate_difference_between_odds(
            poisson_moneyline.odds_away, nhl_match.bookmaker_moneyline.odds_away
        )

        if (
            diff_home_win >= config.model_value_difference
            or diff_draw >= config.model_value_difference
            or diff_away_win >= config.model_value_difference
        ):
            was_value_ml = True
            value_message = "*****There is probably the value!*****"
            print(value_message)
        diff_moneyline_str = (
            f"{nhl_match.bookmaker_moneyline} || Poisson: {poisson_moneyline} \t=> "
            f"Value = ({diff_home_win} %) - ({diff_draw} %) - ({diff_away_win} %)"
        )
        print(diff_moneyline_str)
        if was_value_ml:
            message_for_stat += diff_moneyline_str + "\n"

    # find a value for bookmaker handicaps
    print("Looking for a value for handicaps...")
    if not nhl_match.bookmaker_handicaps:
        print("There are no any handicaps from bookmaker.")
    else:
        # get Poisson handicaps
        poisson_handicaps = user_model.calculate_handicap_odds_by_Poisson()
        # get all available bookmaker handicaps and compare each others with corresponding Poisson
        for bookmaker_handicap in nhl_match.bookmaker_handicaps:
            current_poisson_handicap = poisson_handicaps.get(bookmaker_handicap.line)
            if current_poisson_handicap is None:
                continue

            diff_home_odds = calculate_difference_between_odds(
                current_poisson_handicap.odds_home, bookmaker_handicap.odds_home
            )
            diff_away_odds = calculate_difference_between_odds(
                current_poisson_handicap.odds_away, bookmaker_handicap.odds_away
            )

            if (
                diff_home_odds >= config.model_value_difference
                or diff_away_odds >= config.model_value_difference
            ):
                was_value_handi = True
                value_message = "*****There is probably the value!*****"
                print(value_message)
            diff_handicap_str = (
                f"{bookmaker_handicap} \t|| Poisson: {current_poisson_handicap.odds_home} "
                f"/ {current_poisson_handicap.odds_away} \t=> Value = {diff_home_odds} % / "
                f"{diff_away_odds} %"
            )
            print(diff_handicap_str)
            if was_value_handi:
                message_for_stat += diff_handicap_str + "\n"
                was_value_handi = False

    # find a value for bookmaker totals
    print("Looking for a value for totals...")
    if not nhl_match.bookmaker_totals:
        print("There are no any totals from bookmaker.")
    else:
        # get Poisson totals
        poisson_totals = user_model.calculate_total_odds_by_Poisson()
        # get all available bookmaker totals and compare each others with corresponding Poisson
        for bookmaker_total in nhl_match.bookmaker_totals:
            current_poisson_total = poisson_totals.get(bookmaker_total.line)
            if current_poisson_total is None:
                continue

            diff_over_odds = calculate_difference_between_odds(
                current_poisson_total.odds_over, bookmaker_total.odds_over
            )
            diff_under_odds = calculate_difference_between_odds(
                current_poisson_total.odds_under, bookmaker_total.odds_under
            )
            if (
                diff_over_odds >= config.model_value_difference
                or diff_under_odds >= config.model_value_difference
            ):
                was_value_total = True
                value_message = "*****There is probably the value!*****"
                print(value_message)
            diff_total_str = (
                f"{bookmaker_total} \t|| Poisson: {current_poisson_total.odds_over} "
                f"/ {current_poisson_total.odds_under} \t=> Value = {diff_over_odds} % / "
                f"{diff_under_odds} %"
            )
            print(diff_total_str)
            if was_value_total:
                message_for_stat += diff_total_str + "\n"
                was_value_total = False

    # write to stats
    if message_for_stat:
        splitting = "=" * 100
        nhl_match.save_to_stats(message_for_stat + splitting, config.stats_file)
