from __future__ import annotations

from dataclasses import replace

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.infrastructure.scraping.double import CityPopulationDoubleScraper
from ciudades_del_mundo.infrastructure.scraping.infosection import CityPopulationInfoSectionScraper
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url


class CityPopulationCitiesScraper:
    html_format = "cities"

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._client = CityPopulationClient(debug=debug)
        self._admin_scraper = CityPopulationAdminScraper(debug=debug)
        self._double_scraper = CityPopulationDoubleScraper(debug=debug)
        self._infosection_scraper = CityPopulationInfoSectionScraper(debug=debug)

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        url = build_page_url(base_url, page.path)
        html = self._client.get(url)
        return self.scrape_html(html=html, url=url, country_code=country_code, level=page.lowest_level)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._client.parser)
        base_for_urljoin = self._client.base_for_urljoin(url)
        root = self._admin_scraper._parse_root(soup, country_code=country_code, level=level, url=url)
        if not root:
            roots = self._infosection_scraper.scrape_html(
                html=html,
                url=url,
                country_code=country_code,
                level=level,
            )
            root = roots[0] if roots else None

        entities = [root] if root else []
        parents_by_name: dict[str, ScrapedAdminArea] = {}

        tl = soup.find("table", id="tl")
        if tl:
            for entity in self._double_scraper._parse_table(
                table=tl,
                country_code=country_code,
                level=level + 1,
                base_url=base_for_urljoin,
                parser="tl",
            ):
                entity = replace(entity, parent_code=root.code if root else None)
                entities.append(entity)
                parents_by_name[self._double_scraper._normalize_name(entity.name)] = entity

        ts = soup.find("table", id="ts")
        if ts:
            entities.extend(
                self._double_scraper._parse_table(
                    table=ts,
                    country_code=country_code,
                    level=level + 2,
                    base_url=base_for_urljoin,
                    parser="ts",
                    parents_by_name=parents_by_name,
                )
            )

        return entities
