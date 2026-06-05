"""Shared base implementation for CityPopulation scrapers."""

from __future__ import annotations

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.infrastructure.scraping.page_types import (
    CityPopulationPageProfile,
    detect_citypopulation_page_profile,
)
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url
from ciudades_del_mundo.ports import ScrapedHtmlPage


class BaseCityPopulationScraper:
    """Download a configured page and delegate layout parsing to subclasses."""

    html_format: str

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._client = CityPopulationClient(debug=debug)

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        return self.scrape_page(base_url, country_code, page).entities

    def scrape_page(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> ScrapedHtmlPage:
        url = build_page_url(base_url, page.path)
        html = self._client.get(url)
        entities = self.scrape_html(html=html, url=url, country_code=country_code, level=page.lowest_level)
        return ScrapedHtmlPage(entities=entities, html=html, url=url)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        raise NotImplementedError

    def _soup_and_profile(self, html: str) -> tuple[BeautifulSoup, CityPopulationPageProfile]:
        soup = BeautifulSoup(html, self._client.parser)
        return soup, detect_citypopulation_page_profile(soup)
