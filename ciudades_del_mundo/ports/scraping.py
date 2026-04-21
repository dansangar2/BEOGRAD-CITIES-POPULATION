from __future__ import annotations

from typing import Protocol

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig


class HtmlScraper(Protocol):
    html_format: str

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        ...
