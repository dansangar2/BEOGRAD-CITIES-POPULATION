"""Scraper for CityPopulation country pages centered on city lists."""

from __future__ import annotations

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.double import CityPopulationDoubleScraper
from ciudades_del_mundo.infrastructure.scraping.infosection import CityPopulationInfoSectionScraper


class CityPopulationCitiesScraper(BaseCityPopulationScraper):
    html_format = "cities"

    def __init__(self, debug: bool = False):
        super().__init__(debug=debug)
        self._admin_scraper = CityPopulationAdminScraper(debug=debug)
        self._double_scraper = CityPopulationDoubleScraper(debug=debug)
        self._infosection_scraper = CityPopulationInfoSectionScraper(debug=debug)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._client.parser)
        root = self._admin_scraper._parse_root(soup, country_code=country_code, level=level, url=url)
        if not root:
            roots = self._infosection_scraper.scrape_html(
                html=html,
                url=url,
                country_code=country_code,
                level=level,
            )
            root = roots[0] if roots else None

        return self._double_scraper.parse_hierarchical_tables(
            soup=soup,
            url=url,
            country_code=country_code,
            level=level,
            root=root,
            first_table_offset=1,
        )
