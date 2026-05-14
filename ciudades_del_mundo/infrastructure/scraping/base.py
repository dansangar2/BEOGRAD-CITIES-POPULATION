"""Shared base implementation for CityPopulation scrapers."""

from __future__ import annotations

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url


class BaseCityPopulationScraper:
    """Download a configured page and delegate layout parsing to subclasses."""

    html_format: str

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._client = CityPopulationClient(debug=debug)

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        url = build_page_url(base_url, page.path)
        html = self._client.get(url)
        return self.scrape_html(html=html, url=url, country_code=country_code, level=page.lowest_level)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        raise NotImplementedError
