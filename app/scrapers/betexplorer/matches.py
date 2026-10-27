"""Fixture URLs and match metadata parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from lxml import html
from lxml.etree import ParserError

from app import constants as consts

if TYPE_CHECKING:
    from typing import TypeGuard

_MONEYLINE_OUTCOME_COUNT = 3


@dataclass(frozen=True)
class MatchMetadata:
    """Match date and team names parsed from a BetExplorer page.

    Attributes:
        date: Match start date and time in YYYY-MM-DD HH:MM format.
        home: Home team name from BetExplorer, with surrounding whitespace removed.
        away: Away team name from BetExplorer, with surrounding whitespace removed.
    """

    date: str
    home: str
    away: str


def parse_match_urls(document: str) -> list[str]:
    """Extract unique NHL match URLs with three listed 1X2 odds values.

    Count nonempty odds attributes without validating their numeric values.

    Args:
        document: BetExplorer NHL fixtures HTML.

    Returns:
        Match URLs in source order without duplicates.

    Raises:
        ValueError: The document is empty, is not valid HTML, does not contain
            exactly one fixtures table, or contains an invalid selected match URL.
    """
    if not document or not document.strip():
        raise ValueError("BetExplorer returned an empty fixtures page.")
    try:
        tree = html.fromstring(document)
    except ParserError as exc:
        raise ValueError("BetExplorer fixtures page is not valid HTML.") from exc

    tables = tree.xpath(
        '//table[contains(concat(" ", normalize-space(@class), " "), '
        '" table-main--leaguefixtures ")]'
    )
    if len(tables) != 1:
        raise ValueError("Expected exactly one BetExplorer NHL fixtures table.")

    urls: list[str] = []
    seen: set[str] = set()
    for row in tables[0].xpath(".//tr"):
        links = row.xpath(
            './td[2]/a[contains(concat(" ", normalize-space(@class), " "), " in-match ")]'
        )
        if not links:
            continue
        odds = [
            button
            for button in row.xpath(
                './/td[contains(@class, "table-main__odds")]//button[@data-odd]'
            )
            if (button.get("data-odd") or "").strip()
        ]
        if len(odds) != _MONEYLINE_OUTCOME_COUNT:
            continue
        href = links[0].get("href")
        if not href:
            raise ValueError("BetExplorer match row has no URL.")
        url = urljoin(consts.BETEXPLORER_BASE + "/", href)
        event_id_from_url(url)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def event_id_from_url(url: str) -> str:
    """Validate a BetExplorer NHL match URL and return its event ID.

    Args:
        url: Candidate absolute match URL.

    Returns:
        Eight-character BetExplorer event ID.

    Raises:
        ValueError: The URL is not an absolute BetExplorer NHL match URL or
            does not end with an eight-character alphanumeric event ID.
    """
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in consts.BETEXPLORER_HOSTNAMES
        or not parsed.path.startswith(consts.NHL_MATCH_PATH_PREFIX)
    ):
        raise ValueError(f"Invalid BetExplorer NHL match URL: {url}")
    event_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if len(event_id) != 8 or not event_id.isalnum():
        raise ValueError(f"Invalid BetExplorer event ID in URL: {url}")
    return event_id


def parse_match_metadata(document: str) -> MatchMetadata:
    """Parse match date and teams from JSON-LD, using HTML for missing values.

    Args:
        document: BetExplorer match page HTML.

    Returns:
        Date, home team, and away team from the match page.

    Raises:
        ValueError: The document is empty, not HTML, or lacks usable metadata.
    """
    if not document or not document.strip():
        raise ValueError("BetExplorer returned an empty match page.")
    try:
        tree = html.fromstring(document)
    except ParserError as exc:
        raise ValueError("BetExplorer match page is not valid HTML.") from exc

    date_value = ""
    home = ""
    away = ""
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            payload = json.loads(script.text_content())
        except (TypeError, ValueError):
            continue
        if not _is_sports_event(payload):
            continue
        date_value = _parse_start_date(payload.get("startDate"))
        home = _nested_text(payload.get("homeTeam"), "name")
        away = _nested_text(payload.get("awayTeam"), "name")
        break

    if not date_value:
        dates = tree.xpath('//*[@id="match-date"]/@data-dt')
        if dates:
            try:
                date_value = datetime.strptime(dates[0].strip(), "%d,%m,%Y,%H,%M").strftime(
                    "%Y-%m-%d %H:%M"
                )
            except ValueError:
                pass

    if not home or not away:
        names = tree.xpath('//span[contains(@class, "list-breadcrumb__item__in")]/text()')
        if names:
            parts = names[0].strip().split(" - ")
            if len(parts) == 2:
                home = home or parts[0].strip()
                away = away or parts[1].strip()

    missing = [
        label
        for label, value in (("date", date_value), ("home team", home), ("away team", away))
        if not value
    ]
    if missing:
        raise ValueError(f"BetExplorer match page is missing: {', '.join(missing)}.")
    return MatchMetadata(date_value, home, away)


def _is_sports_event(payload: object) -> TypeGuard[dict[str, object]]:
    """Return whether a JSON-LD payload describes a sports event."""
    if not isinstance(payload, dict):
        return False
    event_type = payload.get("@type")
    if event_type == "SportsEvent":
        return True
    return isinstance(event_type, list) and "SportsEvent" in event_type


def _parse_start_date(value: object) -> str:
    """Format an ISO 8601 start date in the user's local timezone."""
    if not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _nested_text(payload: object, key: str) -> str:
    """Return a stripped nested string value, or an empty string."""
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""
