from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import quote_plus, urlsplit, urlunsplit

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    SessionNotCreatedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from .email_finder import EmailFinderConfig, find_emails_for_website
from .models import BusinessRecord, PlaceLink, ScrapedPlace


class GoogleMapsBlockedError(RuntimeError):
    """Raised when Google shows a verification, CAPTCHA, or traffic warning page."""


@dataclass(frozen=True, slots=True)
class ScraperConfig:
    browser: str = "chrome"
    headless: bool = True
    max_results: int | None = None
    scroll_pause: float = 1.5
    details_pause: float = 1.0
    polite_delay_min: float = 0.8
    polite_delay_max: float = 2.2
    wait_seconds: float = 30.0
    with_emails: bool = True
    email_timeout: float = 10.0
    email_pages: int = 5
    max_stagnant_scrolls: int = 8
    profile_dir: str | None = None
    reuse_browser_profile: bool = False


class GoogleMapsScraper:
    def __init__(self, config: ScraperConfig):
        self.config = config
        self.driver = self._build_driver()
        self.wait = WebDriverWait(self.driver, config.wait_seconds)

    def __enter__(self) -> "GoogleMapsScraper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.driver.quit()

    def scrape_query(self, query: str, skip_urls: Iterable[str] | None = None) -> list[BusinessRecord]:
        return [item.record for item in self.iter_query(query, skip_urls=skip_urls)]

    def iter_query(self, query: str, skip_urls: Iterable[str] | None = None) -> Iterator[ScrapedPlace]:
        search_url = f"https://www.google.com/maps/search/{quote_plus(query)}"
        self.driver.get(search_url)
        self._accept_consent_if_present()
        self._raise_if_blocked()
        self._wait_for_maps_content()

        links = self._collect_result_links()
        if not links and self._has_place_header():
            links = [PlaceLink(name="", url=self.driver.current_url, position=1)]

        skip_urls = set(skip_urls or [])
        seen_urls: set[str] = set(skip_urls)

        for index, place in enumerate(links, start=1):
            if place.url in seen_urls:
                continue
            seen_urls.add(place.url)
            print(f"[{index}/{len(links)}] Scraping {place.name or place.url}")
            record = self.scrape_place(place.url, query)
            yield ScrapedPlace(place=place, record=record)

    def scrape_place(self, url: str, query: str) -> BusinessRecord:
        self.driver.get(url)
        self._accept_consent_if_present()
        self._raise_if_blocked()
        self._wait_for_place_page()
        self._polite_pause(self.config.details_pause)

        record = BusinessRecord(
            search_query=query,
            name=self._first_text(["h1.DUwDvf", "h1"]),
            category=self._first_text(['button[jsaction*="pane.rating.category"]', "button.DkEaL"]),
            address=self._place_info_text("address"),
            phone=self._place_info_text("phone:tel"),
            website=self._website_url(),
        )

        if self.config.with_emails and record.website:
            email_config = EmailFinderConfig(timeout=self.config.email_timeout, max_pages=self.config.email_pages)
            record.emails = find_emails_for_website(record.website, email_config)

        self._polite_pause()

        return record

    def _build_driver(self) -> WebDriver:
        workspace = Path.cwd()
        selenium_cache = workspace / ".selenium-cache"
        selenium_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("SE_CACHE_PATH", str(selenium_cache))

        browser = self.config.browser.lower()
        profile_dir = self._profile_dir(browser)

        if browser == "edge":
            options = webdriver.EdgeOptions()
            if self.config.headless:
                options.add_argument("--headless=new")
            self._apply_common_options(options, profile_dir)
            try:
                return webdriver.Edge(options=options)
            except SessionNotCreatedException:
                fallback_options = webdriver.EdgeOptions()
                if self.config.headless:
                    fallback_options.add_argument("--headless=new")
                self._apply_common_options(fallback_options, self._profile_dir(browser, force_fresh=True))
                return webdriver.Edge(options=fallback_options)

        options = webdriver.ChromeOptions()
        if self.config.headless:
            options.add_argument("--headless=new")
        self._apply_common_options(options, profile_dir)
        try:
            return webdriver.Chrome(options=options)
        except SessionNotCreatedException:
            fallback_options = webdriver.ChromeOptions()
            if self.config.headless:
                fallback_options.add_argument("--headless=new")
            self._apply_common_options(fallback_options, self._profile_dir(browser, force_fresh=True))
            return webdriver.Chrome(options=fallback_options)

    def _profile_dir(self, browser: str, force_fresh: bool = False) -> Path:
        if self.config.profile_dir:
            return Path(self.config.profile_dir)

        root = Path.cwd() / ".browser-profile"
        if self.config.reuse_browser_profile and not force_fresh:
            return root / browser

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return root / f"{browser}-{os.getpid()}-{timestamp}"

    @staticmethod
    def _apply_common_options(options, profile_dir: Path) -> None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = Path.cwd() / ".browser-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        options.add_argument("--window-size=1440,1100")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--no-sandbox")
        options.add_argument("--lang=en-US")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument(f"--disk-cache-dir={cache_dir}")

    def _wait_for_maps_content(self) -> None:
        selectors = [
            (By.CSS_SELECTOR, 'a[href*="/maps/place/"]'),
            (By.CSS_SELECTOR, "div[role='feed']"),
            (By.CSS_SELECTOR, "h1"),
        ]
        self._wait_for_any(selectors)

    def _wait_for_place_page(self) -> None:
        self._wait_for_any(
            [
                (By.CSS_SELECTOR, "h1.DUwDvf"),
                (By.CSS_SELECTOR, "h1"),
                (By.CSS_SELECTOR, '[data-item-id="address"]'),
            ]
        )

    def _wait_for_any(self, selectors: list[tuple[str, str]]) -> None:
        end_time = time.time() + self.config.wait_seconds
        last_error: Exception | None = None
        while time.time() < end_time:
            self._raise_if_blocked()
            for by, selector in selectors:
                try:
                    if self.driver.find_elements(by, selector):
                        return
                except WebDriverException as exc:
                    last_error = exc
            time.sleep(0.25)
        raise TimeoutException(str(last_error) if last_error else "Timed out waiting for page content")

    def _accept_consent_if_present(self) -> None:
        labels = (
            "Accept all",
            "I agree",
            "Agree",
            "Accept",
            "Reject all",
        )
        for label in labels:
            try:
                buttons = self.driver.find_elements(
                    By.XPATH,
                    f"//button[.//span[contains(normalize-space(), '{label}')] or contains(normalize-space(), '{label}')]",
                )
            except WebDriverException:
                continue
            for button in buttons:
                try:
                    if button.is_displayed() and button.is_enabled():
                        button.click()
                        time.sleep(1)
                        return
                except WebDriverException:
                    continue

    def _collect_result_links(self) -> list[PlaceLink]:
        links: list[PlaceLink] = []
        seen: set[str] = set()
        stagnant_rounds = 0
        last_scroll_signature = self._scroll_signature()

        while True:
            self._raise_if_blocked()
            before_count = len(seen)
            for link in self._visible_place_links():
                if link.url not in seen:
                    seen.add(link.url)
                    links.append(PlaceLink(name=link.name, url=link.url, position=len(links) + 1))
                    if self.config.max_results and len(links) >= self.config.max_results:
                        return links

            if self._end_of_results_visible():
                return links

            self._scroll_results()
            self._polite_pause(self.config.scroll_pause)

            current_signature = self._scroll_signature()
            no_new_links = len(seen) == before_count
            no_scroll_progress = current_signature == last_scroll_signature
            stagnant_rounds = stagnant_rounds + 1 if no_new_links and no_scroll_progress else 0
            last_scroll_signature = current_signature

            if stagnant_rounds >= self.config.max_stagnant_scrolls and self._click_next_page_if_present():
                stagnant_rounds = 0
                last_scroll_signature = self._scroll_signature()
                continue

            if stagnant_rounds >= self.config.max_stagnant_scrolls:
                return links

    def _visible_place_links(self) -> list[PlaceLink]:
        elements = self.driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc, a[href*="/maps/place/"]')
        links: list[PlaceLink] = []

        for element in elements:
            try:
                href = element.get_attribute("href") or ""
                if "/maps/place/" not in href:
                    continue
                name = element.get_attribute("aria-label") or element.text or ""
                links.append(PlaceLink(name=name.strip(), url=self._clean_url(href)))
            except (StaleElementReferenceException, WebDriverException):
                continue

        return links

    def _scroll_results(self) -> None:
        feed = self._first_element(["div[role='feed']", "div.m6QErb[aria-label]"])
        if feed:
            self.driver.execute_script(
                "arguments[0].scrollBy({top: Math.max(650, arguments[0].clientHeight * 0.85), behavior: 'instant'});",
                feed,
            )
            return
        self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.PAGE_DOWN)

    def _scroll_signature(self) -> tuple[int, int, int]:
        feed = self._first_element(["div[role='feed']", "div.m6QErb[aria-label]"])
        if not feed:
            return (0, 0, 0)
        try:
            scroll_top, scroll_height, child_count = self.driver.execute_script(
                "return [arguments[0].scrollTop, arguments[0].scrollHeight, arguments[0].querySelectorAll('a[href*=\\'/maps/place/\\']').length];",
                feed,
            )
            return (int(scroll_top), int(scroll_height), int(child_count))
        except WebDriverException:
            return (0, 0, 0)

    def _click_next_page_if_present(self) -> bool:
        xpaths = [
            "//button[contains(@aria-label, 'Next page')]",
            "//button[contains(@aria-label, 'Next')]",
            "//a[contains(@aria-label, 'Next page')]",
            "//a[contains(@aria-label, 'Next')]",
        ]
        for xpath in xpaths:
            for element in self.driver.find_elements(By.XPATH, xpath):
                try:
                    if element.is_displayed() and element.is_enabled():
                        element.click()
                        self._polite_pause(2.0)
                        return True
                except WebDriverException:
                    continue
        return False

    def _end_of_results_visible(self) -> bool:
        page_text = self.driver.page_source.lower()
        end_markers = (
            "you've reached the end of the list",
            "you have reached the end of the list",
            "end of the list",
        )
        return any(marker in page_text for marker in end_markers)

    def _raise_if_blocked(self) -> None:
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            page_text = f"{self.driver.title}\n{body_text}".lower()
        except WebDriverException:
            return

        block_markers = (
            "unusual traffic",
            "our systems have detected",
            "not a robot",
            "captcha",
            "verify you",
            "sorry, we can't complete your request",
        )
        if any(marker in page_text for marker in block_markers):
            raise GoogleMapsBlockedError(
                "Google is showing a verification or traffic warning page. "
                "Pause this run, open with --no-headless if human consent is needed, or use the official Places API."
            )

    def _has_place_header(self) -> bool:
        return bool(self.driver.find_elements(By.CSS_SELECTOR, "h1.DUwDvf, h1"))

    def _place_info_text(self, data_item_prefix: str) -> str:
        selectors = [
            f'button[data-item-id^="{data_item_prefix}"]',
            f'a[data-item-id^="{data_item_prefix}"]',
        ]
        element = self._first_element(selectors)
        if not element:
            return ""

        aria = element.get_attribute("aria-label") or ""
        text = element.text or ""
        value = aria or text
        return self._strip_label(value)

    def _website_url(self) -> str:
        element = self._first_element(
            [
                'a[data-item-id="authority"]',
                'a[aria-label^="Website:"]',
                'a[href^="http"]:has(div)',
            ]
        )
        if not element:
            return ""
        href = element.get_attribute("href") or ""
        if "google.com" in href and "url?q=" not in href:
            return ""
        return href.strip()

    def _first_text(self, selectors: list[str]) -> str:
        element = self._first_element(selectors)
        if not element:
            return ""
        try:
            return " ".join(element.text.split())
        except WebDriverException:
            return ""

    def _first_attr(self, selectors: list[str], attr: str) -> str:
        element = self._first_element(selectors)
        if not element:
            return ""
        try:
            return (element.get_attribute(attr) or "").strip()
        except WebDriverException:
            return ""

    def _first_element(self, selectors: list[str]):
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except (NoSuchElementException, WebDriverException):
                continue
            for element in elements:
                try:
                    if element.is_displayed():
                        return element
                except WebDriverException:
                    continue
        return None

    @staticmethod
    def _strip_label(value: str) -> str:
        value = " ".join(value.split())
        return re.sub(r"^(Address|Phone|Website|Located in):\s*", "", value, flags=re.IGNORECASE).strip()

    def _polite_pause(self, base_seconds: float = 0.0) -> None:
        low = min(self.config.polite_delay_min, self.config.polite_delay_max)
        high = max(self.config.polite_delay_min, self.config.polite_delay_max)
        time.sleep(max(0.0, base_seconds) + random.uniform(low, high))

    @staticmethod
    def _clean_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
