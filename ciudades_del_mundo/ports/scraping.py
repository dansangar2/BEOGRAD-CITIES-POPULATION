from __future__ import annotations

from typing import Protocol

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig


class ScrapingPageNotFoundError(RuntimeError):
    def __init__(self, url: str):
        super().__init__(f"Scraping page not found: {url}")
        self.url = url


class HtmlScraper(Protocol):
    html_format: str

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        ...
