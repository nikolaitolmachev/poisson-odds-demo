"""Display upcoming matches and the first match's bookmaker odds."""

from __future__ import annotations

from app.scrapers.betexplorer import BetExplorerScraper

if __name__ == "__main__":
    scraper = BetExplorerScraper()
    match_urls = scraper.get_upcoming_match_urls()
    print(f"{len(match_urls)} matches")
    print(*match_urls, sep="\n")
    if match_urls:
        match = scraper.get_match(match_urls[0])
        print("\nFirst match:")
        print(match)
        print("Moneyline:", match.bookmaker_moneyline)
        print("Handicaps:", [str(line) for line in match.bookmaker_handicaps])
        print("Totals:", [str(line) for line in match.bookmaker_totals])
