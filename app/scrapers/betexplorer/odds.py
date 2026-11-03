"""Odds parsing and bookmaker-priority selection."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, TypeVar

from lxml import html
from lxml.etree import ParserError
from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal

from app import constants as consts

if TYPE_CHECKING:
    from collections.abc import Iterator

_MONEYLINE_OUTCOME_COUNT = 3
_BOOKMAKER_IDS = frozenset(bookmaker_id for bookmaker_id, _ in consts.BOOKMAKERS)
LineMarket = TypeVar("LineMarket", BookmakerHandicap, BookmakerTotal)


def parse_moneyline(payload: str) -> dict[int, BookmakerMoneyline]:
    """Parse active moneyline odds for bookmakers listed in BOOKMAKERS.

    Args:
        payload: BetExplorer odds endpoint response body.

    Returns:
        Bookmaker IDs mapped to complete three-way moneyline rows.

    Raises:
        ValueError: The payload is empty, is not valid JSON, or has no valid odds HTML.
    """
    tree = _load_odds_tree(payload, "moneyline")
    moneylines: dict[int, BookmakerMoneyline] = {}
    for row in tree.xpath('//tr[@data-bid and @data-originid="1"]'):
        bookmaker_id = _row_bookmaker_id(row)
        if bookmaker_id is None:
            continue
        cells = row.xpath('./td[contains(@class, "table-main__detail-odds")]')
        odds = _active_odds(cells, _MONEYLINE_OUTCOME_COUNT)
        if odds is None:
            continue
        moneylines[bookmaker_id] = BookmakerMoneyline(*odds)
    return moneylines


def parse_handicaps(payload: str) -> dict[int, list[BookmakerHandicap]]:
    """Parse active Asian handicap odds for bookmakers listed in BOOKMAKERS.

    Args:
        payload: BetExplorer odds endpoint response body.

    Returns:
        Bookmaker IDs mapped to whole- and half-goal handicap lines in source order.
        Both odds must meet MINIMAL_ODDS_TO_GET_LINE. Malformed or inactive rows
        are skipped; an empty dictionary means no rows qualify.

    Raises:
        ValueError: The payload is empty, is not valid JSON, or has no valid odds HTML.
    """
    tree = _load_odds_tree(payload, "handicap")
    handicaps_by_bookmaker: dict[int, list[BookmakerHandicap]] = {}
    for bookmaker_id, line, odds in _iter_line_rows(tree):
        handicaps_by_bookmaker.setdefault(bookmaker_id, []).append(BookmakerHandicap(line, *odds))
    return handicaps_by_bookmaker


def parse_totals(payload: str) -> dict[int, list[BookmakerTotal]]:
    """Parse active total odds for bookmakers listed in BOOKMAKERS.

    Args:
        payload: BetExplorer odds endpoint response body.

    Returns:
        Bookmaker IDs mapped to whole- and half-goal total lines in source order.
        Both odds must meet MINIMAL_ODDS_TO_GET_LINE. Malformed or inactive rows
        are skipped; an empty dictionary means no rows qualify.

    Raises:
        ValueError: The payload is empty, is not valid JSON, or has no valid odds HTML.
    """
    tree = _load_odds_tree(payload, "totals")
    totals_by_bookmaker: dict[int, list[BookmakerTotal]] = {}
    for bookmaker_id, line, odds in _iter_line_rows(tree):
        totals_by_bookmaker.setdefault(bookmaker_id, []).append(BookmakerTotal(line, *odds))
    return totals_by_bookmaker


def _load_odds_tree(payload: str, market: str) -> html.HtmlElement:
    """Decode a BetExplorer odds response and return its HTML tree.

    Args:
        payload: BetExplorer odds endpoint response body.
        market: Market label used in validation messages.

    Returns:
        Parsed odds HTML.

    Raises:
        ValueError: The payload is empty, is not valid JSON, or has no valid odds HTML.
    """
    if not payload or not payload.strip():
        raise ValueError(f"BetExplorer returned an empty {market} response.")
    try:
        response = json.loads(payload)
    except ValueError as exc:
        raise ValueError(f"BetExplorer {market} response is not valid JSON.") from exc
    odds_html = response.get("odds") if isinstance(response, dict) else None
    if not isinstance(odds_html, str) or "<" not in odds_html:
        raise ValueError(f"BetExplorer {market} response has no odds HTML.")
    try:
        return html.fromstring(odds_html)
    except ParserError as exc:
        raise ValueError(f"BetExplorer {market} HTML is not valid.") from exc


def _iter_line_rows(tree: html.HtmlElement) -> Iterator[tuple[int, float, list[float]]]:
    """Yield valid handicap or total rows for bookmakers listed in BOOKMAKERS.

    Args:
        tree: Parsed BetExplorer odds HTML.

    Yields:
        Bookmaker ID, line, and the two validated odds values.
    """
    for row in tree.xpath("//tr[@data-bid]"):
        bookmaker_id = _row_bookmaker_id(row)
        if bookmaker_id is None:
            continue
        line_values = row.xpath('./td[contains(@class, "table-main__doubleparameter")]/text()')
        if len(line_values) != 1:
            continue
        try:
            line = float(line_values[0].strip())
        except ValueError:
            continue
        if not math.isfinite(line) or not _is_whole_or_half_line(line):
            continue
        cells = row.xpath('./td[contains(@class, "table-main__detail-odds")]')
        odds = _active_odds(
            cells,
            2,
            minimum=consts.MINIMAL_ODDS_TO_GET_LINE,
        )
        if odds is None:
            continue
        yield bookmaker_id, line, odds


def _row_bookmaker_id(row: html.HtmlElement) -> int | None:
    """Return the row bookmaker ID if it is listed in BOOKMAKERS, or None."""
    try:
        bookmaker_id = int(row.get("data-bid", ""))
    except ValueError:
        return None
    return bookmaker_id if bookmaker_id in _BOOKMAKER_IDS else None


def _active_odds(
    cells: list[html.HtmlElement],
    expected_count: int,
    minimum: float | None = None,
) -> list[float] | None:
    """Return valid active odds, or None if the row cannot be used.

    Args:
        cells: Odds cells from a single bookmaker row.
        expected_count: Required number of odds values.
        minimum: Optional inclusive minimum for every odds value.

    Returns:
        Finite positive odds values, or None for an inactive or malformed row.
    """
    if len(cells) != expected_count:
        return None
    if any("inactive" in (cell.get("class") or "") for cell in cells):
        return None
    values = [(cell.get("data-odd") or "").strip() for cell in cells]
    if not all(values):
        return None
    try:
        odds = [float(value) for value in values]
    except ValueError:
        return None
    if not all(math.isfinite(value) and value > 0 for value in odds):
        return None
    if minimum is not None and any(value < minimum for value in odds):
        return None
    return odds


def _is_whole_or_half_line(line: float) -> bool:
    """Return whether a line uses a whole- or half-goal value."""
    doubled = line * 2
    return math.isclose(doubled, round(doubled), abs_tol=1e-9)


def select_moneyline(
    moneylines: dict[int, BookmakerMoneyline],
) -> BookmakerMoneyline | None:
    """Return the highest-priority available moneyline.

    Args:
        moneylines: Parsed moneyline rows keyed by bookmaker ID.

    Returns:
        The first available moneyline in BOOKMAKERS priority order, or None.
    """
    for bookmaker_id, _ in consts.BOOKMAKERS:
        moneyline = moneylines.get(bookmaker_id)
        if moneyline is not None:
            return moneyline
    return None


def select_lines(markets: dict[int, list[LineMarket]]) -> list[LineMarket]:
    """Select unique lines by bookmaker priority and sort them by line value.

    Args:
        markets: Parsed handicap or total rows keyed by bookmaker ID.

    Returns:
        Lines sorted by value, with duplicates resolved in BOOKMAKERS priority order.
    """
    selected: list[LineMarket] = []
    seen: set[float] = set()
    for bookmaker_id, _ in consts.BOOKMAKERS:
        for market in markets.get(bookmaker_id, []):
            if market.line in seen:
                continue
            seen.add(market.line)
            selected.append(market)
    return sorted(selected, key=lambda market_: market_.line)
