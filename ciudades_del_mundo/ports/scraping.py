"""Protocols and exceptions for scraper implementations."""

from __future__ import annotations

from typing import Protocol

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig


class ScrapingPageNotFoundError(RuntimeError):
    """Raised when a configured CityPopulation page returns HTTP 404."""

    def __init__(self, url: str):
        super().__init__(f"Scraping page not found: {url}")
        self.url = url


class HtmlScraper(Protocol):
    """Protocol implemented by each page-layout-specific scraper."""

    html_format: str

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        ...
