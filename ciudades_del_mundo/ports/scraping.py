"""Protocols and exceptions for scraper implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig


@dataclass(frozen=True)
class ScrapedHtmlPage:
    """Entities plus the raw HTML downloaded for one CityPopulation page."""

    entities: list[ScrapedAdminArea]
    html: str = ""
    url: str = ""


class ScrapingPageNotFoundError(RuntimeError):
    """Raised when a configured CityPopulation page returns HTTP 404."""

    def __init__(self, url: str):
        super().__init__(f"Scraping page not found: {url}")
        self.url = url


class HtmlFetcher(Protocol):
    """Port for downloading raw HTML outside the application use case.

    The application layer may orchestrate prefetching, but the concrete HTTP
    client remains an infrastructure adapter passed by the composition root.
    """

    def get(self, url: str) -> str:
        ...


class HtmlScraper(Protocol):
    """Protocol implemented by each page-layout-specific scraper."""

    html_format: str

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        ...
