"""Selenium collection using the legacy BetExplorer selectors and behavior."""

from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor

from aiohttp import ClientSession
from poisson_odds import BookmakerHandicap, BookmakerMoneyline, BookmakerTotal
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from app import constants as consts
from app.models import NHLMatch, Team


class Scraper:
    """Collect BetExplorer data without changing the existing scraping rules."""

    @staticmethod
    def __create_driver() -> webdriver.Chrome:
        """Create Chrome with the existing headless options and driver manager.

        Returns:
            A Chrome WebDriver.
        """

        # set settings for a driver
        options = Options()
        options.headless = True
        options.add_argument(consts.USER_AGENT_FULL)
        options.add_argument("window-size=1920,1080")
        options.add_argument("--log-level=3")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        options.add_argument("--headless=new")
        prefs = {"profile.managed_default_content_settings.images": 2}
        options.add_experimental_option("prefs", prefs)

        service = Service(executable_path=ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    @staticmethod
    def get_urls_all_upcoming_matches() -> list[str]:
        """Collect upcoming match URLs with available odds.

        Returns:
            Match URLs in the order provided by BetExplorer.
        """
        urls: list[str] = []

        driver = Scraper.__create_driver()
        driver.get(consts.NHL_LEAGUE)
        driver.implicitly_wait(5)

        table = driver.find_elements(
            By.XPATH, '//table[@class="table-main table-main--leaguefixtures h-mb15"]/tbody/tr'
        )
        table.pop(0)

        for row in table:
            try:
                driver.implicitly_wait(0)
                are_odds = row.find_elements(By.XPATH, './td[@class="table-main__odds"]//button')
                if are_odds:
                    mfl = row.find_element(By.XPATH, './td[2]/a[@class="in-match"]').get_attribute(
                        "href"
                    )
                    urls.append(mfl)
            except IndexError:
                continue

        driver.quit()

        return urls

    @staticmethod
    def get_main_data_for_match(url: str) -> NHLMatch:
        """Collect match details, moneyline, handicaps, and totals.

        Args:
            url: BetExplorer match URL.

        Returns:
            Match with the legacy bookmaker selection and date handling.
        """

        driver = Scraper.__create_driver()
        driver.get(url)
        driver.implicitly_wait(5)

        # get both teams
        match_name = driver.find_element(
            By.XPATH, './/span[@class="list-breadcrumb__item__in"]'
        ).text
        team_a = Team(match_name.split(" - ")[0].strip())
        team_b = Team(match_name.split(" - ")[-1].strip())

        # get a date
        try:
            match_date = driver.find_element(
                By.XPATH, './/p[contains(@class, "list-details__item__date")]'
            ).text
            match_date_day = match_date.split("-")[0].strip()
            match_date_time = match_date.split("-")[1].strip()
            match_date_to_datetime = datetime.datetime(
                int(match_date_day.split(".")[2].strip()),
                int(match_date_day.split(".")[1].strip()),
                int(match_date_day.split(".")[0].strip()),
                int(match_date_time.split(":")[0].strip()),
                int(match_date_time.split(":")[1].strip()),
            ).strftime("%Y-%m-%d %H:%M")
        except IndexError:
            match_date_to_datetime = (
                datetime.datetime.now().replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            )

        # get moneyline
        bookmaker_moneylines: dict[str, BookmakerMoneyline] = {}
        bookmaker_moneyline: BookmakerMoneyline | None = None

        od = driver.find_elements(
            By.XPATH, './/table[@class="table-main sortable h-mb15"]/tbody/tr[@data-originid="1"]'
        )
        for odds_row in od:
            bookmaker = odds_row.find_element(
                By.XPATH, './/td[@class="h-text-left over-s-only"]/a'
            ).text

            home_odds = float(
                odds_row.find_element(By.XPATH, ".//td[last()-2]").get_attribute("data-odd")
            )
            draw_odds = float(
                odds_row.find_element(By.XPATH, ".//td[last()-1]").get_attribute("data-odd")
            )
            away_odds = float(
                odds_row.find_element(By.XPATH, ".//td[last()]").get_attribute("data-odd")
            )
            bookmaker_moneyline = BookmakerMoneyline(home_odds, draw_odds, away_odds)

            bookmaker_moneylines[bookmaker] = bookmaker_moneyline

        for need_book in consts.NEEDED_BOOKIES:
            if bookmaker_moneylines.get(need_book) is not None:
                bookmaker_moneyline = bookmaker_moneylines.get(need_book)
                break

        nhl_match = NHLMatch(match_date_to_datetime, team_a, team_b, url, bookmaker_moneyline)
        driver.quit()

        # get handicaps and totals

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_handi = executor.submit(Scraper.get_bookmaker_handicaps, url)
            future_total = executor.submit(Scraper.get_bookmaker_totals, url)

            nhl_match.bookmaker_handicaps = future_handi.result()
            nhl_match.bookmaker_totals = future_total.result()

        return nhl_match

    @staticmethod
    async def get_bookmaker_totals_async(url: str, session: ClientSession) -> list[BookmakerTotal]:
        """Collect totals using the retained legacy async implementation.

        Args:
            url: BetExplorer match URL.
            session: Unused legacy session argument, retained for compatibility.

        Returns:
            Total markets in source order.
        """

        totals: list[BookmakerTotal] = []
        url += consts.SUFFIX_FOR_TOTALS if url[-1] == "/" else "/" + consts.SUFFIX_FOR_TOTALS

        loop = asyncio.get_running_loop()
        driver = await loop.run_in_executor(None, Scraper.__create_driver)

        await loop.run_in_executor(None, driver.get, url)
        driver.implicitly_wait(2)
        od = await loop.run_in_executor(
            None,
            driver.find_elements,
            By.XPATH,
            './/div[@id="odds-content"]/div[@class="box-overflow"]/div',
        )
        for el in od:
            rows = await loop.run_in_executor(None, el.find_elements, By.XPATH, ".//table/tbody/tr")
            total_line_element = await loop.run_in_executor(
                None, rows[0].find_element, By.XPATH, './/td[@class="table-main__doubleparameter"]'
            )
            total_line_str = total_line_element.text
            total_line = float(total_line_str)
            for row in rows:
                bookmaker_element = await loop.run_in_executor(
                    None, row.find_element, By.XPATH, './/td[@class="h-text-left over-s-only"]/a'
                )
                bookmaker = bookmaker_element.text
                if bookmaker in consts.NEEDED_BOOKIES:
                    odds_over_element = await loop.run_in_executor(
                        None, row.find_element, By.XPATH, ".//td[last()-1]"
                    )
                    odds_over = float(odds_over_element.get_attribute("data-odd"))
                    odds_under_element = await loop.run_in_executor(
                        None, row.find_element, By.XPATH, ".//td[last()]"
                    )
                    odds_under = float(odds_under_element.get_attribute("data-odd"))
                    if (
                        odds_over < consts.MINIMAL_ODDS_TO_GET_LINE
                        or odds_under < consts.MINIMAL_ODDS_TO_GET_LINE
                    ):
                        continue
                    book_total = BookmakerTotal(total_line, odds_over, odds_under)
                    totals.append(book_total)
                    break

        driver.quit()

        return totals

    @staticmethod
    def get_bookmaker_totals(url: str) -> list[BookmakerTotal]:
        """Collect total lines with the existing bookmaker priority.

        Args:
            url: BetExplorer match URL.

        Returns:
            Sorted, unique total lines.
        """

        totals: dict[str, list[BookmakerTotal]] = {}
        list_of_totals: list[BookmakerTotal] = []

        url += consts.SUFFIX_FOR_TOTALS if url[-1] == "/" else "/" + consts.SUFFIX_FOR_TOTALS

        driver = Scraper.__create_driver()
        driver.get(url)
        driver.implicitly_wait(5)

        od = driver.find_elements(
            By.XPATH, './/div[@id="odds-content"]/div[@class="box-overflow"]/div'
        )
        for el in od:
            rows = el.find_elements(By.XPATH, ".//table/tbody/tr")
            total_line_str = (
                rows[0].find_element(By.XPATH, './/td[@class="table-main__doubleparameter"]').text
            )
            try:
                total_line = float(total_line_str)
            except ValueError:
                continue

            for row in rows:
                bookmaker = row.find_element(
                    By.XPATH, './/td[@class="h-text-left over-s-only"]/a'
                ).text
                if bookmaker in consts.NEEDED_BOOKIES:
                    odds_over = float(
                        row.find_element(By.XPATH, ".//td[last()-1]").get_attribute("data-odd")
                    )
                    odds_under = float(
                        row.find_element(By.XPATH, ".//td[last()]").get_attribute("data-odd")
                    )
                    if (
                        odds_over < consts.MINIMAL_ODDS_TO_GET_LINE
                        or odds_under < consts.MINIMAL_ODDS_TO_GET_LINE
                    ):
                        continue
                    book_total = BookmakerTotal(total_line, odds_over, odds_under)

                    if totals.get(bookmaker) is None:
                        totals[bookmaker] = []
                    totals[bookmaker].append(book_total)

        lines: list[float] = []
        for need_book in consts.NEEDED_BOOKIES:
            current_book = totals.get(need_book)
            if current_book is not None:
                for cb in current_book:
                    if cb.line in lines:
                        continue
                    else:
                        list_of_totals.append(cb)
                        lines.append(cb.line)

        driver.quit()

        return sorted(list_of_totals)

    @staticmethod
    async def get_bookmaker_handicaps_async(
        url: str, session: ClientSession
    ) -> list[BookmakerHandicap]:
        """Collect handicaps using the retained legacy async implementation.

        Args:
            url: BetExplorer match URL.
            session: Unused legacy session argument, retained for compatibility.

        Returns:
            Handicap markets in source order.
        """

        handicaps: list[BookmakerHandicap] = []
        url += consts.SUFFIX_FOR_HANDICAPS if url[-1] == "/" else "/" + consts.SUFFIX_FOR_HANDICAPS

        loop = asyncio.get_running_loop()
        driver = await loop.run_in_executor(None, Scraper.__create_driver)

        await loop.run_in_executor(None, driver.get, url)
        driver.implicitly_wait(2)
        od = await loop.run_in_executor(
            None,
            driver.find_elements,
            By.XPATH,
            './/div[@id="odds-content"]/div[@class="box-overflow"]/div',
        )
        for el in od:
            rows = await loop.run_in_executor(None, el.find_elements, By.XPATH, ".//table/tbody/tr")
            handicap_line_element = await loop.run_in_executor(
                None, rows[0].find_element, By.XPATH, './/td[@class="table-main__doubleparameter"]'
            )
            handicap_line_str = handicap_line_element.text
            try:
                handicap_line = float(handicap_line_str)
            except ValueError:
                continue
            for row in rows:
                bookmaker_element = await loop.run_in_executor(
                    None, row.find_element, By.XPATH, './/td[@class="h-text-left over-s-only"]/a'
                )
                bookmaker = bookmaker_element.text
                if bookmaker in consts.NEEDED_BOOKIES:
                    odds_home_element = await loop.run_in_executor(
                        None, row.find_element, By.XPATH, ".//td[last()-1]"
                    )
                    odds_home = float(odds_home_element.get_attribute("data-odd"))
                    odds_away_element = await loop.run_in_executor(
                        None, row.find_element, By.XPATH, ".//td[last()]"
                    )
                    odds_away = float(odds_away_element.get_attribute("data-odd"))
                    if (
                        odds_home < consts.MINIMAL_ODDS_TO_GET_LINE
                        or odds_away < consts.MINIMAL_ODDS_TO_GET_LINE
                    ):
                        continue
                    book_handicap = BookmakerHandicap(handicap_line, odds_home, odds_away)
                    handicaps.append(book_handicap)
                    break

        driver.quit()

        return handicaps

    @staticmethod
    def get_bookmaker_handicaps(url: str) -> list[BookmakerHandicap]:
        """Collect handicap lines with the existing bookmaker priority.

        Args:
            url: BetExplorer match URL.

        Returns:
            Sorted, unique handicap lines.
        """

        handicaps: dict[str, list[BookmakerHandicap]] = {}
        list_of_handicaps: list[BookmakerHandicap] = []

        url += consts.SUFFIX_FOR_HANDICAPS if url[-1] == "/" else "/" + consts.SUFFIX_FOR_HANDICAPS

        driver = Scraper.__create_driver()
        driver.get(url)
        driver.implicitly_wait(5)

        od = driver.find_elements(
            By.XPATH, './/div[@id="odds-content"]/div[@class="box-overflow"]/div'
        )
        for el in od:
            rows = el.find_elements(By.XPATH, ".//table/tbody/tr")
            handicap_line_str = (
                rows[0].find_element(By.XPATH, './/td[@class="table-main__doubleparameter"]').text
            )
            try:
                handicap_line = float(handicap_line_str)
            except ValueError:
                continue

            for row in rows:
                bookmaker = row.find_element(
                    By.XPATH, './/td[@class="h-text-left over-s-only"]/a'
                ).text

                odds_home = float(
                    row.find_element(By.XPATH, ".//td[last()-1]").get_attribute("data-odd")
                )
                odds_away = float(
                    row.find_element(By.XPATH, ".//td[last()]").get_attribute("data-odd")
                )
                if (
                    odds_home < consts.MINIMAL_ODDS_TO_GET_LINE
                    or odds_away < consts.MINIMAL_ODDS_TO_GET_LINE
                ):
                    continue
                book_handicap = BookmakerHandicap(handicap_line, odds_home, odds_away)
                if handicaps.get(bookmaker) is None:
                    handicaps[bookmaker] = []
                handicaps[bookmaker].append(book_handicap)

        lines: list[float] = []
        for need_book in consts.NEEDED_BOOKIES:
            current_book = handicaps.get(need_book)
            if current_book is not None:
                for cb in current_book:
                    if cb.line in lines:
                        continue
                    else:
                        list_of_handicaps.append(cb)
                        lines.append(cb.line)

        driver.quit()

        return sorted(list_of_handicaps)
